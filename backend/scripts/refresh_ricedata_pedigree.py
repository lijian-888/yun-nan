"""Refresh ONE variety from its own expanded RiceData page; retain old snapshots.

Default is read-only. --apply appends a checked snapshot using MIGRATION_DATABASE_URL.
The displayed label is authoritative; postback value 0 is never a name/variety ID.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text


def parse_tree(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    tree = soup.find(id="pedigree")
    if tree is None:
        raise ValueError("Source tree missing")
    records, stack, pending = [], {}, []
    for table in tree.find_all("table", recursive=False):
        row = table.find("tr")
        cells = row.find_all("td", recursive=False)
        anchor = cells[-1].find("a", id=re.compile(r"^pedigreet\d+$"))
        if anchor is None:
            raise ValueError("Unrecognized source tree row")
        name = anchor.get_text(" ", strip=True)
        if not name:
            raise ValueError("Empty source label")
        depth = len(cells)-2
        if depth < 0 or (depth and depth-1 not in stack):
            raise ValueError("Source hierarchy incomplete")
        parent = stack.get(depth-1) if depth else None
        toggle = cells[-2].find("a")
        expandable = bool(toggle and toggle.find("img"))
        if expandable and toggle.find("img").get("alt", "").startswith("展开"):
            match = re.search(r"__doPostBack\('pedigree','([^']+)'\)", toggle.get("href", ""))
            if not match:
                raise ValueError("Expansion argument missing")
            pending.append(match[1].replace("\\\\", "\\"))
        href = anchor.get("href", "")
        linked = re.search(r"^varis/([1-9]\d*)\.htm", href)
        records.append(dict(name=name, depth=depth, parent=parent,
                            source_id=linked.group(1) if linked else None,
                            url=urljoin(source_url, href) if linked else source_url,
                            expandable=expandable))
        stack[depth] = len(records)-1
        stack = {d: i for d, i in stack.items() if d <= depth}
    if not records or sum(n["depth"] == 0 for n in records) != 1:
        raise ValueError("Source root ambiguous")
    return records, pending


def collect(source_id, limit=150):
    url = f"https://www.ricedata.cn/variety/pedigree.aspx?id={source_id}&fromid="
    with requests.Session() as client:
        response = client.get(url, timeout=20)
        response.raise_for_status()
        response.encoding = "utf-8"
        html = response.text
        expanded = set()
        for _ in range(limit+1):
            records, pending = parse_tree(html, url)
            if not pending:
                return url, records, html
            argument = pending[0]
            if argument in expanded or len(expanded) >= limit or len(records) > 1500:
                raise ValueError("Expansion incomplete or exceeds bound; no import")
            soup = BeautifulSoup(html, "html.parser")
            fields = {n["name"]: n.get("value", "") for n in soup.select("input[name]")}
            fields.update(__EVENTTARGET="pedigree", __EVENTARGUMENT=argument)
            response = client.post(url, data=fields, timeout=20)
            response.raise_for_status()
            response.encoding = "utf-8"
            html = response.text
            expanded.add(argument)


def snapshot_plan(records):
    children = {index: [] for index in range(len(records))}
    for index,node in enumerate(records):
        if node["parent"] is not None:
            children[node["parent"]].append(index)
    signatures, canonical, memo = {}, {}, {}
    for index in range(len(records)-1,-1,-1):
        node = records[index]
        signature = (node["source_id"],node["name"],tuple(signatures[c] for c in children[index]))
        signatures[index] = signature
    # Share only positive source links with matching displayed labels AND
    # identical expanded branches. Zero-valued/text nodes stay contextual.
    for index,node in enumerate(records):
        parent = node["parent"]
        if parent is not None and canonical[parent] != parent:
            position = children[parent].index(index)
            canonical[index] = canonical[children[canonical[parent]][position]]
            continue
        identity = signatures[index] if node["source_id"] else ("occurrence",index)
        canonical[index] = memo.setdefault(identity,index)
    edges = {}
    for index,node in enumerate(records):
        if node["parent"] is not None:
            child,parent = canonical[node["parent"]],canonical[index]
            edges.setdefault((child,parent),children[node["parent"]].index(index))
    return canonical,edges


def save_snapshot(connection, variety_id, url, records):
    root = connection.execute(text("SELECT variety_name FROM ricedata.rice_variety WHERE variety_id=:id"),
                              {"id": variety_id}).scalar_one()
    if records[0]["name"] != root:
        raise ValueError("Source root differs from selected variety")
    canonical,edges = snapshot_plan(records)
    snapshot = connection.execute(text("""
        INSERT INTO ricedata.rice_pedigree_snapshot
        (variety_id,source_url,fully_expanded,node_count,edge_count,max_depth)
        VALUES (:id,:url,true,:nodes,:edges,:depth) RETURNING pedigree_snapshot_id
    """), dict(id=variety_id, url=url, nodes=len(set(canonical.values())), edges=len(edges),
               depth=max(n["depth"] for n in records))).scalar_one()
    ids = {}
    for index, node in enumerate(records):
        if canonical[index] != index:
            continue
        linked = None
        if node["source_id"]:
            linked = connection.execute(text("""
                SELECT variety_id FROM ricedata.rice_variety
                WHERE source_variety_id=:source AND variety_name=:name LIMIT 1
            """), dict(source=node["source_id"], name=node["name"])).scalar()
        # Occurrence identities preserve branch context, including repeated zero values.
        identity = "page:" + hashlib.sha256(f"{url}|{index}|{node['name']}".encode()).hexdigest()[:40]
        node_id = connection.execute(text("""
            INSERT INTO ricedata.rice_pedigree_node
            (pedigree_snapshot_id,source_node_key,material_name,normalized_name,node_type,
             linked_variety_id,generation_depth,is_root,was_expandable,expansion_succeeded,source_href)
            VALUES (:snapshot,:key,:name,:name,:kind,:linked,:depth,:root,:expandable,true,:url)
            RETURNING pedigree_node_id
        """), dict(snapshot=snapshot,key=identity,name=node["name"],
                   kind="variety" if node["source_id"] else "cross" if "/" in node["name"] else "text",
                   linked=linked,depth=node["depth"],root=index==0,expandable=node["expandable"],url=node["url"])).scalar_one()
        ids[index] = node_id
    for (child,parent),position in edges.items():
        connection.execute(text("""
                INSERT INTO ricedata.rice_pedigree_edge
                (pedigree_snapshot_id,child_node_id,parent_node_id,parent_role,display_order)
                VALUES (:snapshot,:child,:parent,'parent',:position)
            """), dict(snapshot=snapshot,child=ids[child],parent=ids[parent],position=position))
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variety-id", type=int, required=True)
    parser.add_argument("--source-id", type=int, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    url, records, html = collect(args.source_id)
    args.evidence_dir.mkdir(parents=True, exist_ok=False)
    (args.evidence_dir/"source.html").write_text(html, encoding="utf-8")
    (args.evidence_dir/"nodes.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(root=records[0]["name"],nodes=len(records),url=url),ensure_ascii=False),flush=True)
    if args.apply:
        engine = create_engine(os.environ["MIGRATION_DATABASE_URL"])
        with engine.begin() as connection:
            snapshot = save_snapshot(connection,args.variety_id,url,records)
        print(f"Appended snapshot {snapshot}; previous snapshots retained")


if __name__ == "__main__":
    main()

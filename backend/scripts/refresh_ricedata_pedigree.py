"""Refresh ONE variety from its own expanded RiceData page; retain old snapshots.

Default is read-only. --apply appends a checked snapshot using MIGRATION_DATABASE_URL.
The displayed label is authoritative; postback value 0 is never a name/variety ID.
"""
import argparse
import hashlib
import json
import os
import re
import time
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
        if expandable and not toggle.find("img").get("alt","").startswith(("展开","折叠")):
            raise ValueError("Unknown source expansion state")
        if expandable and toggle.find("img").get("alt", "").startswith("展开"):
            match = re.search(r"__doPostBack\('pedigree','([^']+)'\)", toggle.get("href", ""))
            if not match:
                raise ValueError("Expansion argument missing")
            if not match[1].startswith("t"):
                raise ValueError("Unexpected source expansion action")
            pending.append(match[1].replace("\\\\", "\\"))
        href = anchor.get("href", "")
        linked = re.search(r"^varis/([1-9]\d*)\.htm", href)
        records.append(dict(name=name, depth=depth, parent=parent,
                            source_id=linked.group(1) if linked else None,
                            url=urljoin(source_url, href) if linked else source_url,
                            expandable=expandable,
                            pending_expansion=bool(expandable and toggle.find("img").get("alt", "").startswith("展开"))))
        stack[depth] = len(records)-1
        stack = {d: i for d, i in stack.items() if d <= depth}
    if not records or sum(n["depth"] == 0 for n in records) != 1:
        raise ValueError("Source root ambiguous")
    return records, pending


def decode_response(response):
    response.raise_for_status()
    try:
        return response.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return response.content.decode("gb18030")


def occurrence_paths(records):
    paths, result = {}, {}
    for index,node in enumerate(records):
        path=(paths[node["parent"]] if node["parent"] is not None else ())+(node["name"],)
        paths[index]=path
        result[path]=result.get(path,0)+1
    return result


def collect(source_id, limit=10000, delay=.5):
    url = f"https://www.ricedata.cn/variety/pedigree.aspx?id={source_id}&fromid="
    with requests.Session() as client:
        for attempt in range(3):
            time.sleep(delay+attempt)
            response=None
            try:
                response=client.get(url,timeout=30)
                response.raise_for_status()
                break
            except requests.RequestException:
                if response is not None and response.status_code in (403,429):
                    raise
                if attempt==2:
                    raise
        response.raise_for_status()
        html = decode_response(response)
        expanded = set()
        for _ in range(limit+1):
            records, pending = parse_tree(html, url)
            if not pending:
                return url, records, html
            argument = pending[0]
            if argument in expanded or len(expanded) >= limit or len(records) > 20000:
                raise ValueError("Expansion incomplete or exceeds bound; no import")
            previous=occurrence_paths(records)
            soup = BeautifulSoup(html, "html.parser")
            fields = {n["name"]: n.get("value", "") for n in soup.select("input[name]")}
            fields.update(__EVENTTARGET="pedigree", __EVENTARGUMENT=argument)
            for attempt in range(3):
                time.sleep(delay+attempt)
                response=None
                try:
                    response = client.post(url, data=fields, timeout=30)
                    html = decode_response(response)
                    break
                except (requests.RequestException,UnicodeError):
                    if response is not None and response.status_code in (403,429):
                        raise
                    if attempt == 2:
                        raise
            next_records,_=parse_tree(html,url)
            current=occurrence_paths(next_records)
            if any(current.get(path,0)<count for path,count in previous.items()):
                raise ValueError("Expansion lost an existing branch; no import")
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
    if not records or any("pending_expansion" not in n or n["pending_expansion"] for n in records):
        raise ValueError("No proof that every source expansion completed")
    source = connection.execute(text("SELECT source_variety_id FROM ricedata.rice_variety WHERE variety_id=:id"),
                                {"id": variety_id}).scalar_one()
    if str(records[0]["source_id"]) != str(source):
        raise ValueError("Source root identity differs from selected variety")
    canonical,edges = snapshot_plan(records)
    digest=hashlib.sha256(json.dumps(records,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    snapshot = connection.execute(text("""
        INSERT INTO ricedata.rice_pedigree_snapshot
        (variety_id,source_url,fully_expanded,node_count,edge_count,max_depth,source_verified,source_evidence_sha256)
        VALUES (:id,:url,true,:nodes,:edges,:depth,true,:digest) RETURNING pedigree_snapshot_id
    """), dict(id=variety_id, url=url, nodes=len(set(canonical.values())), edges=len(edges),
               depth=max(n["depth"] for n in records),digest=digest)).scalar_one()
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
    counts=connection.execute(text("""SELECT
        (SELECT count(*) FROM ricedata.rice_pedigree_node WHERE pedigree_snapshot_id=:id) AS nodes,
        (SELECT count(*) FROM ricedata.rice_pedigree_edge WHERE pedigree_snapshot_id=:id) AS edges
    """),{"id":snapshot}).mappings().one()
    if counts["nodes"] != len(ids) or counts["edges"] != len(edges):
        raise ValueError("Saved graph differs from checked source plan")
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variety-id", type=int, required=True)
    parser.add_argument("--source-id", type=int, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-expansions",type=int,default=10000)
    args = parser.parse_args()
    url, records, html = collect(args.source_id,limit=args.max_expansions)
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

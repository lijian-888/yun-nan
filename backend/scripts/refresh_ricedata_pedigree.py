"""Refresh ONE variety from its own expanded RiceData page; retain old snapshots.

Default is read-only. --apply appends a checked snapshot using MIGRATION_DATABASE_URL.
The displayed label is authoritative; postback value 0 is never a name/variety ID.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

try:
    from lxml import etree
except ImportError:
    etree=None


HTML_PARSER="lxml" if importlib.util.find_spec("lxml") else "html.parser"


def source_document(html):
    return BeautifulSoup(html, HTML_PARSER)


def parse_tree(html, source_url, document=None):
    soup = document if document is not None else source_document(html)
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


def parse_source(html,source_url,mode='auto'):
    """Return one document's tree and POST state; keep the reference for differential verification."""
    if mode not in ('auto','direct','reference'):
        raise ValueError('Unknown parser mode')
    if mode=='reference' or (mode=='auto' and etree is None):
        document=source_document(html)
        records,pending=parse_tree(html,source_url,document)
        return records,pending,{n['name']:n.get('value','') for n in document.select('input[name]')}
    if etree is None:
        raise RuntimeError('Direct parser requires lxml')
    document=etree.HTML(html,parser=etree.HTMLParser(no_network=True,huge_tree=True))
    trees=document.xpath('//*[@id="pedigree"]') if document is not None else []
    if not trees:
        raise ValueError('Source tree missing')
    records,stack,pending=[],{},[]

    def label_text(element):
        # Match BeautifulSoup get_text(' ',strip=True), excluding comments/scripts/styles.
        parts=[]
        def visit(node):
            if node.text and node.text.strip():
                parts.append(node.text.strip())
            for child in node:
                if isinstance(child.tag,str) and child.tag not in ('script','style'):
                    visit(child)
                if child.tail and child.tail.strip():
                    parts.append(child.tail.strip())
        visit(element)
        return ' '.join(parts)

    for table in trees[0].findall('table'):
        row=table.find('.//tr')
        cells=row.findall('td') if row is not None else []
        if len(cells)<2:
            raise ValueError('Unrecognized source tree row')
        anchor=next((a for a in cells[-1].iter('a') if re.fullmatch(r'pedigreet\d+',a.get('id',''))),None)
        if anchor is None:
            raise ValueError('Unrecognized source tree row')
        name=label_text(anchor)
        if not name:
            raise ValueError('Empty source label')
        depth=len(cells)-2
        if depth and depth-1 not in stack:
            raise ValueError('Source hierarchy incomplete')
        parent=stack.get(depth-1) if depth else None
        toggle=next(cells[-2].iter('a'),None)
        image=next(toggle.iter('img'),None) if toggle is not None else None
        expandable=image is not None
        alt=image.get('alt','') if image is not None else ''
        if expandable and not alt.startswith(('展开','折叠')):
            raise ValueError('Unknown source expansion state')
        is_pending=expandable and alt.startswith('展开')
        if is_pending:
            match=re.search(r"__doPostBack\('pedigree','([^']+)'\)",toggle.get('href',''))
            if not match:
                raise ValueError('Expansion argument missing')
            if not match[1].startswith('t'):
                raise ValueError('Unexpected source expansion action')
            pending.append(match[1].replace('\\\\','\\'))
        linked=re.search(r'^varis/([1-9]\d*)\.htm',anchor.get('href',''))
        records.append(dict(name=name,depth=depth,parent=parent,source_id=linked.group(1) if linked else None,
                            url=urljoin(source_url,anchor.get('href','')) if linked else source_url,
                            expandable=expandable,pending_expansion=is_pending))
        stack[depth]=len(records)-1
        stack={d:i for d,i in stack.items() if d<=depth}
    if not records or sum(n['depth']==0 for n in records)!=1:
        raise ValueError('Source root ambiguous')
    fields={n.get('name'):n.get('value','') for n in document.iter('input') if 'name' in n.attrib}
    return records,pending,fields


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


class CollectionDeferred(Exception):
    """Saved partial source state, never a publishable snapshot."""
    def __init__(self,state):
        super().__init__('Source collection yielded; resume exact page state')
        self.state=state


def collect(source_id, limit=10000, delay=.5, *, resume=None, on_progress=None,
            slice_seconds=None, slice_expansions=None, before_request=None, direct_only=False):
    url = f"https://www.ricedata.cn/variety/pedigree.aspx?id={source_id}&fromid="
    started=time.monotonic()
    if resume and resume.get('url')!=url:
        raise ValueError('Checkpoint belongs to another source')
    expanded=set(resume.get('expanded',[])) if resume else set()
    initial_expansions=len(expanded)
    network_seconds=resume.get('network_seconds',0) if resume else 0
    parse_seconds=resume.get('parse_seconds',0) if resume else 0
    previous=None
    with requests.Session() as client:
        def request(method,**kwargs):
            nonlocal network_seconds
            for attempt in range(3):
                time.sleep(delay+attempt)
                if before_request:
                    before_request()
                response=None
                clock=time.monotonic()
                try:
                    response=getattr(client,method)(url,timeout=30,**kwargs)
                    return decode_response(response)
                except (requests.RequestException,UnicodeError):
                    if response is not None and response.status_code in (403,429):
                        raise
                    if attempt==2:
                        raise
                finally:
                    network_seconds+=time.monotonic()-clock
        if resume:
            client.cookies.update(resume.get('cookies',{}))
            html=resume['html']
        else:
            html=request('get')
        while True:
            clock=time.monotonic()
            records,pending,fields=parse_source(html,url)
            paths=occurrence_paths(records)
            if previous is not None and any(paths.get(path,0)<count for path,count in previous.items()):
                raise ValueError('Expansion lost an existing branch; no import')
            parse_seconds+=time.monotonic()-clock
            if len(records)>20000:
                raise ValueError('Source exceeds occurrence bound; no import')
            state=dict(url=url,html=html,expanded=sorted(expanded),cookies=client.cookies.get_dict(),
                       network_seconds=network_seconds,parse_seconds=parse_seconds)
            eligible=list(pending)
            if direct_only:
                eligible=[]
                for index,argument in zip((i for i,n in enumerate(records) if n['pending_expansion']),pending):
                    if index==0:
                        eligible.append(argument);continue
                    if records[index]['source_id']:continue
                    ancestor=records[index]['parent'];inside=True
                    while ancestor is not None and ancestor!=0:
                        if records[ancestor]['source_id']:inside=False;break
                        ancestor=records[ancestor]['parent']
                    if inside:eligible.append(argument)
            metric=dict(expansions=len(expanded),source_occurrences=len(records),pending_branches=len(pending),
                        page_bytes=len(html.encode()),network_seconds=round(network_seconds,3),
                        parse_seconds=round(parse_seconds,3),slice_seconds=round(time.monotonic()-started,3),
                        next_branch=pending[0] if pending else None)
            if direct_only:metric['pending_direct_branches']=len(eligible)
            if on_progress:
                on_progress(state,metric)
            if not eligible:
                return url, records, html
            if ((slice_seconds is not None and time.monotonic()-started>=slice_seconds) or
                (slice_expansions is not None and len(expanded)-initial_expansions>=slice_expansions)):
                raise CollectionDeferred(state)
            argument = eligible[0]
            if argument in expanded or len(expanded) >= limit:
                raise ValueError("Expansion incomplete or exceeds bound; no import")
            previous=paths
            fields.update(__EVENTTARGET="pedigree", __EVENTARGUMENT=argument)
            html=request('post',data=fields)
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
        candidate=memo.setdefault(identity,index)
        if parent is not None and any(canonical[sibling]==candidate for sibling in children[parent] if sibling<index):
            # Repeated parents (for example a self-cross) occupy separate source
            # positions; a unique DB edge must not erase the second occurrence.
            candidate=index
        canonical[index]=candidate
    edges = {}
    for index,node in enumerate(records):
        if node["parent"] is not None:
            child,parent = canonical[node["parent"]],canonical[index]
            edges.setdefault((child,parent),children[node["parent"]].index(index))
    return canonical,edges


def validate_plan(records,canonical,edges):
    expected=occurrence_paths(records)
    adjacency={}
    for child,parent in edges:
        adjacency.setdefault(child,[]).append(parent)
    pending=[(canonical[0],(),())]
    actual={};visits=0
    while pending:
        node,seen,path=pending.pop()
        if node in seen:
            raise ValueError("Conversion introduced a cycle")
        visits+=1
        if visits>len(records):
            raise ValueError("Conversion introduced extra source occurrences")
        full=path+(records[node]["name"],)
        actual[full]=actual.get(full,0)+1
        for parent in adjacency.get(node,[]):
            pending.append((parent,seen+(node,),full))
    if actual!=expected:
        raise ValueError("Converted relationships differ from expanded source")


def save_snapshot(connection, variety_id, url, records):
    if not records or any("pending_expansion" not in n or n["pending_expansion"] for n in records):
        raise ValueError("No proof that every source expansion completed")
    source = connection.execute(text("SELECT source_variety_id FROM ricedata.rice_variety WHERE variety_id=:id"),
                                {"id": variety_id}).scalar_one()
    if str(records[0]["source_id"]) != str(source):
        raise ValueError("Source root identity differs from selected variety")
    canonical,edges = snapshot_plan(records)
    validate_plan(records,canonical,edges)
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

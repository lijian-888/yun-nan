"""Read-only real-data check: directory and graph must count the same ancestry.

Run with the existing API role. An optional candidate module path allows testing
the patch without replacing or reloading the running application.
"""
import importlib.util
import json
import os
from time import monotonic

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app import pedigree_graph as module


if os.environ.get('PEDIGREE_GRAPH_CANDIDATE'):
    spec = importlib.util.spec_from_file_location('app.pedigree_graph_candidate',os.environ['PEDIGREE_GRAPH_CANDIDATE'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

with Session(create_engine(os.environ['DATABASE_URL'])) as session:
    session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    for keyword in ['138B','00122－40－315','云粳39号','荃广优8238']:
        start=monotonic()
        result=module.list_varieties(session,keyword=keyword)
        record=next(r for r in result['records'] if r['name']==keyword)
        graph=module.pedigree_graph(session,record['id'])
        print(json.dumps(dict(name=keyword,list_nodes=record['node_count'],graph_nodes=graph['node_count'],
                              list_edges=record['edge_count'],graph_edges=graph['edge_count'],
                              seconds=round(monotonic()-start,3)),ensure_ascii=False),flush=True)
        assert (record['node_count'],record['edge_count'])==(graph['node_count'],graph['edge_count'])
    start=monotonic()
    page=module.list_varieties(session,page_size=30)
    page_seconds=round(monotonic()-start,3)
    for record in page['records']:
        graph=module.pedigree_graph(session,record['id'])
        assert (record['node_count'],record['edge_count'])==(graph['node_count'],graph['edge_count']),record['name']
    print(json.dumps(dict(page_records=len(page['records']),page_seconds=page_seconds,status='passed')),flush=True)
    session.rollback()

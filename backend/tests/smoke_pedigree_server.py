"""Read-only probe, using the server API role; no data changes/model calls."""
import json
import os
from time import monotonic
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.pedigree_graph import catalog,list_varieties,pedigree_graph,create_pedigree_router

engine=create_engine(os.environ['DATABASE_URL'])
with Session(engine) as session:
    start=monotonic();summary=catalog(session)
    print(json.dumps({'catalog':summary,'seconds':round(monotonic()-start,3)},ensure_ascii=False))
    assert summary['total']>0 and summary['with_pedigree']>0
    assert sum(g['count'] for g in summary['groups'])==summary['total']
    for keyword in ['田两优9号','D优130','南粳9212','宁9212','不存在品种XYZ999']:
        start=monotonic();result=list_varieties(session,keyword)
        print(json.dumps({'keyword':keyword,'count':result['total'],'seconds':round(monotonic()-start,3)},ensure_ascii=False))
        if keyword.startswith('不存在'):assert result['total']==0;continue
        assert result['total']>0
        graph=pedigree_graph(session,result['records'][0]['id'])
        assert graph and graph['root_ids']
        assert graph['node_count']==len(graph['nodes']) and graph['edge_count']==len(graph['edges'])
        assert len({n['id'] for n in graph['nodes']})==graph['node_count']
        print(json.dumps({'name':graph['variety']['name'],'nodes':graph['node_count'],'edges':graph['edge_count'],'warnings':graph['warnings']},ensure_ascii=False))
    largest=session.scalar(text("SELECT variety_id FROM agent_query.ricedata__rice_pedigree_snapshot ORDER BY node_count DESC LIMIT 1"))
    start=monotonic();graph=pedigree_graph(session,largest)
    assert graph['node_count']==graph['snapshot']['node_count']
    print(json.dumps({'largest':largest,'nodes':graph['node_count'],'edges':graph['edge_count'],'seconds':round(monotonic()-start,3)},ensure_ascii=False))
    page=list_varieties(session,page=2)
    assert page['page']==2 and page['records']
    missing=session.scalar(text("SELECT variety_id FROM agent_query.ricedata__rice_pedigree_snapshot WHERE edge_count=0 LIMIT 1"))
    if missing:
        empty=pedigree_graph(session,missing)
        assert empty['edge_count']==0 and empty['warnings']
    app=FastAPI();app.include_router(create_pedigree_router(lambda:None,lambda:session));client=TestClient(app)
    for url in ['/catalog','/varieties?group=indica&only_pedigree=true','/varieties/'+str(largest)+'/graph']:
        response=client.get('/api/research/pedigree'+url)
        assert response.status_code==200
    session.rollback()
print('Pedigree real-data and JSON probes: PASS')

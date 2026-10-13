import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from app.pedigree_graph import create_pedigree_router, serialize_graph, public_url


class PedigreeTests(unittest.TestCase):
    def client(self, denied=False):
        def user():
            if denied: raise HTTPException(403,"无权限")
        class Session:
            def rollback(self): pass
        app=FastAPI();app.include_router(create_pedigree_router(user,lambda:Session()))
        return TestClient(app)

    def test_no_name_merging_and_no_dangling_edges(self):
        nodes=[dict(id=1,name="根",is_root=True,source_url=None),dict(id=2,name="同名",is_root=False,source_url=None),dict(id=3,name="同名",is_root=False,source_url=None)]
        edges=[dict(id=1,child_id=1,parent_id=2),dict(id=2,child_id=1,parent_id=3),dict(id=3,child_id=1,parent_id=99)]
        graph=serialize_graph(dict(id=1,source_url=None),None,nodes,edges)
        self.assertEqual(len(graph['nodes']),3);self.assertEqual(len(graph['edges']),2)
        self.assertTrue(graph['warnings'])

    def test_partial_snapshot_and_missing_relation_are_explicit(self):
        graph=serialize_graph(dict(id=1,source_url=None),dict(fully_expanded=False,node_count=0,edge_count=0),[],[])
        self.assertTrue(any("尚未完整采集" in w for w in graph['warnings']))
        self.assertTrue(any("不表示" in w for w in graph['warnings']))

    def test_shared_ancestors_and_cycles_terminate(self):
        nodes=[dict(id=i,is_root=i==1,source_url=None) for i in range(1,6)]
        edges=[dict(id=i,child_id=c,parent_id=p) for i,(c,p) in enumerate([(1,2),(1,3),(2,4),(3,4),(4,1)])]
        graph=serialize_graph(dict(id=1,source_url=None),None,nodes,edges)
        self.assertEqual(graph['unconnected_count'],1);self.assertEqual(graph['edge_count'],5)

    def test_authentication_on_every_route(self):
        client=self.client(True)
        for url in ["/catalog","/varieties","/varieties/1/graph"]:
            self.assertEqual(client.get("/api/research/pedigree"+url).status_code,403)

    def test_bounds_and_sql_errors_not_empty_graphs(self):
        client=self.client()
        for url in ["/varieties?q="+"a"*121,"/varieties?page=0","/varieties?group=core","/varieties/0/graph","/varieties/999999999999999999999/graph"]:
            self.assertEqual(client.get("/api/research/pedigree"+url).status_code,422)
        with patch("app.pedigree_graph.catalog",side_effect=SQLAlchemyError("test")):
            self.assertEqual(client.get("/api/research/pedigree/catalog").status_code,503)
        with patch("app.pedigree_graph.pedigree_graph",return_value=None):
            self.assertEqual(client.get("/api/research/pedigree/varieties/99/graph").status_code,404)

    def test_urls_may_not_be_executable_or_embed_credentials(self):
        self.assertIsNone(public_url("javascript:alert(1)"))
        self.assertIsNone(public_url("https://name:password@example.com/file"))
        self.assertEqual(public_url("https://www.ricedata.cn/variety/varis/601360.htm"),"https://www.ricedata.cn/variety/varis/601360.htm")


if __name__=="__main__": unittest.main()

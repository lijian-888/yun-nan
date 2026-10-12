import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from unittest.mock import patch
from sqlalchemy.exc import SQLAlchemyError

from app.variety_search import VarietySearchRequest, compile_search, create_variety_search_router, variety_detail


class VarietySearchTests(unittest.TestCase):
    def test_keyword_and_operators_are_not_sql(self):
        payload = VarietySearchRequest(keyword="x%' OR 1=1 --_")
        _, sql, params = compile_search(payload)
        self.assertNotIn(payload.keyword, sql)
        self.assertEqual(params['pattern'], "%x\\%' OR 1=1 --\\_%")

    def test_exact_search_includes_aliases_embedded_in_names(self):
        _, sql, _ = compile_search(VarietySearchRequest(keyword="宁9212", fuzzy=False))
        self.assertIn("regexp_split_to_table(v.variety_name", sql)
        _, sql, _ = compile_search(VarietySearchRequest(keyword="宁9212", fuzzy=True))
        self.assertNotIn("regexp_split_to_table", sql)

    def test_same_approval_contains_all_traits(self):
        _, sql, params = compile_search(VarietySearchRequest(region="江西", approval_year=2004, traits=[
            dict(trait_code="plant_height_cm", operator="gte", value=100),
            dict(trait_code="amylose_content_pct", operator="lte", value=25)]))
        self.assertIn("a.approval_region LIKE :region", sql)
        self.assertIn("a.approval_year=:year", sql)
        self.assertEqual(sql.count("a.approval_id IN (SELECT f.approval_id"), 2)
        self.assertEqual(params['region'], "江西%")

    def test_two_bounds_match_one_observation(self):
        _, sql, _ = compile_search(VarietySearchRequest(traits=[
            dict(trait_code="yield_kg_per_mu", operator="gte", value=500),
            dict(trait_code="yield_kg_per_mu", operator="lte", value=600)]))
        self.assertEqual(sql.count("rice_variety_yield_observation y"), 1)
        self.assertIn("y.yield_kg_per_mu >= :value0_0 AND y.yield_kg_per_mu <= :value0_1", sql)
        self.assertIn("y.review_status='accepted'", sql)

    def test_uncertain_range_is_not_exact_value(self):
        _, sql, _ = compile_search(VarietySearchRequest(traits=[dict(trait_code="plant_height_cm", operator="eq", value=110)]))
        self.assertIn("COALESCE(f.value_min,f.value_numeric)=:value0_0 AND COALESCE(f.value_max,f.value_numeric)=:value0_0", sql)

    def test_institute_alias_and_shared_flags(self):
        relation, sql, _ = compile_search(VarietySearchRequest(source="institute", keyword="材料", has_phenotype=True, confirmed_only=True))
        self.assertEqual(relation, "agent_query.core__material m")
        self.assertIn("agent_data.material_alias", sql)
        self.assertIn("m.has_phenotype IS TRUE", sql)
        self.assertIn("m.needs_review IS FALSE", sql)

    def test_reject_invalid_or_cross_source_filters(self):
        for data in [dict(page=0), dict(page_size=101), dict(source="institute", region="云南"), dict(field="location"),
                     dict(traits=[dict(trait_code="password", operator="eq", value=1)]),
                     dict(traits=[dict(trait_code="plant_height_cm", operator="eq", value=float('inf'))])]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                VarietySearchRequest(**data)

    def test_auth_errors_and_database_errors_not_empty(self):
        class Session:
            def rollback(self): pass
        session = Session()
        app = FastAPI()
        app.include_router(create_variety_search_router(lambda: {"role": "researcher"}, lambda: session))
        client = TestClient(app)
        with patch("app.variety_search.search_varieties", side_effect=SQLAlchemyError("test")):
            response = client.post("/api/research/variety-search/query", json={})
            self.assertEqual(response.status_code, 503)
            self.assertIn("这不表示数据库没有数据", response.json()['detail'])
        with patch("app.variety_search.variety_detail", return_value=None):
            self.assertEqual(client.get("/api/research/variety-search/ricedata/999999").status_code, 404)

    def test_all_routes_require_authorization(self):
        from fastapi import HTTPException
        def denied():
            raise HTTPException(403, "无权限")
        app = FastAPI()
        app.include_router(create_variety_search_router(denied, lambda: None))
        client = TestClient(app)
        for method, path in [("get", "/catalog"), ("post", "/query"), ("get", "/ricedata/1")]:
            self.assertEqual(getattr(client, method)("/api/research/variety-search"+path).status_code, 403)


if __name__ == "__main__":
    unittest.main()

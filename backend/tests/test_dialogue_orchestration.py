import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.ai_gateway import AIProviderSettings
from app.dialogue_orchestration import DialoguePlan, plan_dialogue, validate_fact_measurements
from app.research_agent import ResearchAgentError


class DialoguePlannerTests(unittest.TestCase):
    provider = AIProviderSettings("cherryin", "https://example.invalid/v1", "test-model", "test-token", True)

    def run_plan(self, response_content, question="你有国家水稻数据中心的数据吗", status=200):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(status, json={"choices": [{"message": {"content": response_content}}]})
        real_client = httpx.AsyncClient
        with patch("app.dialogue_orchestration.httpx.AsyncClient", side_effect=lambda **kw:
                   real_client(transport=httpx.MockTransport(handler), **kw)):
            result = asyncio.run(plan_dialogue(provider=self.provider, question=question,
                history=[{"role": "user", "content": "D优130的数据"}]))
        return result, seen

    def test_model_not_keywords_selects_route(self):
        for intent in ("capabilities", "general", "variety_fact", "reference", "research_task"):
            plan = {"intent": intent, "source": "public", "include_counts": False, "needs_web": False}
            result, requests = self.run_plan(json.dumps(plan))
            self.assertEqual(result.intent, intent)
            self.assertIn("你有国家水稻数据中心", requests[0]["messages"][1]["content"])
            self.assertNotIn("SELECT", requests[0]["messages"][1]["content"])

    def test_invalid_plan_fails_without_keyword_fallback(self):
        for output in ("好的，我来查", "{}", '{"intent":"run_sql"}',
                       json.dumps({"intent":"capabilities", "source":"public", "include_counts":"false", "needs_web":False}),
                       json.dumps({"intent":"general", "source":"public", "include_counts":False, "needs_web":False, "sql":"DROP TABLE x"})):
            with self.assertRaisesRegex(ResearchAgentError, "问题理解未完成"):
                self.run_plan(output)

    def test_upstream_failure_is_explicit_not_not_found(self):
        with self.assertRaisesRegex(ResearchAgentError, "未执行数据库查询"):
            self.run_plan("", status=503)

    def test_preserve_verified_measurements_and_units(self):
        validate_fact_measurements("直链淀粉含量24.10%，株高102.7厘米。", "24.1% 102.7cm")
        validate_fact_measurements("这条审定没有收录该指标。", "未找到该指标")

    def test_invented_or_borrowed_measurement_is_rejected(self):
        for answer, evidence in (("24.1%", "四川审定未收录"), ("株高105厘米", "株高102.7cm"),
                                 ("已查到实粒数105粒", "请先选择2000年或2003年审定")):
            with self.assertRaisesRegex(ResearchAgentError, "无查询证据"):
                validate_fact_measurements(answer, evidence)


if __name__ == "__main__":
    unittest.main()

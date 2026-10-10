import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.ai_gateway import AIProviderSettings
from app.dialogue_orchestration import DialoguePlan, plan_dialogue, polish_business_answer, validate_fact_measurements
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
        for intent in ("capabilities", "general", "variety_fact", "reference", "research_task", "database"):
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

    def test_business_answer_keeps_facts_but_not_internal_status_codes(self):
        answer = ("基因型样本表（core.genotype_sample）中 **linked（已关联）**：180个样本；"
                  "综合分标记为 **not_eligible_missing_dimensions**（不满足综合评定条件），丰产69.43分。")
        polished = polish_business_answer(answer, "按匹配状态统计并说明五性评价")
        self.assertIn("已关联", polished)
        self.assertIn("180", polished)
        self.assertIn("69.43", polished)
        self.assertNotIn("linked", polished)
        self.assertNotIn("not_eligible", polished)
        self.assertNotIn("core.genotype_sample", polished)
        self.assertEqual(polish_business_answer(answer, "请显示原始状态码"), answer)

    def test_business_answer_removes_incidental_raw_relation_and_followup(self):
        answer = ("当前 `core.genotype_sample` 表中有已关联180个样本；unmatched未返回。"
                  "\n\n如果你需要按项目进一步统计，请告诉我。")
        polished = polish_business_answer(answer, "查询基因型样本表中的样本数")
        self.assertIn("当前所查业务表中", polished)
        self.assertIn("已关联180", polished)
        self.assertIn("未匹配未返回", polished)
        self.assertNotIn("core.genotype_sample", polished)
        self.assertNotIn("项目", polished)


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.local_variety_query import lookup_local_variety_data
from app.research_clarification import clarification_for_question
from app.research_question_routing import (
    is_general_explanation, is_system_capability_question, build_system_capability_evidence,
)


class IntentContractTests(unittest.TestCase):
    def test_system_help_is_not_a_cultivar_name(self):
        for q in ("现在有哪些数据可以查询", "你可以查询哪些数据？", "目前有什么数据", "你有哪些功能？",
                  "平台能做些什么？", "介绍一下功能", "数据库查询范围是什么？", "院内有哪些数据可查？"):
            self.assertTrue(is_system_capability_question(q), q)
            session = MagicMock()
            self.assertIsNone(lookup_local_variety_data(session, q, institute_enabled=True))
            self.assertIsNone(clarification_for_question(session, q))
            session.execute.assert_not_called()

    def test_named_cultivar_and_literal_metric_are_not_system_help(self):
        for q in ("D优130有哪些数据？", "南粳9212的数据", "D优130的直链淀粉含量是多少？", "株高是多少？", "那江西"):
            self.assertFalse(is_system_capability_question(q), q)
            self.assertFalse(is_general_explanation(q), q)

    def test_scientific_and_general_questions_do_not_get_variety_clarification(self):
        prior = [SimpleNamespace(role="assistant", operation_state=[{"state":"ricedata_trait_context", "variety_id":6383}])]
        for q in ("什么是直链淀粉含量？", "为什么结实率会下降？", "如何评价稳产性？", "如何整理表型数据？",
                  "数据库怎么导出数据？", "给我一些数据清洗建议", "帮我写一段项目介绍", "如何选择杂交亲本？"):
            self.assertTrue(is_general_explanation(q), q)
            session = MagicMock()
            self.assertIsNone(lookup_local_variety_data(session, q, prior, institute_enabled=True))
            self.assertIsNone(clarification_for_question(session, q))
            session.execute.assert_not_called()

    def test_open_ended_question_does_not_execute_unscoped_measurement_sql(self):
        from app.main import build_published_evidence_context
        session = MagicMock()
        context, cards = asyncio.run(build_published_evidence_context(session, "如何评价稳产性？"))
        self.assertIn("一般知识", context)
        self.assertEqual(cards, [])
        session.execute.assert_not_called()

    def test_direct_parent_recommendation_still_requests_missing_goals(self):
        result = clarification_for_question(MagicMock(), "给我推荐亲本，制定方案")
        self.assertEqual(result["kind"], "parent_target")
        self.assertIn("目标性状", result["question"])


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN")
class RealRoutingTests(unittest.TestCase):
    def test_catalog_is_model_evidence_not_a_prepared_answer(self):
        engine = create_engine(os.environ["TEST_RICEDATA_DSN"])
        try:
            with Session(engine) as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                context, cards = build_system_capability_evidence(session, "你有哪些能力", institute_enabled=True)
                self.assertNotIn("counts", cards[0])
                self.assertIn("不是预设回答", context)
                self.assertNotIn("## 当前可以查询和讨论", context)
                self.assertNotIn("record_count", context)
                self.assertNotIn("根系", context)
                self.assertNotIn("SRC-", context)
                self.assertEqual(cards[0]["type"], "system_capabilities")
        finally:
            engine.dispose()

    def test_requested_catalog_counts_are_live_facts(self):
        engine = create_engine(os.environ["TEST_RICEDATA_DSN"])
        try:
            with Session(engine) as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                context, cards = build_system_capability_evidence(session, "数据数量是多少", institute_enabled=True)
                expected = session.scalar(text("SELECT count(*) FROM ricedata.rice_variety"))
                self.assertEqual(cards[0]["counts"]["品种基本信息"], expected)
                facts = json.loads(context.split("\n")[1])
                self.assertEqual(facts["public_catalog"][0]["record_count"], expected)
        finally:
            engine.dispose()

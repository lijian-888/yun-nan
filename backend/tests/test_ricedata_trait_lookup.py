import os
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ricedata_trait_facts import extract_narrative_facts, requested_traits
from app.ricedata_trait_lookup import _approval_followup, lookup_numeric_trait


class NarrativeExtractionTests(unittest.TestCase):
    def test_yield_paragraph_keeps_approximation_and_provenance(self):
        text = "每亩有效穗22万左右，每穗实粒数105粒左右，结实率92%左右，千粒重27克左右。"
        facts = extract_narrative_facts("yield_performance_text", text)
        target = next(item for item in facts if item["trait_code"] == "filled_grains_per_panicle")
        self.assertEqual(str(target["value_numeric"]), "105")
        self.assertEqual(target["qualifier"], "约")
        self.assertIn("每穗实粒数105粒左右", target["source_excerpt"])

    def test_two_quality_values_are_distinct(self):
        facts = extract_narrative_facts("yield_performance_text", "直链淀粉含量24.1%，蛋白质含量7.2%。")
        self.assertEqual([(item["trait_code"], str(item["value_numeric"])) for item in facts],
                         [("amylose_content_pct", "24.1"), ("protein_content_pct", "7.2")])

    def test_non_source_field_and_prose_do_not_invent_numbers(self):
        self.assertEqual(extract_narrative_facts("untrusted", "每穗实粒数105粒"), [])
        self.assertEqual(extract_narrative_facts("yield_performance_text", "每穗实粒数较多，品质优。"), [])
        self.assertEqual([spec.code for spec in requested_traits("直链淀粉含量是多少？")], ["amylose_content_pct"])
        self.assertEqual([spec.code for spec in requested_traits("整精米率是多少？")], ["head_rice_rate_pct"])

    def test_short_approval_followup_is_not_a_general_province_query(self):
        self.assertTrue(_approval_followup("那江西"))
        self.assertTrue(_approval_followup("2006年福建审定"))
        self.assertTrue(_approval_followup("2006年"))
        self.assertTrue(_approval_followup("江西省"))
        self.assertFalse(_approval_followup("江西有哪些品种适宜种植？"))


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN for local database integration")
class ApprovalLookupIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_multiple_approvals_ask_then_answer_2006(self):
        with Session(self.engine) as session:
            first = lookup_numeric_trait(session, "D优130的直链淀粉含量是多少？")
            self.assertIn("川审稻2003010", first["content"])
            self.assertIn("闽审稻2006008", first["content"])
            self.assertNotIn("24.1%", first["content"])
            previous = SimpleNamespace(role="assistant", operation_state=[first["pending"]])
            second = lookup_numeric_trait(session, "福建2006年", [previous])
            self.assertIn("24.1%", second["content"])
            self.assertIn("闽审稻2006008", second["content"])

    def test_missing_approval_does_not_borrow_other_province_value(self):
        with Session(self.engine) as session:
            result = lookup_numeric_trait(session, "D优130四川2003年审定的直链淀粉含量是多少？")
            self.assertIn("未找到", result["content"])
            self.assertNotIn("24.1%", result["content"])

    def test_malformed_source_number_is_flagged_for_review(self):
        with Session(self.engine) as session:
            result = lookup_numeric_trait(session, "徐稻3号（徐91069）的每穗实粒数是多少？")
            self.assertIn("人工核对", result["content"])
            self.assertIn("每穗实粒数粒120粒", result["content"])

    def test_yield_text_only_metric(self):
        with Session(self.engine) as session:
            result = lookup_numeric_trait(session, "淮稻8号（淮9926）的每穗实粒数是多少？")
            self.assertIn("约105粒/穗", result["content"])
            self.assertIn("产量表现", result["content"])

    def test_completed_lookup_context_carries_variety_and_trait_to_other_province(self):
        with Session(self.engine) as session:
            first = lookup_numeric_trait(session, "鄂香优华占的垩白粒率是多少？")
            self.assertIn("广西", first["content"])
            self.assertIn("江西", first["content"])
            clarification = SimpleNamespace(role="assistant", content=first["content"],
                                            operation_state=[first["context"], first["pending"]])
            guangxi = lookup_numeric_trait(session, "广西", [clarification])
            self.assertIn("未找到", guangxi["content"])
            self.assertEqual(guangxi["context"]["approval_id"], 31926)
            answered = SimpleNamespace(role="assistant", content=guangxi["content"],
                                       operation_state=[guangxi["context"]])
            jiangxi = lookup_numeric_trait(session, "那江西", [answered])
            self.assertIn("赣审稻2016034", jiangxi["content"])
            self.assertIn("4%", jiangxi["content"])
            self.assertNotIn("0.9%", jiangxi["content"])
            self.assertEqual(jiangxi["context"]["trait_code"], "chalky_grain_rate_pct")

    def test_pre_upgrade_completed_answer_and_failed_retries_still_resolve(self):
        with Session(self.engine) as session:
            legacy = SimpleNamespace(
                role="assistant",
                content="**鄂香优华占 · 2020年 · 广西 · 桂审稻2020028号**：未找到“垩白粒率”的已结构化可核对数值。",
                operation_state=[{"state": "completed", "label": "已按品种、审定记录和原始文本完成数据库查询"}],
            )
            failed_retry = SimpleNamespace(role="user", content="那江西", operation_state=[])
            result = lookup_numeric_trait(session, "那江西", [failed_retry, legacy])
            self.assertIn("4%", result["content"])

    def test_unrelated_followup_does_not_reuse_trait_context(self):
        context = {"state": "ricedata_trait_context", "variety_id": 7742,
                   "trait_code": "chalky_grain_rate_pct", "approval_id": 31926}
        answered = SimpleNamespace(role="assistant", content="...", operation_state=[context])
        with Session(self.engine) as session:
            self.assertIsNone(lookup_numeric_trait(session, "江西有哪些品种适宜种植？", [answered]))
            self.assertIsNone(lookup_numeric_trait(session, "你好", [answered]))

    def test_old_unrelated_variety_mention_is_not_silent_context(self):
        unrelated = SimpleNamespace(role="assistant", content="关于田间管理的回答", operation_state=[])
        old_question = SimpleNamespace(role="user", content="鄂香优华占的资料", operation_state=[])
        with Session(self.engine) as session:
            result = lookup_numeric_trait(session, "垩白粒率是多少？", [unrelated, old_question])
            self.assertIn("请提供完整品种名", result["content"])


if __name__ == "__main__":
    unittest.main()

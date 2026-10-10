import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.local_variety_query import _unshadowed_materials, contains_institute_history, lookup_local_variety_data, source_scope
from app.ricedata_trait_lookup import _approval_followup, lookup_numeric_trait
from app.ricedata_variety_identity import has_explicit_subject, resolve_varieties, select_name_matches
from app.ricedata_variety_overview import lookup_variety_overview


def history(result):
    return [SimpleNamespace(role="assistant", content=result["content"], operation_state=[
        state for state in [result.get("context"), result.get("pending"), *(result.get("extra_contexts") or [])]
        if state])]


class QueryContractTests(unittest.TestCase):
    def test_longest_match_is_local_to_its_occurrence(self):
        rows = [{"variety_name": "D优130"}, {"variety_name": "国稻3号"}, {"variety_name": "3号"}]
        self.assertEqual(select_name_matches("D优130和国稻3号的株高", rows), rows[:2])

    def test_numeric_spreadsheet_labels_are_not_global_identities(self):
        self.assertEqual(select_name_matches("国稻3号", [{"variety_name": "3"}]), [])

    def test_cross_source_short_label_does_not_steal_a_full_name(self):
        public = [{"variety_name": "国稻3号"}]
        private = [{"preferred_name": "稻3号"}]
        self.assertEqual(_unshadowed_materials("国稻3号的株高", public, private), [])
        private = [{"preferred_name": "国稻3号"}]
        self.assertEqual(_unshadowed_materials("国稻3号的株高", public, private), private)

    def test_only_omitted_subject_inherits_context(self):
        for question in ["结实率是多少？", "那江西", "国家", "全部审定记录", "它的全部表型数据", "1"]:
            self.assertFalse(has_explicit_subject(question), question)
        for question in ["不存在测试稻999999的结实率", "南粳9212的株高", "不存在的品种的全部表型数据"]:
            self.assertTrue(has_explicit_subject(question), question)

    def test_scope_is_explicit_and_preserved(self):
        self.assertEqual(source_scope("只查院内材料"), "institute")
        self.assertEqual(source_scope("只查国家水稻数据中心"), "public")
        self.assertEqual(source_scope("院内和国家水稻数据中心都看"), "both")
        self.assertEqual(source_scope("千粒重呢", "institute"), "institute")

    def test_private_query_history_is_detected_for_egress_guard(self):
        self.assertTrue(contains_institute_history([SimpleNamespace(operation_state=[
            {"state": "local_data_private"}], evidence=[])]))
        self.assertTrue(contains_institute_history([SimpleNamespace(operation_state=[], evidence=[
            {"type": "institute_local"}])]))
        self.assertFalse(contains_institute_history([SimpleNamespace(operation_state=[], evidence=[])]))

    def test_national_number_and_all_are_selector_followups(self):
        for question in ["国家", "国审", "闽审稻2006008", "查看全部", "全部审定记录"]:
            self.assertTrue(_approval_followup(question), question)

    @patch("app.ricedata_trait_lookup._find_varieties", return_value=[
        {"variety_id": 1, "variety_name": "测试材料", "source_url": "https://example.test/1"}])
    @patch("app.ricedata_trait_lookup._rows")
    def test_explicit_raw_value_is_returned_without_writing_source(self, rows, _):
        rows.side_effect = [[{"approval_id": 2, "approval_year": 2004, "approval_region": "江苏", "approval_no": "苏审稻2004001"}],
                            [], [], [{"yield_performance_text": "每穗实粒数105粒左右。"}]]
        result = lookup_numeric_trait(None, "测试材料的每穗实粒数是多少？")
        self.assertIn("约105粒/穗", result["content"])
        self.assertIn("按原文读取，待核对", result["content"])
        self.assertIn("每穗实粒数105粒左右", result["evidence"][0]["excerpts"][0]["text"])
        self.assertIn("产量表现", result["content"])

    @patch("app.ricedata_trait_lookup._find_varieties", return_value=[{"variety_id": 1, "variety_name": "测试材料"}])
    @patch("app.ricedata_trait_lookup._rows")
    def test_qualitative_raw_description_does_not_invent_number(self, rows, _):
        rows.side_effect = [[{"approval_id": 2, "approval_year": 2004, "approval_region": "江苏", "approval_no": "苏审稻2004001"}],
                            [], [], [{"characteristics_raw_text": "结实率较高。"}]]
        result = lookup_numeric_trait(None, "测试材料的结实率是多少？")
        self.assertIn("结实率较高", result["content"])
        self.assertIn("人工核对", result["content"])
        self.assertNotIn("95%", result["content"])

    @patch("app.ricedata_trait_lookup._find_varieties", return_value=[{"variety_id": 1, "variety_name": "测试材料"}])
    @patch("app.ricedata_trait_lookup._rows")
    def test_unsupported_raw_unit_is_not_silently_relabelled(self, rows, _):
        rows.side_effect = [[{"approval_id": 2, "approval_year": 2004, "approval_region": "江苏", "approval_no": "苏审稻2004001"}],
                            [], [], [{"characteristics_raw_text": "株高105m。"}]]
        result = lookup_numeric_trait(None, "测试材料的株高是多少？")
        self.assertIn("人工核对", result["content"])
        self.assertNotIn("**105厘米**", result["content"])

    @patch("app.ricedata_trait_lookup._find_varieties", return_value=[{"variety_id": 1, "variety_name": "测试材料"}])
    @patch("app.ricedata_trait_lookup._rows")
    def test_approximate_lower_bound_is_preserved(self, rows, _):
        rows.side_effect = [[{"approval_id": 2, "approval_year": 2004, "approval_region": "江苏", "approval_no": "苏审稻2004001"}],
                            [], [], [{"characteristics_raw_text": "结实率约85%以上。"}]]
        result = lookup_numeric_trait(None, "测试材料的结实率是多少？")
        self.assertIn("约85%以上", result["content"])

    @patch("app.ricedata_trait_lookup._find_varieties", return_value=[])
    @patch("app.ricedata_trait_lookup._rows")
    def test_ordinal_follows_displayed_subset_not_all_records(self, rows, _):
        approvals = [{"approval_id": i, "approval_year": 2000 + i, "approval_region": "江苏",
                      "approval_no": f"苏审稻200000{i}"} for i in (1, 2, 3)]
        prior = SimpleNamespace(role="assistant", operation_state=[
            {"state": "ricedata_trait_context", "variety_id": 1, "trait_code": "seed_setting_rate_pct"},
            {"state": "ricedata_trait_clarification", "variety_id": 1, "approval_ids": [3, 2]}])
        rows.side_effect = [[{"variety_id": 1, "variety_name": "测试材料"}], approvals, [], [], []]
        result = lookup_numeric_trait(None, "第一条", [prior])
        self.assertEqual(result["context"]["approval_id"], 3)

    @patch("app.local_variety_query.resolve_varieties", side_effect=OperationalError("read", {}, Exception("offline")))
    def test_database_fault_is_not_not_found(self, _):
        session = SimpleNamespace(begin_nested=lambda: nullcontext())
        result = lookup_local_variety_data(session, "只查国家水稻数据中心 D优130的株高")
        self.assertIn("暂时无法查询", result["content"])
        self.assertNotIn("未找到", result["content"])

    @patch("app.local_variety_query._materials")
    @patch("app.local_variety_query.resolve_varieties")
    @patch("app.local_variety_query.lookup_variety_overview", return_value=None)
    @patch("app.local_variety_query.lookup_numeric_trait", return_value=None)
    @patch("app.local_variety_query._core_answer", return_value={"content": "local", "evidence": []})
    def test_same_name_across_sources_requires_identity_confirmation(self, answer, numeric, overview, public, private):
        public.return_value = [{"variety_name": "测试材料", "source_variety_id": "601", "source_url": "https://example.test/601"}]
        private.return_value = [{"preferred_name": "测试材料", "material_id": "MAT-ABC"}]
        session = SimpleNamespace(begin_nested=lambda: nullcontext())
        result = lookup_local_variety_data(session, "测试材料的株高", institute_enabled=True)
        self.assertEqual(result["pending"]["state"], "local_identity_clarification")
        self.assertEqual(len(result["pending"]["options"]), 2)
        self.assertIn("尚未确认", result["content"])


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN for read-only integration")
class RealQueryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.session = Session(self.engine)
        self.session.execute(text("SET TRANSACTION READ ONLY"))
        self.session.execute(text("SET LOCAL statement_timeout='15s'"))

    def tearDown(self):
        self.session.close()

    def test_unknown_new_subject_and_next_omitted_followup_do_not_return_old_subject(self):
        first = lookup_numeric_trait(self.session, "D优130的直链淀粉含量是多少？")
        unknown = lookup_numeric_trait(self.session, "不存在测试稻999999的直链淀粉含量是多少？", history(first))
        self.assertNotIn("D优130", unknown["content"])
        following = lookup_numeric_trait(self.session, "直链淀粉含量是多少？", history(unknown) + history(first))
        self.assertNotIn("D优130", following["content"])

    def test_unknown_overview_does_not_inherit_or_use_stale_clarification_id(self):
        previous = SimpleNamespace(role="assistant", content="D优130", operation_state=[
            {"state": "ricedata_variety_context", "variety_id": 6383}])
        result = lookup_variety_overview(self.session, "不存在测试稻999999的全部表型数据",
                                         variety_id=6383, history_items=[previous])
        self.assertIn("未找到", result["content"])
        self.assertNotIn("D优130", result["content"])

    def test_multiple_names_are_not_silently_discarded(self):
        names = resolve_varieties(self.session, "D优130和国稻3号的株高分别是多少？")
        self.assertEqual({r["variety_id"] for r in names}, {6383, 42583})

    def test_all_approvals_keep_missing_and_present_values_separate(self):
        first = lookup_numeric_trait(self.session, "D优130的直链淀粉含量是多少？")
        result = lookup_numeric_trait(self.session, "查看全部", history(first))
        self.assertIn("川审稻2003010", result["content"])
        self.assertIn("闽审稻2006008", result["content"])
        self.assertNotIn("24.1%", result["content"].split("---")[0])
        self.assertIn("24.1%", result["content"].split("---")[1])

    def test_explicit_wrong_region_is_not_replaced(self):
        result = lookup_numeric_trait(self.session, "D优130云南审定的直链淀粉含量是多少？")
        self.assertIn("未找到与您指定", result["content"])
        self.assertNotIn("24.1%", result["content"])

    def test_national_followup_selects_national_approval(self):
        first = lookup_numeric_trait(self.session, "中9优838选的结实率是多少？")
        result = lookup_numeric_trait(self.session, "国家", history(first))
        self.assertIn("国审稻2001019", result["content"])
        self.assertNotIn("桂审稻200044号", result["content"])

    def test_source_name_is_not_an_approval_selector(self):
        result = lookup_numeric_trait(self.session, "只查国家水稻数据中心 D优130的直链淀粉含量")
        self.assertIsNotNone(result.get("pending"))
        self.assertIn("川审稻2003010", result["content"])
        self.assertIn("闽审稻2006008", result["content"])

    def test_nested_metric_names_can_be_queried_together(self):
        result = lookup_numeric_trait(self.session, "D优130福建的精米率和整精米率")
        self.assertIn("精米率", result["content"])
        self.assertIn("整精米率", result["content"])
        self.assertNotIn("未找到", result["content"])
        followup = lookup_numeric_trait(self.session, "直链淀粉含量呢", history(result))
        self.assertIn("D优130", followup["content"])
        self.assertIn("24.1%", followup["content"])

    def test_multi_metric_approval_followup_retains_all_requested_metrics(self):
        first = lookup_numeric_trait(self.session, "D优130的精米率和整精米率")
        second = lookup_numeric_trait(self.session, "福建", history(first))
        self.assertIn("精米率", second["content"])
        self.assertIn("整精米率", second["content"])
        self.assertNotIn("请说明", second["content"])

    def test_public_short_name_not_stolen_by_institute_numeric_label(self):
        result = lookup_local_variety_data(self.session, "国稻3号的特征特性如何？", institute_enabled=True)
        self.assertIn("国稻3号", result["content"])
        self.assertIsNone(result.get("pending"))

    def test_institute_query_and_year_followup_are_local_and_traceable(self):
        if not self.session.scalar(text("SELECT to_regclass('agent_data.material')")):
            self.skipTest("Install curated query views first")
        first = lookup_local_variety_data(self.session, "只查院内 CXCDD1447的千粒重", institute_enabled=True)
        self.assertIn("24.69", first["content"])
        self.assertIn("SRC-00168", first["content"])
        self.assertNotIn("国家水稻数据中心", first["content"])
        second = lookup_local_variety_data(self.session, "2025年", history(first), institute_enabled=True)
        self.assertIn("千粒重", second["content"])
        self.assertNotIn("2026", second["content"])


if __name__ == "__main__":
    unittest.main()

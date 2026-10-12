import unittest

from app.research_agent import ResearchAgentError
from app.ricedata_fact_guard import validate_scoped_fact_answer


class ScopedFactGuardTests(unittest.TestCase):
    def setUp(self):
        self.scope = {
            "state": "numeric_fact_scope", "variety_id": 6383,
            "approvals": [
                {"approval_id": 1, "approval_year": 2003, "approval_region": "四川", "approval_no": "川审稻2003010"},
                {"approval_id": 2, "approval_year": 2006, "approval_region": "福建", "approval_no": "闽审稻2006008"},
            ],
            "bindings": [{"approval_id": 2, "trait_code": "amylose_content_pct",
                          "value": "24.1%", "status": "confirmed"}],
        }

    def test_correct_record_and_missing_other_record(self):
        validate_scoped_fact_answer("四川2003年未收录。福建2006年直链淀粉含量24.1%。", self.scope)
        validate_scoped_fact_answer("| 审定记录 | 直链淀粉含量 |\n| 福建2006年 | 24.1% |", self.scope)

    def test_real_number_on_wrong_approval_is_rejected(self):
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("四川2003年直链淀粉含量24.1%。", self.scope)

    def test_real_number_on_wrong_trait_is_rejected(self):
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("福建2006年结实率24.1%。", self.scope)

    def test_multiple_approvals_require_a_record_label(self):
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("直链淀粉含量为24.1%。", self.scope)
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("数据库未找到直链淀粉含量。", self.scope)

    def test_single_selected_approval_allows_compact_answer(self):
        one = {**self.scope, "approvals": self.scope["approvals"][1:]}
        validate_scoped_fact_answer("直链淀粉含量为24.1%。", one)
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("四川2003年直链淀粉含量24.1%。", one)

    def test_unverified_candidate_requires_plain_status(self):
        candidate = {"state": "numeric_fact_scope", "variety_id": 9,
                     "approvals": [{"approval_id": 3, "approval_year": 1997,
                                    "approval_region": "辽宁", "approval_no": "辽审稻[1997]64号"}],
                     "bindings": [{"approval_id": 3, "trait_code": "yield_kg_per_mu",
                                   "value": "平均亩产556.6Kg", "status": "unverified_original"}]}
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("亩产556.6公斤/亩。", candidate)
        validate_scoped_fact_answer("原文候选亩产556.6公斤/亩，待核对。", candidate)
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer("原文中有候选。亩产已确认为556.6公斤/亩。", candidate)

    def test_no_scope_keeps_general_answers_unaffected(self):
        validate_scoped_fact_answer("品种比较应考虑多点试验。", None)

    def test_yield_scope_accepts_sourced_relative_change_but_not_wrong_yield(self):
        yield_scope = {"state": "numeric_fact_scope", "variety_id": 6383,
                       "approvals": [{"approval_id": 47765, "approval_year": 2006,
                                      "approval_region": "福建", "approval_no": "闽审稻2006008"}],
                       "bindings": [{"approval_id": 47765, "trait_code": "yield_kg_per_mu",
                                     "value": "584.44公斤/亩", "status": "confirmed"}]}
        validate_scoped_fact_answer(
            "D优130（闽审稻2006008）在福建2003年区试平均亩产584.44公斤/亩，较对照增产4.93%。",
            yield_scope,
        )
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer(
                "D优130（闽审稻2006008）在福建2003年区试平均亩产528.73公斤/亩，增产4.93%。",
                yield_scope,
            )


if __name__ == "__main__":
    unittest.main()

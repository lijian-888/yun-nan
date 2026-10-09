import os
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ricedata_trait_facts import extract_narrative_facts, requested_traits
from app.ricedata_trait_lookup import lookup_numeric_trait


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


if __name__ == "__main__":
    unittest.main()

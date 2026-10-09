import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.research_clarification import clarification_for_question, expanded_question


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN for local database integration")
class ResearchClarificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_broad_variety_data_asks_for_metric_and_approval(self):
        with Session(self.engine) as session:
            question = "中9优838选（国丰1号;中优838）的表型数据"
            result = clarification_for_question(session, question)
            self.assertEqual(result["kind"], "variety_data")
            self.assertIn("结实率", result["question"])
            self.assertIn("2000年广西", result["question"])
            self.assertIsNone(clarification_for_question(session, question, supplement="全部表型数据", attempt=1))
            self.assertIsNone(clarification_for_question(session, question, supplement="结实率", attempt=1))
            followup = clarification_for_question(session, question, supplement="2000年广西", attempt=1)
            self.assertIn("哪些数据", followup["question"])
            self.assertNotIn("请指定其中一条", followup["question"])

    def test_short_canonical_name_is_preserved_in_clarification(self):
        with Session(self.engine) as session:
            result = clarification_for_question(session, "国稻3号的表型数据给我一下")
            self.assertEqual(result["variety_id"], 42583)
            self.assertIn("国稻3号", result["question"])
            self.assertNotIn("**3号**", result["question"])

    def test_precise_metric_and_named_overview_do_not_get_interrupted(self):
        with Session(self.engine) as session:
            self.assertIsNone(clarification_for_question(session, "D优130的直链淀粉含量是多少？"))
            self.assertIsNone(clarification_for_question(session, "中9优838选的特征特性如何？"))

    def test_parent_request_asks_only_missing_conditions(self):
        with Session(self.engine) as session:
            original = "给我推荐亲本，要高产、抗倒伏、米质好"
            first = clarification_for_question(session, original)
            self.assertIn("籼稻还是粳稻", first["question"])
            self.assertIn("种植地区", first["question"])
            second = clarification_for_question(session, original, supplement="籼稻", attempt=1)
            self.assertNotIn("籼稻还是粳稻", second["question"])
            self.assertIn("种植地区", second["question"])
            self.assertIsNone(clarification_for_question(session, original, supplement="籼稻，云南", attempt=1))
            no_target = clarification_for_question(session, "给我推荐亲本，云南籼稻")
            self.assertIn("目标性状", no_target["question"])
            source_not_region = clarification_for_question(session, "根据云南农科院的数据推荐高产亲本")
            self.assertIn("种植地区", source_not_region["question"])

    def test_a_metric_reply_does_not_make_a_missing_variety_optional(self):
        with Session(self.engine) as session:
            result = clarification_for_question(session, "查一下品种数据", supplement="结实率", attempt=1)
            self.assertIn("品种名称", result["question"])

    def test_skip_and_answer_preserve_existing_details_without_inventing(self):
        answered = expanded_question("推荐亲本", "籼稻，云南", skipped=False)
        self.assertIn("用户补充条件：籼稻，云南", answered)
        self.assertIn("不能自行补造", answered)
        skipped = expanded_question("推荐亲本", "籼稻", skipped=True)
        self.assertIn("此前已补充：籼稻", skipped)
        self.assertIn("不得把不同审定记录", skipped)


if __name__ == "__main__":
    unittest.main()

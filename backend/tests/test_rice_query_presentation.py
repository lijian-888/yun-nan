import os
import unittest
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.rice_query_presentation import (
    display_value, source_excerpts, table, trait_label, trial_label, yield_change, yield_year,
)
from app.ricedata_variety_overview import lookup_variety_overview


class PresentationTests(unittest.TestCase):
    def test_units_are_labels_without_numeric_scaling(self):
        cases = [("plant_height_cm", "cm", "102.7厘米"),
                 ("effective_panicles_10k_per_mu", "10k/mu", "102.7万穗/亩"),
                 ("grains_per_panicle", "grain", "102.7粒/穗"),
                 ("thousand_grain_weight_g", "g", "102.7克"),
                 ("length_width_ratio", "ratio", "102.7")]
        for code, unit, expected in cases:
            self.assertEqual(display_value({"trait_code": code, "unit": unit, "value_numeric": Decimal("102.70")}), expected)

    def test_unknown_units_are_not_exposed_or_silently_converted(self):
        result = display_value({"value_numeric": 10, "unit": "unexpected_unit"})
        self.assertEqual(result, "10（单位待核）")

    def test_ranges_and_combined_approximate_bounds_survive(self):
        self.assertEqual(display_value({"value_min": 100, "value_max": 105, "unit": "cm", "qualifier": "约"}),
                         "约100～105厘米")
        self.assertEqual(display_value({"value_numeric": 85, "unit": "%", "value_text": "结实率约85%以上"}),
                         "约85%以上")

    def test_legacy_source_qualifier_is_scoped_to_same_metric_and_number(self):
        source = "株高100厘米左右，千粒重约27克。"
        self.assertEqual(display_value({"trait_code": "plant_height_cm", "value_numeric": 100, "unit": "cm", "source_text": source}),
                         "约100厘米")
        self.assertEqual(display_value({"trait_code": "plant_height_cm", "value_numeric": 99, "unit": "cm", "source_text": source}),
                         "99厘米")

    def test_growth_duration_and_quality_label_follow_literal_meaning(self):
        self.assertEqual(trait_label({"trait_code": "growth_duration_days", "source_text": "出苗至成熟120天"}), "出苗至成熟日数")
        self.assertEqual(trait_label({"trait_code": "protein_content_pct", "source_text": "粗蛋白含量7.2%"}), "粗蛋白含量")
        self.assertEqual(trait_label({"trait_code": "unknown_trait"}), "其他性状（名称待核）")

    def test_qualitative_observations_do_not_acquire_numeric_units(self):
        self.assertEqual(display_value({"value_text": "感病", "unit": "grade"}), "感病")

    def test_trial_and_aggregate_years_do_not_use_approval_year(self):
        self.assertEqual(trial_label("regional_trial"), "区域试验")
        self.assertEqual(trial_label("production_trial"), "生产试验")
        self.assertEqual(trial_label("internal_code"), "试验类型待核")
        self.assertEqual(yield_year({"covered_years": [2003, 2004], "is_aggregate": True}), "2003/2004年（平均）")
        self.assertIn("对应待核", yield_year({"covered_years": [2003, 2004]}))

    def test_relative_change_preserves_sign_and_missingness(self):
        self.assertEqual(yield_change({"relative_change_pct": Decimal("-0.35")}), "减产0.35%")
        self.assertEqual(yield_change({"relative_change_pct": 0}), "0%")
        self.assertEqual(yield_change({}), "未提供")

    def test_originals_are_once_per_section_and_clipping_is_explicit(self):
        originals = source_excerpts([("全文", "株高100厘米，结实率85%。"), ("节选", "株高100厘米,")])
        self.assertEqual(len(originals), 1)
        long = source_excerpts([("全文", "原" * 4001)])
        self.assertIn("仅展示节选", long[0]["text"])

    def test_database_text_cannot_break_markdown_table(self):
        result = table(["性状", "结果"], [["A|B\nC", "<script>**值**"]])
        self.assertIn(r"A\|B C", result)
        self.assertIn(r"\<script\>", result)
        self.assertEqual(len(result.splitlines()), 3)


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN")
class PresentationDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])
        self.session = Session(self.engine)
        self.session.execute(text("SET TRANSACTION READ ONLY"))
        self.session.execute(text("SET LOCAL statement_timeout='15s'"))

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_real_xiannong_answer_has_chinese_tables_and_folded_originals(self):
        result = lookup_variety_overview(self.session, "先农8号的全部表型数据")
        body = result["content"]
        for expected in ["102.7厘米", "21.8万穗/亩", "117.7粒/穗", "79.1%", "24.7克",
                         "2005年", "2003年", "2004年", "区域试验", "430.21", "减产0.35%", "增产4.94%"]:
            self.assertIn(expected, body)
        for internal in ["regional_trial", "grain", "10k/mu", "生育期/全生育期", "特征特性原文", "本地已收录"]:
            self.assertNotIn(internal, body)
        originals = result["evidence"][0]["excerpts"]
        self.assertEqual([item["title"] for item in originals], ["特征特性原文", "产量表现原文"])
        self.assertIn("株高102.7厘米", originals[0]["text"])

    def test_real_multi_approval_values_remain_separate(self):
        result = lookup_variety_overview(self.session, "D优130的全部表型数据")
        sections = result["content"].split("### ")
        self.assertIn("川审稻2003010", sections[1])
        self.assertNotIn("24.1%", sections[1])
        self.assertIn("闽审稻2006008", sections[2])
        self.assertIn("24.1%", sections[2])


if __name__ == "__main__":
    unittest.main()

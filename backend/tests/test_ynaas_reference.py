import unittest

from app.ynaas_reference import _gene_terms, _reference_intents, build_ynaas_database_evidence


class YnaasReferenceIntentTests(unittest.TestCase):
    def test_variety_and_pedigree_intent(self):
        self.assertEqual(
            _reference_intents("查询荃广优1606的品种系谱和父母本"),
            (True, True, False),
        )

    def test_gene_identifiers_are_normalized_and_bounded(self):
        self.assertEqual(_gene_terms("查询 OsSPL14 和 Hd3a 基因的 GO 功能"), ["osspl14", "hd3a"])

    def test_unrelated_question_does_not_touch_database(self):
        class SessionThatMustNotBeUsed:
            def execute(self, *args, **kwargs):
                raise AssertionError("database should not be queried")

            def scalar(self, *args, **kwargs):
                raise AssertionError("database should not be queried")

        self.assertEqual(
            build_ynaas_database_evidence(SessionThatMustNotBeUsed(), "今天的田间天气怎么样？"),
            ("", []),
        )


if __name__ == "__main__":
    unittest.main()

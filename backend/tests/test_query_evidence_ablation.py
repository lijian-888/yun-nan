"""Paired on/off checks for the mechanisms protecting public fact queries.

Run with TEST_RICEDATA_DSN against a read-only copy of the RiceData database.
These are regression probes, not a claim of corpus-wide extraction accuracy.
"""

import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dialogue_orchestration import validate_fact_measurements
from app.research_agent import ResearchAgentError
from app.ricedata_fact_guard import validate_scoped_fact_answer
from app.ricedata_trait_lookup import lookup_numeric_trait
from app.ricedata_variety_identity import resolve_varieties
import app.ricedata_trait_lookup as trait_lookup


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN for read-only ablation")
class QueryEvidenceAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_bounded_identity_limit_drops_a_real_record(self):
        with Session(self.engine) as session:
            self.assertEqual(len(resolve_varieties(session, "R18的株高", limit=8)), 8)
            self.assertEqual(len(resolve_varieties(session, "R18的株高")), 9)

    def test_original_candidate_removes_a_false_absence_without_confirming_it(self):
        question = "抚粳3号（辽粳64）辽宁1997年的亩产是多少？"
        with Session(self.engine) as session:
            with patch.object(trait_lookup, "extract_yield_candidates", return_value=[]):
                without = lookup_numeric_trait(session, question)
            with_candidates = lookup_numeric_trait(session, question)
        self.assertNotIn("556.6", without["content"])
        self.assertIn("556.6", with_candidates["content"])
        self.assertIn("待核对", with_candidates["content"])

    def test_view_projection_succeeds_when_direct_raw_table_is_denied(self):
        with Session(self.engine) as session:
            if not session.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ynaas_longyun_api')")):
                self.skipTest("Yunnan API role is not installed")
            direct_allowed = session.scalar(text(
                "SELECT has_table_privilege('ynaas_longyun_api',"
                "'ricedata.rice_variety_trait_measurement','SELECT')"))
        if direct_allowed:
            self.skipTest("This database grants raw-table SELECT; no permission contrast here")
        with Session(self.engine) as session:
            session.execute(text("SET LOCAL ROLE ynaas_longyun_api"))
            result = lookup_numeric_trait(session, "D优130福建2006年的直链淀粉含量是多少？")
            self.assertIn("24.1%", result["content"])
        raw_rows = trait_lookup._rows

        def raw_table_ablation(session, query, params):
            return raw_rows(session, query.replace(
                "agent_query.ricedata__rice_variety_trait_measurement",
                "ricedata.rice_variety_trait_measurement"), params)

        with Session(self.engine) as session:
            session.execute(text("SET LOCAL ROLE ynaas_longyun_api"))
            with patch.object(trait_lookup, "_rows", side_effect=raw_table_ablation):
                with self.assertRaises(SQLAlchemyError):
                    lookup_numeric_trait(session, "D优130福建2006年的直链淀粉含量是多少？")

    def test_numeric_membership_without_approval_binding_misses_a_swap(self):
        answer = "四川2003年审定的直链淀粉含量24.1%。"
        evidence = "四川2003年未收录；福建2006年直链淀粉含量24.1%。"
        validate_fact_measurements(answer, evidence)  # disabled scoped guard: passes
        scope = {"state": "numeric_fact_scope", "variety_id": 6383,
                 "approvals": [{"approval_id": 1, "approval_year": 2003, "approval_region": "四川"},
                               {"approval_id": 2, "approval_year": 2006, "approval_region": "福建"}],
                 "bindings": [{"approval_id": 2, "trait_code": "amylose_content_pct",
                               "value": "24.1%", "status": "confirmed"}]}
        with self.assertRaises(ResearchAgentError):
            validate_scoped_fact_answer(answer, scope)


if __name__ == "__main__":
    unittest.main()

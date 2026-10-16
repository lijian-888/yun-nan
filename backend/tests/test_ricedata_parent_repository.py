import inspect
import unittest

from app.parent_recommendation_engine import ConditionKind, ConditionSpec, ParentRole, RecommendationMode, RecommendationRequest
from app.ricedata_parent_repository import RiceDataParentRepository, _ancestor_profiles, classify_roles, variety_attributes


class RiceDataParentRepositoryTests(unittest.TestCase):
    def test_trait_summary_is_joined_to_approval_for_variety_identity(self):
        source = inspect.getsource(RiceDataParentRepository)
        self.assertNotIn("SELECT s.variety_id", source)
        self.assertNotIn("v.variety_id=s.variety_id", source)
        self.assertIn("ta.variety_id", source)
        self.assertIn("v.variety_id=a.variety_id", source)

    def test_candidate_recall_uses_single_aggregate_fact_scan(self):
        class EmptyResult:
            def mappings(self): return self
            def __iter__(self): return iter(())

        class RecordingSession:
            def __init__(self): self.statements = []
            def execute(self, statement, params=None):
                self.statements.append((str(statement), params or {}))
                return EmptyResult()

        session = RecordingSession()
        request = RecommendationRequest(
            mode=RecommendationMode.CONVENTIONAL,
            breeding_goal="测试目标",
            conditions=(ConditionSpec(
                code="growth", label="生育期", field="growth_duration_days",
                operator="between", value=(120, 150), kind=ConditionKind.HARD,
            ),),
        )
        RiceDataParentRepository(session)._candidate_rows(request)
        sql = session.statements[-1][0]
        self.assertIn("trait_fact AS", sql)
        self.assertIn("trait_scores AS", sql)
        self.assertNotIn("CASE WHEN EXISTS", sql)

    def test_role_classification_keeps_unknown_sterile_line_out_of_a_specific_system(self):
        self.assertEqual(classify_roles("籼型三系不育系", "甲A"), frozenset({ParentRole.STERILE_THREE_LINE}))
        self.assertEqual(classify_roles("籼型两系不育系", "乙S"), frozenset({ParentRole.STERILE_TWO_LINE}))
        self.assertEqual(classify_roles("籼型不育系", "待核实"), frozenset({ParentRole.UNKNOWN}))
        self.assertEqual(classify_roles("籼型恢复系", "丙R"), frozenset({ParentRole.RESTORER}))

    def test_variety_attributes_are_derived_only_from_recorded_type(self):
        self.assertEqual(variety_attributes("籼型常规水稻")["rice_type"], "indica")
        self.assertEqual(variety_attributes("粳型常规水稻")["variety_kind"], "inbred")
        self.assertEqual(variety_attributes("")["rice_type"], "unknown")

    def test_ancestor_paths_use_linked_variety_identity_not_unlinked_same_name(self):
        snapshots = [{"pedigree_snapshot_id": 10, "variety_id": 1, "max_depth": 3, "fully_expanded": True}]
        nodes = [
            {"pedigree_snapshot_id": 10, "pedigree_node_id": 100, "material_name": "甲", "linked_variety_id": 1, "is_root": True},
            {"pedigree_snapshot_id": 10, "pedigree_node_id": 101, "material_name": "共同祖先", "linked_variety_id": 900, "is_root": False},
            {"pedigree_snapshot_id": 10, "pedigree_node_id": 102, "material_name": "同名未链接", "linked_variety_id": None, "is_root": False},
        ]
        edges = [
            {"pedigree_snapshot_id": 10, "pedigree_edge_id": 1, "child_node_id": 100, "parent_node_id": 101},
            {"pedigree_snapshot_id": 10, "pedigree_edge_id": 2, "child_node_id": 100, "parent_node_id": 102},
        ]
        profile = _ancestor_profiles([1], snapshots, nodes, edges, 3)[1]
        self.assertIn("900", profile.ancestors)
        self.assertNotIn("同名未链接", profile.ancestors)
        self.assertTrue(any("未完成全局身份链接" in warning for warning in profile.warnings))


if __name__ == "__main__":
    unittest.main()

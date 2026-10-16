import unittest

from app.parent_recommendation_engine import (
    AncestorPath,
    CandidateProfile,
    ConditionKind,
    ConditionScope,
    ConditionSpec,
    EvidencePoint,
    KinshipPolicy,
    ParentRecommendationEngine,
    ParentRole,
    PedigreeProfile,
    RecommendationMode,
    RecommendationRequest,
)


class MemoryRiceDataRepository:
    def __init__(self, candidates):
        self._candidates = candidates

    def candidates(self, request):
        return self._candidates[:request.candidate_limit]


def point(field, value, source, *, region="云南", status="accepted"):
    return EvidencePoint(field, value, source, region=region, review_status=status)


def candidate(variety_id, name, *, role=ParentRole.CONVENTIONAL, yield_change=5,
              disease=3, complete=5, ancestors=None):
    return CandidateProfile(
        variety_id=variety_id,
        name=name,
        variety_type="籼型常规水稻",
        roles=frozenset({role}),
        attributes={"rice_type": "indica"},
        evidence={
            "yield_relative_change_pct": (point("yield_relative_change_pct", yield_change, f"yield:{variety_id}"),),
            "leaf_blast_grade": (point("leaf_blast_grade", disease, f"disease:{variety_id}"),),
        },
        pedigree=PedigreeProfile(ancestors or {}, complete),
        source_url=f"https://www.ricedata.cn/variety/{variety_id}",
    )


def request(*conditions, mode=RecommendationMode.CONVENTIONAL, depth=3, exclude_depth=3):
    return RecommendationRequest(
        mode=mode,
        breeding_goal="提高产量并保持稻瘟病抗性",
        target_region="云南",
        conditions=tuple(conditions),
        kinship=KinshipPolicy(depth=depth, exclude_through_depth=exclude_depth),
    )


class ParentRecommendationEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = ParentRecommendationEngine()
        self.yield_preference = ConditionSpec(
            "yield", "相对对照增产不低于3%", "yield_relative_change_pct", "gte", 3,
            ConditionKind.PREFERENCE, ConditionScope.AT_LEAST_ONE, importance=3,
        )
        self.disease_gate = ConditionSpec(
            "blast", "叶瘟等级不高于5级", "leaf_blast_grade", "lte", 5,
            ConditionKind.HARD, ConditionScope.EVERY_PARENT, evidence_policy="worst",
        )

    def test_only_repository_candidates_are_used_and_result_names_ricedata_source(self):
        result = self.engine.recommend(
            request(self.yield_preference, self.disease_gate),
            MemoryRiceDataRepository([candidate(1, "甲"), candidate(2, "乙")]),
        )
        self.assertEqual(result["candidate_source"], "国家水稻数据中心")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["recommendations"][0]["tier"], "A")

    def test_hard_gate_cannot_be_compensated_by_a_high_preference_score(self):
        result = self.engine.recommend(
            request(self.yield_preference, self.disease_gate),
            MemoryRiceDataRepository([candidate(1, "高产感病", yield_change=20, disease=8), candidate(2, "抗病", disease=2)]),
        )
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["excluded_combinations"][0]["tier"], "excluded")
        self.assertTrue(any("不满足" in reason for reason in result["excluded_combinations"][0]["exclusion_reasons"]))

    def test_missing_hard_evidence_goes_to_pending_not_pass(self):
        missing = candidate(1, "缺证据")
        missing = CandidateProfile(**{**missing.__dict__, "evidence": {"yield_relative_change_pct": missing.evidence["yield_relative_change_pct"]}})
        result = self.engine.recommend(
            request(self.disease_gate),
            MemoryRiceDataRepository([missing, candidate(2, "有证据")]),
        )
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["pending_verification"][0]["tier"], "pending")
        self.assertTrue(result["pending_verification"][0]["data_gaps"])

    def test_three_generation_common_ancestor_is_excluded_with_paths(self):
        shared_a = AncestorPath("900", "共同祖先", 3, ("甲", "甲父", "甲祖", "共同祖先"), ("edge-a",))
        shared_b = AncestorPath("900", "共同祖先", 2, ("乙", "乙父", "共同祖先"), ("edge-b",))
        result = self.engine.recommend(
            request(self.yield_preference, depth=3, exclude_depth=3),
            MemoryRiceDataRepository([
                candidate(1, "甲", ancestors={"900": shared_a}),
                candidate(2, "乙", ancestors={"900": shared_b}),
            ]),
        )
        excluded = result["excluded_combinations"][0]
        self.assertEqual(excluded["kinship"]["level"], "excluded")
        self.assertEqual(excluded["kinship"]["common_ancestors"][0]["name"], "共同祖先")
        self.assertEqual(excluded["kinship"]["common_ancestors"][0]["female_path"][0], "甲")

    def test_incomplete_pedigree_is_unknown_not_unrelated(self):
        result = self.engine.recommend(
            request(self.yield_preference, depth=5, exclude_depth=3),
            MemoryRiceDataRepository([
                candidate(1, "甲", complete=2), candidate(2, "乙", complete=5),
            ]),
        )
        item = result["recommendations"][0]
        self.assertEqual(item["kinship"]["level"], "unknown")
        self.assertTrue(any("不能据此声称无亲缘" in reason for reason in item["kinship"]["reasons"]))

    def test_hybrid_roles_are_directional_and_unknown_roles_are_pending(self):
        sterile = candidate(1, "三系不育系", role=ParentRole.STERILE_THREE_LINE)
        restorer = candidate(2, "恢复系", role=ParentRole.RESTORER)
        unknown = candidate(3, "角色待核实", role=ParentRole.UNKNOWN)
        result = self.engine.recommend(
            request(self.yield_preference, mode=RecommendationMode.HYBRID_THREE_LINE),
            MemoryRiceDataRepository([sterile, restorer, unknown]),
        )
        formal_pairs = {(item["female_parent"]["variety_id"], item["male_parent"]["variety_id"])
                        for item in result["recommendations"]}
        self.assertIn((1, 2), formal_pairs)
        self.assertNotIn((2, 1), formal_pairs)
        self.assertTrue(result["pending_verification"])

    def test_untrusted_machine_extraction_does_not_satisfy_a_hard_gate(self):
        untrusted = candidate(1, "待核对")
        evidence = dict(untrusted.evidence)
        evidence["leaf_blast_grade"] = (point("leaf_blast_grade", 2, "machine:1", status="machine_extracted"),)
        untrusted = CandidateProfile(**{**untrusted.__dict__, "evidence": evidence})
        result = self.engine.recommend(
            request(self.disease_gate),
            MemoryRiceDataRepository([untrusted, candidate(2, "已核验")]),
        )
        self.assertEqual(result["pending_verification"][0]["tier"], "pending")


if __name__ == "__main__":
    unittest.main()

"""Deterministic parent recommendation domain module.

The public interface is ``ParentRecommendationEngine.recommend``.  Language
models may prepare a draft request, but only a researcher-confirmed request is
accepted here.  The implementation never fills missing evidence and never
turns a score into an offspring-performance probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Any, Protocol, Sequence


class RecommendationMode(str, Enum):
    CONVENTIONAL = "conventional"
    HYBRID_THREE_LINE = "hybrid_three_line"
    HYBRID_TWO_LINE = "hybrid_two_line"


class ConditionKind(str, Enum):
    HARD = "hard"
    PREFERENCE = "preference"


class ConditionScope(str, Enum):
    EVERY_PARENT = "every_parent"
    AT_LEAST_ONE = "at_least_one"
    FEMALE = "female"
    MALE = "male"
    PAIR = "pair"


class EvidenceState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class ParentRole(str, Enum):
    CONVENTIONAL = "conventional"
    STERILE_THREE_LINE = "sterile_three_line"
    STERILE_TWO_LINE = "sterile_two_line"
    RESTORER = "restorer"
    UNKNOWN = "unknown"


class KinshipLevel(str, Enum):
    EXCLUDED = "excluded"
    HIGH = "high"
    WARNING = "warning"
    LOW = "low"
    UNKNOWN = "unknown"


TRUSTED_REVIEW_STATES = frozenset({"accepted", "verified", "curated", "source_verified"})


@dataclass(frozen=True)
class EvidencePoint:
    field: str
    value: float | str
    source_id: str
    source_url: str = ""
    unit: str = ""
    region: str = ""
    year: int | None = None
    review_status: str = "accepted"
    excerpt: str = ""

    @property
    def trusted(self) -> bool:
        return self.review_status in TRUSTED_REVIEW_STATES


@dataclass(frozen=True)
class AncestorPath:
    identity: str
    name: str
    depth: int
    path: tuple[str, ...]
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PedigreeProfile:
    ancestors: dict[str, AncestorPath] = field(default_factory=dict)
    complete_through_depth: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FiveTraitProfile:
    """Existing five-trait evaluation for one parent, never an offspring prediction."""

    yield_score: float | None = None
    stability_score: float | None = None
    disease_score: float | None = None
    stress_score: float | None = None
    quality_score: float | None = None
    comprehensive_score: float | None = None
    comprehensive_rank: int | None = None
    scored_dimension_count: int = 0
    missing_dimensions: tuple[str, ...] = ()
    evaluation_status: str = "not_evaluated"
    evaluation_profile_id: str = ""
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    interpretation_boundary: str = ""


@dataclass(frozen=True)
class CandidateProfile:
    variety_id: int
    name: str
    variety_type: str
    roles: frozenset[ParentRole]
    attributes: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, tuple[EvidencePoint, ...]] = field(default_factory=dict)
    pedigree: PedigreeProfile = field(default_factory=PedigreeProfile)
    five_trait: FiveTraitProfile = field(default_factory=FiveTraitProfile)
    source_url: str = ""


@dataclass(frozen=True)
class ConditionSpec:
    code: str
    label: str
    field: str
    operator: str
    value: float | str | tuple[float, float] | tuple[str, ...]
    kind: ConditionKind
    scope: ConditionScope = ConditionScope.EVERY_PARENT
    importance: int = 2
    unit: str = ""
    require_trusted_evidence: bool = True
    evidence_policy: str = "consistent"

    def __post_init__(self) -> None:
        if self.operator not in {"eq", "neq", "gte", "gt", "lte", "lt", "between", "in", "contains"}:
            raise ValueError(f"不支持的条件运算符：{self.operator}")
        if self.importance not in {1, 2, 3}:
            raise ValueError("偏好重要度只能为 1、2 或 3。")
        if self.evidence_policy not in {"consistent", "worst"}:
            raise ValueError("证据聚合策略只能为 consistent 或 worst。")


@dataclass(frozen=True)
class KinshipPolicy:
    depth: int = 3
    exclude_through_depth: int = 3
    require_complete_pedigree: bool = False

    def __post_init__(self) -> None:
        if self.depth not in {3, 5}:
            raise ValueError("系谱检查深度只能为 3 代或 5 代。")
        if not 2 <= self.exclude_through_depth <= self.depth:
            raise ValueError("共同祖先排除代数必须在 2 代至检查深度之间。")


@dataclass(frozen=True)
class RecommendationRequest:
    mode: RecommendationMode
    breeding_goal: str
    conditions: tuple[ConditionSpec, ...]
    target_region: str = ""
    pinned_variety_ids: tuple[int, ...] = ()
    excluded_variety_ids: tuple[int, ...] = ()
    candidate_limit: int = 80
    result_limit: int = 10
    kinship: KinshipPolicy = field(default_factory=KinshipPolicy)

    def __post_init__(self) -> None:
        if len(self.breeding_goal.strip()) < 2:
            raise ValueError("请填写育种目标。")
        if not 2 <= self.candidate_limit <= 120:
            raise ValueError("候选数量上限必须在 2–120 之间。")
        if self.mode is not RecommendationMode.CONVENTIONAL and self.candidate_limit > 80:
            raise ValueError("杂交稻定向配组的候选数量上限为 80。")
        if not 1 <= self.result_limit <= 50:
            raise ValueError("推荐结果数量必须在 1–50 之间。")
        if set(self.pinned_variety_ids) & set(self.excluded_variety_ids):
            raise ValueError("同一品种不能同时处于必评和排除名单。")


class ParentEvidenceRepository(Protocol):
    """The production and in-memory adapters satisfy this internal seam."""

    def candidates(self, request: RecommendationRequest) -> Sequence[CandidateProfile]: ...


@dataclass(frozen=True)
class ConditionOutcome:
    condition_code: str
    label: str
    state: EvidenceState
    reasons: tuple[str, ...]
    evidence: tuple[EvidencePoint, ...] = ()


@dataclass(frozen=True)
class KinshipOutcome:
    level: KinshipLevel
    reasons: tuple[str, ...]
    common_ancestors: tuple[dict[str, Any], ...]
    pedigree_complete: bool


def _compare(actual: float | str, condition: ConditionSpec) -> bool:
    expected = condition.value
    if condition.operator == "contains":
        return str(expected).lower() in str(actual).lower()
    if condition.operator == "in":
        choices = expected if isinstance(expected, tuple) else (expected,)
        return str(actual) in {str(item) for item in choices}
    if condition.operator in {"eq", "neq"}:
        equal = str(actual).strip().lower() == str(expected).strip().lower()
        return equal if condition.operator == "eq" else not equal
    try:
        number = float(actual)
    except (TypeError, ValueError):
        return False
    if condition.operator == "between":
        if not isinstance(expected, tuple) or len(expected) != 2:
            raise ValueError(f"条件 {condition.label} 的区间值无效。")
        low, high = float(expected[0]), float(expected[1])
        return low <= number <= high
    target = float(expected)  # type: ignore[arg-type]
    return {
        "gte": number >= target,
        "gt": number > target,
        "lte": number <= target,
        "lt": number < target,
    }[condition.operator]


def _candidate_evidence(candidate: CandidateProfile, condition: ConditionSpec, target_region: str) -> tuple[EvidencePoint, ...]:
    if condition.field in candidate.attributes:
        return (EvidencePoint(
            field=condition.field,
            value=candidate.attributes[condition.field],
            source_id=f"ricedata:variety:{candidate.variety_id}",
            source_url=candidate.source_url,
            review_status="source_verified",
        ),)
    points = tuple(candidate.evidence.get(condition.field, ()))
    if condition.require_trusted_evidence:
        points = tuple(point for point in points if point.trusted)
    if target_region:
        local = tuple(point for point in points if point.region and point.region.startswith(target_region))
        if local:
            return local
    return points


def evaluate_candidate_condition(
    candidate: CandidateProfile,
    condition: ConditionSpec,
    target_region: str = "",
) -> ConditionOutcome:
    points = _candidate_evidence(candidate, condition, target_region)
    if not points:
        return ConditionOutcome(condition.code, condition.label, EvidenceState.UNKNOWN,
                                (f"{candidate.name}缺少可用于判断“{condition.label}”的可信证据。",))
    verdicts = [_compare(point.value, condition) for point in points]
    if condition.evidence_policy == "worst":
        state = EvidenceState.PASS if all(verdicts) else EvidenceState.FAIL
    elif all(verdicts):
        state = EvidenceState.PASS
    elif not any(verdicts):
        state = EvidenceState.FAIL
    else:
        return ConditionOutcome(
            condition.code, condition.label, EvidenceState.UNKNOWN,
            (f"{candidate.name}关于“{condition.label}”存在相互冲突的有效记录。",), points,
        )
    phrase = "满足" if state is EvidenceState.PASS else "不满足"
    return ConditionOutcome(condition.code, condition.label, state,
                            (f"{candidate.name}{phrase}“{condition.label}”。",), points)


def _combine_condition(
    female: CandidateProfile,
    male: CandidateProfile,
    condition: ConditionSpec,
    target_region: str,
) -> ConditionOutcome:
    female_result = evaluate_candidate_condition(female, condition, target_region)
    male_result = evaluate_candidate_condition(male, condition, target_region)
    if condition.scope is ConditionScope.FEMALE:
        return female_result
    if condition.scope is ConditionScope.MALE:
        return male_result
    if condition.scope is ConditionScope.PAIR:
        return ConditionOutcome(condition.code, condition.label, EvidenceState.UNKNOWN,
                                (f"“{condition.label}”需要针对亲本组合人工核实。",))
    states = (female_result.state, male_result.state)
    if condition.scope is ConditionScope.EVERY_PARENT:
        state = EvidenceState.FAIL if EvidenceState.FAIL in states else (
            EvidenceState.UNKNOWN if EvidenceState.UNKNOWN in states else EvidenceState.PASS
        )
    else:
        state = EvidenceState.PASS if EvidenceState.PASS in states else (
            EvidenceState.UNKNOWN if EvidenceState.UNKNOWN in states else EvidenceState.FAIL
        )
    return ConditionOutcome(
        condition.code,
        condition.label,
        state,
        female_result.reasons + male_result.reasons,
        female_result.evidence + male_result.evidence,
    )


def assess_kinship(
    female: CandidateProfile,
    male: CandidateProfile,
    policy: KinshipPolicy,
) -> KinshipOutcome:
    if female.variety_id == male.variety_id:
        return KinshipOutcome(KinshipLevel.EXCLUDED, ("两个亲本是同一品种。",), (), True)
    left = female.pedigree
    right = male.pedigree
    direct_left = str(male.variety_id) in left.ancestors
    direct_right = str(female.variety_id) in right.ancestors
    if direct_left or direct_right:
        return KinshipOutcome(KinshipLevel.EXCLUDED, ("双亲存在亲本—子代直接关系。",), (), True)
    common = []
    for identity in sorted(set(left.ancestors) & set(right.ancestors)):
        a, b = left.ancestors[identity], right.ancestors[identity]
        if a.depth > policy.depth or b.depth > policy.depth:
            continue
        common.append({
            "identity": identity,
            "name": a.name or b.name,
            "female_depth": a.depth,
            "male_depth": b.depth,
            "female_path": list(a.path),
            "male_path": list(b.path),
            "source_ids": sorted(set(a.source_ids) | set(b.source_ids)),
        })
    common.sort(key=lambda item: (max(item["female_depth"], item["male_depth"]),
                                  item["female_depth"] + item["male_depth"], item["name"]))
    complete = (left.complete_through_depth >= policy.depth and right.complete_through_depth >= policy.depth)
    if common:
        nearest = common[0]
        distance = max(nearest["female_depth"], nearest["male_depth"])
        if distance <= 2 or distance <= policy.exclude_through_depth:
            return KinshipOutcome(
                KinshipLevel.EXCLUDED,
                (f"{distance}代范围内发现共同祖先{nearest['name']}。",), tuple(common), complete,
            )
        return KinshipOutcome(
            KinshipLevel.WARNING,
            (f"{distance}代范围内发现较远共同祖先{nearest['name']}。",), tuple(common), complete,
        )
    if not complete:
        warnings = left.warnings + right.warnings
        return KinshipOutcome(
            KinshipLevel.UNKNOWN,
            (f"系谱未完整覆盖双方{policy.depth}代祖先，不能据此声称无亲缘。", *warnings), (), False,
        )
    return KinshipOutcome(KinshipLevel.LOW, (f"已核验的{policy.depth}代范围内未发现共同祖先。",), (), True)


def _role_compatible(mode: RecommendationMode, female: CandidateProfile, male: CandidateProfile) -> EvidenceState:
    if mode is RecommendationMode.CONVENTIONAL:
        return EvidenceState.PASS
    required_female = ParentRole.STERILE_THREE_LINE if mode is RecommendationMode.HYBRID_THREE_LINE else ParentRole.STERILE_TWO_LINE
    if ParentRole.UNKNOWN in female.roles or ParentRole.UNKNOWN in male.roles:
        return EvidenceState.UNKNOWN
    return EvidenceState.PASS if required_female in female.roles and ParentRole.RESTORER in male.roles else EvidenceState.FAIL


def _serialize_outcome(value: ConditionOutcome, condition: ConditionSpec | None = None) -> dict[str, Any]:
    return {
        "condition_code": value.condition_code,
        "label": value.label,
        "kind": condition.kind.value if condition else "",
        "scope": condition.scope.value if condition else "",
        "state": value.state.value,
        "reasons": list(value.reasons),
        "evidence": [point.__dict__ for point in value.evidence],
    }


_FIVE_TRAIT_DIMENSIONS = (
    ("yield", "丰产", "yield_score"),
    ("stability", "稳产", "stability_score"),
    ("disease", "抗病", "disease_score"),
    ("stress", "耐逆", "stress_score"),
    ("quality", "品质", "quality_score"),
)


def _serialize_five_trait(value: FiveTraitProfile) -> dict[str, Any]:
    dimensions = [
        {"code": code, "label": label, "score": getattr(value, field_name)}
        for code, label, field_name in _FIVE_TRAIT_DIMENSIONS
    ]
    return {
        "dimensions": dimensions,
        "comprehensive_score": value.comprehensive_score,
        "comprehensive_rank": value.comprehensive_rank,
        "scored_dimension_count": value.scored_dimension_count,
        "missing_dimensions": list(value.missing_dimensions),
        "evaluation_status": value.evaluation_status,
        "evaluation_profile_id": value.evaluation_profile_id,
        "evidence_summary": value.evidence_summary,
        "interpretation_boundary": value.interpretation_boundary,
    }


def _pair_five_trait(female: CandidateProfile, male: CandidateProfile) -> dict[str, Any]:
    dimensions = []
    for code, label, field_name in _FIVE_TRAIT_DIMENSIONS:
        female_score = getattr(female.five_trait, field_name)
        male_score = getattr(male.five_trait, field_name)
        if female_score is not None and male_score is not None:
            coverage = "both"
            evidence_text = f"双方均有{label}评价证据。"
        elif female_score is not None:
            coverage = "female_only"
            evidence_text = f"仅{female.name}有{label}评价证据。"
        elif male_score is not None:
            coverage = "male_only"
            evidence_text = f"仅{male.name}有{label}评价证据。"
        else:
            coverage = "missing"
            evidence_text = f"双方均缺少{label}评价证据。"
        dimensions.append({
            "code": code,
            "label": label,
            "female_score": female_score,
            "male_score": male_score,
            "coverage": coverage,
            "evidence_text": evidence_text,
        })
    return {
        "female": {"variety_id": female.variety_id, "name": female.name,
                   **_serialize_five_trait(female.five_trait)},
        "male": {"variety_id": male.variety_id, "name": male.name,
                 **_serialize_five_trait(male.five_trait)},
        "dimensions": dimensions,
        "covered_dimension_count": sum(item["coverage"] != "missing" for item in dimensions),
        "boundary": "组合层面仅对照双亲已有五性证据，不计算后代五性得分或育种成功率。",
    }


class ParentRecommendationEngine:
    """Deep module for candidate pairing, deterministic gates and ranking."""

    VERSION = "ynaas-parent-recommendation-v2.0"

    def recommend(self, request: RecommendationRequest, repository: ParentEvidenceRepository) -> dict[str, Any]:
        candidates = [item for item in repository.candidates(request)
                      if item.variety_id not in set(request.excluded_variety_ids)]
        by_id = {item.variety_id: item for item in candidates}
        missing_pinned = sorted(set(request.pinned_variety_ids) - set(by_id))
        if missing_pinned:
            raise ValueError("必评名单中的品种不在国家水稻数据中心候选结果中：" +
                             "、".join(str(item) for item in missing_pinned))
        if len(candidates) < 2:
            raise ValueError("符合候选来源边界的品种不足 2 个。")

        pairs: list[tuple[CandidateProfile, CandidateProfile]] = []
        if request.mode is RecommendationMode.CONVENTIONAL:
            pairs = list(combinations(candidates, 2))
        else:
            pairs = [(female, male) for female in candidates for male in candidates
                     if female.variety_id != male.variety_id]

        formal: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for female, male in pairs:
            role_state = _role_compatible(request.mode, female, male)
            kinship = assess_kinship(female, male, request.kinship)
            outcomes = [_combine_condition(female, male, condition, request.target_region)
                        for condition in request.conditions]
            hard = [result for condition, result in zip(request.conditions, outcomes)
                    if condition.kind is ConditionKind.HARD]
            preferences = [(condition, result) for condition, result in zip(request.conditions, outcomes)
                           if condition.kind is ConditionKind.PREFERENCE]
            hard_fail = [item for item in hard if item.state is EvidenceState.FAIL]
            hard_unknown = [item for item in hard if item.state is EvidenceState.UNKNOWN]
            exclusion_reasons = [reason for item in hard_fail for reason in item.reasons]
            if role_state is EvidenceState.FAIL:
                exclusion_reasons.append("双亲角色与当前杂交稻推荐模式不兼容。")
            if kinship.level is KinshipLevel.EXCLUDED:
                exclusion_reasons.extend(kinship.reasons)

            known = sum(item.state is not EvidenceState.UNKNOWN for item in outcomes)
            coverage = known / len(outcomes) if outcomes else 0.0
            total_weight = sum(condition.importance for condition, _ in preferences)
            earned = sum(condition.importance for condition, result in preferences
                         if result.state is EvidenceState.PASS)
            target_match = earned / total_weight if total_weight else 0.5
            confidence = "高" if coverage >= .8 and kinship.pedigree_complete else (
                "中" if coverage >= .5 else "低"
            )
            female_contributions = [condition.label for condition, result in preferences
                                    if condition.scope is not ConditionScope.PAIR and
                                    evaluate_candidate_condition(female, condition, request.target_region).state is EvidenceState.PASS]
            male_contributions = [condition.label for condition, result in preferences
                                  if condition.scope is not ConditionScope.PAIR and
                                  evaluate_candidate_condition(male, condition, request.target_region).state is EvidenceState.PASS]
            qualification_reasons = [reason for item in hard if item.state is EvidenceState.PASS
                                     for reason in item.reasons]
            if role_state is EvidenceState.PASS and request.mode is not RecommendationMode.CONVENTIONAL:
                qualification_reasons.append("双亲角色满足当前杂交稻配组模式。")
            if kinship.level is KinshipLevel.LOW:
                qualification_reasons.extend(kinship.reasons)
            record = {
                "female_parent": {"variety_id": female.variety_id, "name": female.name,
                                  "variety_type": female.variety_type, "source_url": female.source_url,
                                  "five_trait": _serialize_five_trait(female.five_trait)},
                "male_parent": {"variety_id": male.variety_id, "name": male.name,
                                "variety_type": male.variety_type, "source_url": male.source_url,
                                "five_trait": _serialize_five_trait(male.five_trait)},
                "target_match": round(target_match, 4),
                "evidence_coverage": round(coverage, 4),
                "confidence": confidence,
                "qualification_reasons": qualification_reasons,
                "female_contributions": female_contributions,
                "male_contributions": male_contributions,
                "recommendation_reasons": [
                    *(f"{female.name}可贡献：{item}" for item in female_contributions),
                    *(f"{male.name}可贡献：{item}" for item in male_contributions),
                ],
                "five_trait_evaluation": _pair_five_trait(female, male),
                "condition_outcomes": [_serialize_outcome(item, condition)
                                       for condition, item in zip(request.conditions, outcomes)],
                "kinship": {
                    "level": kinship.level.value,
                    "reasons": list(kinship.reasons),
                    "common_ancestors": list(kinship.common_ancestors),
                    "pedigree_complete": kinship.pedigree_complete,
                },
                "risks": list(kinship.reasons),
                "data_gaps": [reason for item in hard_unknown for reason in item.reasons],
            }
            if exclusion_reasons:
                record["tier"] = "excluded"
                record["exclusion_reasons"] = exclusion_reasons
                excluded.append(record)
                continue
            if role_state is EvidenceState.UNKNOWN or hard_unknown or (
                request.kinship.require_complete_pedigree and not kinship.pedigree_complete
            ):
                record["tier"] = "pending"
                if role_state is EvidenceState.UNKNOWN:
                    record["data_gaps"].append("亲本角色证据不足，需要人工确认。")
                pending.append(record)
                continue
            tier = "A" if target_match >= .8 and coverage >= .75 and confidence == "高" else (
                "B" if target_match >= .6 and coverage >= .5 else "C"
            )
            record["tier"] = tier
            formal.append(record)

        order = {"A": 0, "B": 1, "C": 2}
        formal.sort(key=lambda item: (order[item["tier"]], -item["target_match"],
                                      -item["evidence_coverage"], item["female_parent"]["name"],
                                      item["male_parent"]["name"]))
        pending.sort(key=lambda item: (-item["target_match"], -item["evidence_coverage"]))
        return {
            "title": "表型与系谱辅助亲本推荐",
            "disclaimer": "结果基于当前国家水稻数据中心证据，不是后代表现预测或育种成功率。",
            "engine_version": self.VERSION,
            "candidate_source": "国家水稻数据中心",
            "mode": request.mode.value,
            "breeding_goal": request.breeding_goal.strip(),
            "target_region": request.target_region,
            "candidate_count": len(candidates),
            "pair_count": len(pairs),
            "formal_count": len(formal),
            "pending_count": len(pending),
            "excluded_count": len(excluded),
            "recommendations": formal[:request.result_limit],
            "backup_recommendations": formal[request.result_limit:request.result_limit + 50],
            "pending_verification": pending[:100],
            "excluded_combinations": excluded[:100],
            "selection_warning": (
                "没有组合进入正式推荐；请查看待核实和已排除原因。" if not formal else ""
            ),
        }


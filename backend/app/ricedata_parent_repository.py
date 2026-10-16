"""PostgreSQL adapter for the deterministic parent recommendation module.

Only ``agent_query.ricedata__*`` read models are queried.  Institute material
tables are deliberately absent from this adapter.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from .parent_recommendation_engine import (
    AncestorPath,
    CandidateProfile,
    ConditionKind,
    ConditionScope,
    EvidencePoint,
    ParentRole,
    PedigreeProfile,
    RecommendationRequest,
)


_SQL_OPERATOR = {"eq": "=", "neq": "<>", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}


def _rows(session: Session, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(sql), params or {}).mappings()]


def classify_roles(variety_type: str, name: str) -> frozenset[ParentRole]:
    value = f"{variety_type} {name}"
    roles: set[ParentRole] = set()
    if "恢复系" in value or "恢复型" in value:
        roles.add(ParentRole.RESTORER)
    if "不育系" in value:
        if "两系" in value or "光温敏" in value:
            roles.add(ParentRole.STERILE_TWO_LINE)
        elif "三系" in value or "细胞质" in value:
            roles.add(ParentRole.STERILE_THREE_LINE)
        else:
            roles.add(ParentRole.UNKNOWN)
    if "常规" in value:
        roles.add(ParentRole.CONVENTIONAL)
    return frozenset(roles or {ParentRole.UNKNOWN})


def variety_attributes(variety_type: str) -> dict[str, str]:
    if "籼粳" in variety_type:
        rice_type = "mixed"
    elif variety_type.startswith("籼"):
        rice_type = "indica"
    elif variety_type.startswith("粳"):
        rice_type = "japonica"
    else:
        rice_type = "unknown"
    if "不育系" in variety_type:
        kind = "sterile"
    elif "常规" in variety_type:
        kind = "inbred"
    elif "杂交" in variety_type:
        kind = "hybrid"
    else:
        kind = "unknown"
    return {"rice_type": rice_type, "variety_kind": kind, "variety_type": variety_type}


def _ancestor_profiles(
    candidate_ids: Sequence[int],
    snapshot_rows: list[dict[str, Any]],
    node_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    requested_depth: int,
) -> dict[int, PedigreeProfile]:
    snapshots = {int(row["variety_id"]): row for row in snapshot_rows}
    nodes_by_snapshot: dict[int, dict[int, dict[str, Any]]] = {}
    for row in node_rows:
        nodes_by_snapshot.setdefault(int(row["pedigree_snapshot_id"]), {})[int(row["pedigree_node_id"])] = row
    edges_by_snapshot: dict[int, list[dict[str, Any]]] = {}
    for row in edge_rows:
        edges_by_snapshot.setdefault(int(row["pedigree_snapshot_id"]), []).append(row)
    result: dict[int, PedigreeProfile] = {}
    for variety_id in candidate_ids:
        snapshot = snapshots.get(int(variety_id))
        if not snapshot:
            result[int(variety_id)] = PedigreeProfile(
                complete_through_depth=0,
                warnings=("国家水稻数据中心尚无已核验系谱快照。",),
            )
            continue
        snapshot_id = int(snapshot["pedigree_snapshot_id"])
        nodes = nodes_by_snapshot.get(snapshot_id, {})
        adjacency: dict[int, list[tuple[int, str]]] = {}
        for edge in edges_by_snapshot.get(snapshot_id, []):
            adjacency.setdefault(int(edge["child_node_id"]), []).append(
                (int(edge["parent_node_id"]), str(edge.get("pedigree_edge_id") or ""))
            )
        roots = [node_id for node_id, row in nodes.items() if row.get("is_root")]
        ancestors: dict[str, AncestorPath] = {}
        queue = deque((root, 0, (str(nodes[root].get("material_name") or variety_id),), ()) for root in roots)
        visited_depth: dict[int, int] = {root: 0 for root in roots}
        unlinked_names: set[str] = set()
        while queue:
            node_id, depth, path, source_ids = queue.popleft()
            if depth >= requested_depth:
                continue
            for parent_id, edge_id in adjacency.get(node_id, []):
                parent = nodes.get(parent_id)
                if not parent:
                    continue
                parent_depth = depth + 1
                name = str(parent.get("material_name") or "").strip()
                linked_id = parent.get("linked_variety_id")
                next_path = (*path, name or "亲本名称待核实")
                next_sources = (*source_ids, edge_id) if edge_id else source_ids
                if linked_id:
                    identity = str(linked_id)
                    existing = ancestors.get(identity)
                    if existing is None or parent_depth < existing.depth:
                        ancestors[identity] = AncestorPath(identity, name, parent_depth, next_path, next_sources)
                elif name and name not in {"0", "?", "？"}:
                    unlinked_names.add(name)
                if parent_depth < requested_depth and (
                    parent_id not in visited_depth or parent_depth < visited_depth[parent_id]
                ):
                    visited_depth[parent_id] = parent_depth
                    queue.append((parent_id, parent_depth, next_path, next_sources))
        fully_expanded = bool(snapshot.get("fully_expanded"))
        max_depth = int(snapshot.get("max_depth") or 0)
        complete = requested_depth if fully_expanded and max_depth >= requested_depth else min(max_depth, requested_depth)
        warnings: list[str] = []
        if not fully_expanded:
            warnings.append("来源系谱尚未完整采集。")
        if max_depth < requested_depth:
            warnings.append(f"来源系谱只覆盖到{max_depth}代，未达到本次{requested_depth}代检查范围。")
        if unlinked_names:
            warnings.append(f"存在{len(unlinked_names)}个未完成全局身份链接的祖先名称，未按同名自动合并。")
        result[int(variety_id)] = PedigreeProfile(ancestors, complete, tuple(warnings))
    return result


class RiceDataParentRepository:
    """Production adapter backed exclusively by RiceData read models."""

    def __init__(self, session: Session):
        self.session = session

    def _candidate_rows(self, request: RecommendationRequest) -> list[dict[str, Any]]:
        self.session.execute(text("SET LOCAL statement_timeout='20s'"))
        clauses = ["TRUE"]
        params: dict[str, Any] = {"limit": request.candidate_limit, "pinned": list(request.pinned_variety_ids) or [-1]}
        score_terms: list[str] = []
        # Only identity conditions are pushed down. Trait gates remain visible
        # to the engine so failures and unknown states are explainable.
        for index, condition in enumerate(request.conditions):
            if condition.kind is not ConditionKind.HARD or condition.scope not in {
                ConditionScope.EVERY_PARENT, ConditionScope.FEMALE, ConditionScope.MALE,
            }:
                continue
            if condition.field == "rice_type" and condition.operator in {"eq", "in"}:
                values = condition.value if isinstance(condition.value, tuple) else (condition.value,)
                patterns = []
                for offset, value in enumerate(values):
                    key = f"rice_type_{index}_{offset}"
                    params[key] = {"indica": "籼%", "japonica": "粳%", "mixed": "籼粳%"}.get(str(value), str(value))
                    patterns.append(f"v.variety_type LIKE :{key}")
                clauses.append("(" + " OR ".join(patterns) + ")")
            elif condition.field == "variety_kind" and condition.operator in {"eq", "in"}:
                values = condition.value if isinstance(condition.value, tuple) else (condition.value,)
                patterns = []
                for offset, value in enumerate(values):
                    key = f"kind_{index}_{offset}"
                    params[key] = {"inbred": "%常规%", "hybrid": "%杂交%", "sterile": "%不育系%"}.get(str(value), str(value))
                    patterns.append(f"v.variety_type LIKE :{key}")
                clauses.append("(" + " OR ".join(patterns) + ")")
        if request.target_region:
            params["target_region"] = request.target_region + "%"
            score_terms.append("CASE WHEN EXISTS (SELECT 1 FROM agent_query.ricedata__rice_variety_approval ar WHERE ar.variety_id=v.variety_id AND ar.approval_region LIKE :target_region) THEN 3 ELSE 0 END")
        for index, condition in enumerate(request.conditions):
            if condition.scope is ConditionScope.PAIR or condition.operator not in {*_SQL_OPERATOR, "between"}:
                continue
            if condition.field in {"rice_type", "variety_kind", "variety_type", "approval_region"}:
                continue
            low_key = f"recall_{index}_low"
            high_key = f"recall_{index}_high"
            if condition.operator == "between":
                if not isinstance(condition.value, tuple) or len(condition.value) != 2:
                    continue
                params[low_key], params[high_key] = float(condition.value[0]), float(condition.value[1])
                comparison = f"candidate_value BETWEEN :{low_key} AND :{high_key}"
            else:
                try:
                    params[low_key] = float(condition.value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                comparison = f"candidate_value {_SQL_OPERATOR[condition.operator]} :{low_key}"
            weight = condition.importance if condition.kind is ConditionKind.PREFERENCE else 3
            if condition.field in {"yield_relative_change_pct", "yield_kg_per_mu"}:
                column = "relative_change_pct" if condition.field == "yield_relative_change_pct" else "yield_kg_per_mu"
                yield_comparison = comparison.replace("candidate_value", f"y.{column}")
                score_terms.append(f"""CASE WHEN EXISTS (
                    SELECT 1 FROM agent_query.ricedata__rice_variety_yield_observation y
                    WHERE y.variety_id=v.variety_id AND y.review_status='accepted'
                      AND {yield_comparison}
                ) THEN {weight} ELSE 0 END""")
            else:
                params[f"recall_trait_{index}"] = condition.field
                score_terms.append(f"""CASE WHEN EXISTS (
                    SELECT 1 FROM (
                      SELECT s.variety_id,COALESCE(m.value_numeric,m.value_min,m.value_max) AS candidate_value
                      FROM agent_query.ricedata__rice_variety_trait_measurement m
                      JOIN agent_query.ricedata__rice_variety_trait_summary s ON s.trait_summary_id=m.trait_summary_id
                      WHERE m.trait_code=:recall_trait_{index}
                      UNION ALL
                      SELECT n.variety_id,COALESCE(n.value_numeric,n.value_min,n.value_max) AS candidate_value
                      FROM agent_query.ricedata__rice_variety_narrative_fact n
                      WHERE n.trait_code=:recall_trait_{index} AND n.review_status='verified'
                    ) fact WHERE fact.variety_id=v.variety_id AND {comparison}
                ) THEN {weight} ELSE 0 END""")
        where = " AND ".join(clauses)
        recall_score = " + ".join(score_terms) if score_terms else "0"
        return _rows(self.session, f"""
            SELECT v.variety_id,v.variety_name,v.variety_type,v.parentage_text,v.source_url,
                   COALESCE(a.latest_year,0) AS latest_year,COALESCE(a.approval_count,0) AS approval_count,
                   ({recall_score}) AS recall_score
            FROM agent_query.ricedata__rice_variety v
            LEFT JOIN LATERAL (
              SELECT max(approval_year) AS latest_year,count(*) AS approval_count
              FROM agent_query.ricedata__rice_variety_approval a WHERE a.variety_id=v.variety_id
            ) a ON TRUE
            WHERE (({where}) OR v.variety_id=ANY(:pinned))
            ORDER BY (v.variety_id=ANY(:pinned)) DESC,recall_score DESC,a.approval_count DESC,a.latest_year DESC NULLS LAST,
                     v.variety_name,v.variety_id
            LIMIT :limit
        """, params)

    def candidates(self, request: RecommendationRequest) -> Sequence[CandidateProfile]:
        base = self._candidate_rows(request)
        ids = [int(row["variety_id"]) for row in base]
        base_by_id = {int(row["variety_id"]): row for row in base}
        if not ids:
            return []
        params = {"ids": ids}
        approvals = _rows(self.session, """
            SELECT approval_id,variety_id,approval_region,approval_year
            FROM agent_query.ricedata__rice_variety_approval WHERE variety_id=ANY(:ids)
            ORDER BY variety_id,approval_year DESC NULLS LAST,approval_id
        """, params)
        measurements = _rows(self.session, """
            SELECT s.variety_id,s.approval_id,m.trait_code,m.value_numeric,m.value_min,m.value_max,
                   m.value_text,m.unit,m.observation_year,m.observation_region,m.trait_measurement_id,
                   a.approval_region,v.source_url
            FROM agent_query.ricedata__rice_variety_trait_measurement m
            JOIN agent_query.ricedata__rice_variety_trait_summary s ON s.trait_summary_id=m.trait_summary_id
            JOIN agent_query.ricedata__rice_variety_approval a ON a.approval_id=s.approval_id
            JOIN agent_query.ricedata__rice_variety v ON v.variety_id=s.variety_id
            WHERE s.variety_id=ANY(:ids)
        """, params)
        narratives = _rows(self.session, """
            SELECT n.variety_id,n.approval_id,n.trait_code,n.value_numeric,n.value_min,n.value_max,
                   n.value_text,n.unit,n.review_status,n.source_excerpt,n.narrative_fact_id,
                   a.approval_region,v.source_url
            FROM agent_query.ricedata__rice_variety_narrative_fact n
            JOIN agent_query.ricedata__rice_variety_approval a ON a.approval_id=n.approval_id
            JOIN agent_query.ricedata__rice_variety v ON v.variety_id=n.variety_id
            WHERE n.variety_id=ANY(:ids)
        """, params)
        yields = _rows(self.session, """
            SELECT y.variety_id,y.approval_id,y.relative_change_pct,y.yield_kg_per_mu,y.trial_year,
                   y.trial_region,y.review_status,y.source_sentence,y.yield_observation_id,v.source_url
            FROM agent_query.ricedata__rice_variety_yield_observation y
            JOIN agent_query.ricedata__rice_variety v ON v.variety_id=y.variety_id
            WHERE y.variety_id=ANY(:ids)
        """, params)
        snapshots = _rows(self.session, """
            SELECT DISTINCT ON (variety_id) pedigree_snapshot_id,variety_id,max_depth,fully_expanded
            FROM agent_query.ricedata__rice_pedigree_snapshot
            WHERE variety_id=ANY(:ids) AND source_verified
            ORDER BY variety_id,crawled_at DESC,pedigree_snapshot_id DESC
        """, params)
        snapshot_ids = [int(row["pedigree_snapshot_id"]) for row in snapshots]
        nodes = _rows(self.session, """
            SELECT pedigree_snapshot_id,pedigree_node_id,material_name,linked_variety_id,is_root
            FROM agent_query.ricedata__rice_pedigree_node WHERE pedigree_snapshot_id=ANY(:ids)
        """, {"ids": snapshot_ids or [-1]})
        edges = _rows(self.session, """
            SELECT pedigree_snapshot_id,pedigree_edge_id,child_node_id,parent_node_id,parent_role
            FROM agent_query.ricedata__rice_pedigree_edge WHERE pedigree_snapshot_id=ANY(:ids)
        """, {"ids": snapshot_ids or [-1]})
        pedigrees = _ancestor_profiles(ids, snapshots, nodes, edges, request.kinship.depth)

        evidence: dict[int, dict[str, list[EvidencePoint]]] = {item: {} for item in ids}
        approval_by_id = {int(row["approval_id"]): row for row in approvals}
        for row in approvals:
            if row.get("approval_region"):
                evidence[int(row["variety_id"])].setdefault("approval_region", []).append(EvidencePoint(
                    "approval_region", str(row["approval_region"]), f"approval:{row['approval_id']}",
                    str(base_by_id[int(row["variety_id"])].get("source_url") or ""),
                    region=str(row.get("approval_region") or ""),
                    year=row.get("approval_year"), review_status="source_verified",
                ))
        for row in measurements:
            value = row.get("value_numeric")
            if value is None:
                value = row.get("value_max") if row.get("value_max") is not None else row.get("value_text")
            if value is None:
                continue
            approval = approval_by_id.get(int(row["approval_id"]), {})
            evidence[int(row["variety_id"])].setdefault(str(row["trait_code"]), []).append(EvidencePoint(
                str(row["trait_code"]), float(value) if not isinstance(value, str) else value,
                f"trait:{row['trait_measurement_id']}", str(row.get("source_url") or ""),
                str(row.get("unit") or ""), str(row.get("observation_region") or approval.get("approval_region") or ""),
                row.get("observation_year") or approval.get("approval_year"), "curated", str(row.get("value_text") or ""),
            ))
        for row in narratives:
            value = row.get("value_numeric")
            if value is None:
                value = row.get("value_max") if row.get("value_max") is not None else row.get("value_text")
            if value is None:
                continue
            approval = approval_by_id.get(int(row["approval_id"]), {})
            evidence[int(row["variety_id"])].setdefault(str(row["trait_code"]), []).append(EvidencePoint(
                str(row["trait_code"]), float(value) if not isinstance(value, str) else value,
                f"narrative:{row['narrative_fact_id']}", str(row.get("source_url") or ""),
                str(row.get("unit") or ""), str(approval.get("approval_region") or ""),
                approval.get("approval_year"), str(row.get("review_status") or "machine_extracted"),
                str(row.get("source_excerpt") or ""),
            ))
        for row in yields:
            review = "accepted" if row.get("review_status") == "accepted" else str(row.get("review_status") or "review")
            common = dict(source_id=f"yield:{row['yield_observation_id']}", source_url=str(row.get("source_url") or ""),
                          unit="%", region=str(row.get("trial_region") or ""), year=row.get("trial_year"),
                          review_status=review, excerpt=str(row.get("source_sentence") or ""))
            if row.get("relative_change_pct") is not None:
                evidence[int(row["variety_id"])].setdefault("yield_relative_change_pct", []).append(
                    EvidencePoint("yield_relative_change_pct", float(row["relative_change_pct"]), **common)
                )
            if row.get("yield_kg_per_mu") is not None:
                evidence[int(row["variety_id"])].setdefault("yield_kg_per_mu", []).append(
                    EvidencePoint("yield_kg_per_mu", float(row["yield_kg_per_mu"]), **{**common, "unit": "公斤/亩"})
                )

        return [CandidateProfile(
            variety_id=int(row["variety_id"]),
            name=str(row["variety_name"]),
            variety_type=str(row.get("variety_type") or ""),
            roles=classify_roles(str(row.get("variety_type") or ""), str(row["variety_name"])),
            attributes=variety_attributes(str(row.get("variety_type") or "")),
            evidence={key: tuple(values) for key, values in evidence[int(row["variety_id"])].items()},
            pedigree=pedigrees[int(row["variety_id"])],
            source_url=str(row.get("source_url") or ""),
        ) for row in base]


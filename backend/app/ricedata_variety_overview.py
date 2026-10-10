"""Approval-scoped, deterministic overview of existing RiceData phenotype records."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ricedata_variety_identity import has_explicit_subject, resolve_varieties
from .ricedata_trait_lookup import _history_context
from .rice_query_presentation import (
    cell, display_value, group_label, number, source_excerpts, table, trait_label,
    trial_label, yield_change, yield_year,
)


_ALL = re.compile(r"全部|所有|完整|全套|都看")
_PHENOTYPE = re.compile(r"表型|农艺|性状|考种|品种.{0,4}数据|的.{0,3}数据")


def wants_variety_overview(question: str, supplement: str = "") -> bool:
    return bool(_PHENOTYPE.search(question) and _ALL.search(question + " " + supplement))


def _rows(session: Session, query: str, params: dict[str, Any]) -> list[dict]:
    return [dict(row) for row in session.execute(text(query), params).mappings()]


def _value(row: dict) -> str:
    return display_value(row)


def _excerpt(value: Any, limit: int) -> str:
    source = str(value or "").strip()
    return source[:limit] + ("…（原文较长，当前仅展示节选）" if len(source) > limit else "")


def lookup_variety_overview(
    session: Session, question: str, *, variety_id: int | None = None,
    history_items: list[Any] | None = None,
) -> dict | None:
    """Return local data only. Never merge observations across approvals."""
    if not wants_variety_overview(question):
        return None
    explicit = resolve_varieties(session, question)
    if explicit:
        variety_id = None
    elif has_explicit_subject(question):
        variety_id = None
    elif variety_id is None:
        if history_items:
            context = _history_context(session, history_items)
            variety_id = context.get("variety_id") if context else None
    varieties = (_rows(session, """
        SELECT variety_id, variety_name, source_variety_id, source_url
        FROM ricedata.rice_variety WHERE variety_id = :variety_id
    """, {"variety_id": variety_id}) if variety_id else explicit)
    if not varieties:
        return {"content": "未找到问题中的品种。请提供完整品种名或国家水稻数据中心品种链接。",
                "evidence": [], "context": {"state": "local_query_reset", "reason": "subject_not_found"}}
    if len(varieties) != 1:
        names = "；".join(f"{row['variety_name']}（{row['source_variety_id']}）" for row in varieties)
        return {"content": f"找到多个可能的品种，请先确认：{names}", "evidence": [], "context": None}
    variety = varieties[0]
    approvals = _rows(session, """
        SELECT approval_id, approval_no, approval_year, approval_region,
               yield_performance_text, suitable_area_text, approval_opinion_text
        FROM ricedata.rice_variety_approval WHERE variety_id = :variety_id
        ORDER BY approval_year NULLS LAST, approval_region, approval_id LIMIT 21
    """, {"variety_id": variety["variety_id"]})
    lines = [f"## {cell(variety['variety_name'])} · 表型资料",
             "数据来源：国家水稻数据中心（系统数据库已收录）。",
             "各审定记录分别展示；审定年份不等于试验或检测年份，未跨记录合并数值。"]
    evidence = []
    source_url = variety.get("source_url")
    if not approvals:
        lines.append("系统数据库未收录该品种的审定记录或表型数值。")
    for approval in approvals[:20]:
        approval_id = approval["approval_id"]
        label = " · ".join(str(part) for part in (
            f"{approval['approval_year']}年" if approval.get("approval_year") else "年份未载明",
            approval.get("approval_region"), approval.get("approval_no")) if part)
        lines.append(f"### {cell(label)}")
        facts = _rows(session, """
            SELECT m.trait_code, m.trait_name, m.value_numeric, m.value_min, m.value_max,
                   m.value_text, m.unit, m.observation_year, m.observation_region, m.source_text
            FROM ricedata.rice_variety_trait_measurement m
            JOIN ricedata.rice_variety_trait_summary s ON s.trait_summary_id = m.trait_summary_id
            WHERE s.approval_id = :approval_id
            ORDER BY m.trait_measurement_id LIMIT 61
        """, {"approval_id": approval_id})
        extras = _rows(session, """
            SELECT trait_code, trait_name, value_numeric, value_min, value_max,
                   value_text, unit, qualifier, source_excerpt AS source_text, source_field,
                   NULL::smallint AS observation_year, NULL::text AS observation_region
            FROM ricedata.rice_variety_narrative_fact
            WHERE approval_id = :approval_id AND review_status IN ('machine_extracted', 'verified')
            ORDER BY narrative_fact_id LIMIT 61
        """, {"approval_id": approval_id})
        all_facts = [*facts, *extras]
        facts, seen = [], set()
        for row in all_facts:
            # Equivalent units are display labels only, not numeric conversions.
            # Keep different values, qualifiers, years and observation regions.
            key = (row["trait_code"], _value(row), row.get("observation_year"), row.get("observation_region"))
            if key not in seen:
                seen.add(key)
                facts.append(row)
        if facts:
            for group in ("农艺性状", "稻米品质", "抗病与耐逆"):
                subset = [f for f in facts[:60] if group_label(f) == group]
                if not subset:
                    continue
                has_year = any(f.get("observation_year") for f in subset)
                has_region = any(f.get("observation_region") for f in subset)
                headers = ["性状", "结果"] + (["观测年份"] if has_year else []) + (["观测地区"] if has_region else [])
                entries = [[trait_label(f), _value(f),
                            *([f.get("observation_year") or "未注明"] if has_year else []),
                            *([f.get("observation_region") or "未注明"] if has_region else [])] for f in subset]
                lines.extend([f"**{group}**", table(headers, entries)])
            if len({f["trait_code"] for f in facts}) < len(facts):
                lines.append("同一性状有多条结果，已保留差异及观测信息，未擅自取平均。")
            if len(facts) > 60:
                lines.append("当前展示前60项结果，其余请指定指标继续查询。")
        else:
            lines.append("该审定记录暂无已结构化的性状数值。")
        yields = _rows(session, """
            SELECT trial_year, covered_years, is_aggregate, trial_type, trial_region,
                   yield_kg_per_mu, control_name, relative_change_pct, review_status, source_sentence
            FROM ricedata.rice_variety_yield_observation
            WHERE approval_id = :approval_id AND yield_kg_per_mu IS NOT NULL
            ORDER BY evidence_order, yield_observation_id LIMIT 21
        """, {"approval_id": approval_id})
        if yields:
            lines.append("**产量表现**")
            has_review = any(item.get("review_status") == "review" for item in yields[:20])
            entries = [[yield_year(item), trial_label(item.get("trial_type")) +
                        (f" · {item['trial_region']}" if item.get("trial_region") else ""),
                        number(item["yield_kg_per_mu"]), item.get("control_name") or "未注明",
                        yield_change(item), *(["待人工核对" if item.get("review_status") == "review" else "—"]
                                             if has_review else [])] for item in yields[:20]]
            lines.append(table(["试验年份", "试验类型", "亩产（公斤/亩）", "对照品种", "较对照变化"] +
                               (["备注"] if has_review else []), entries))
            if len(yields) > 20:
                lines.append("- 其余产量记录请指定年份或试验类型查询。")
        summaries = _rows(session, """
            SELECT characteristics_raw_text
            FROM ricedata.rice_variety_trait_summary
            WHERE approval_id = :approval_id AND characteristics_raw_text IS NOT NULL
            ORDER BY trait_summary_id LIMIT 3
        """, {"approval_id": approval_id})
        if approval.get("suitable_area_text"):
            lines.append("**适宜种植地区**：" + cell(_excerpt(approval["suitable_area_text"], 600)))
        excerpts = source_excerpts([
            ("特征特性原文", summary["characteristics_raw_text"]) for summary in summaries] + [
            ("产量表现原文", approval.get("yield_performance_text")),
            ("适宜地区原文", approval.get("suitable_area_text")),
            ("审定意见原文", approval.get("approval_opinion_text")),
            *[("性状取值依据", f.get("source_text")) for f in all_facts],
            *[("产量取值依据", y.get("source_sentence")) for y in yields],
        ])
        evidence.append({"type": "ricedata_variety", "title": f"{variety['variety_name']} · {label}",
                         "detail": "按本条审定资料整理；自动抽取结果请结合原文核对。",
                         "excerpts": excerpts, "priority": len(evidence) + 1,
                         **({"url": source_url} if source_url else {})})
    if len(approvals) > 20:
        lines.append("\n本页仅展示前20条审定记录；其余记录请按年份、省份或编号查询。")
    if evidence:
        lines.append("原文及来源链接收纳在下方“查看原文与来源”，可展开核对。")
    return {"content": "\n\n".join(lines), "evidence": evidence,
            "context": {"state": "ricedata_variety_context", "variety_id": variety["variety_id"]}}

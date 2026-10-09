"""Approval-scoped, deterministic overview of existing RiceData phenotype records."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ricedata_variety_identity import resolve_varieties
from .ricedata_trait_lookup import _history_context


_ALL = re.compile(r"全部|所有|完整|全套|都看")
_PHENOTYPE = re.compile(r"表型|农艺|性状|考种|品种.{0,4}数据|的.{0,3}数据")


def wants_variety_overview(question: str, supplement: str = "") -> bool:
    return bool(_PHENOTYPE.search(question) and _ALL.search(question + " " + supplement))


def _rows(session: Session, query: str, params: dict[str, Any]) -> list[dict]:
    return [dict(row) for row in session.execute(text(query), params).mappings()]


def _value(row: dict) -> str:
    def number(value: Any) -> str:
        return format(Decimal(str(value)).normalize(), "f")

    numeric = row.get("value_numeric")
    minimum, maximum = row.get("value_min"), row.get("value_max")
    if minimum is not None and maximum is not None:
        value = f"{number(minimum)}～{number(maximum)}"
    elif numeric is not None:
        value = number(numeric)
    else:
        value = str(row.get("value_text") or "").strip()
    return f"{row.get('qualifier') or ''}{value}{row.get('unit') or ''}"


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
    if variety_id is None:
        explicit = resolve_varieties(session, question)
        if not explicit and history_items:
            context = _history_context(session, history_items)
            variety_id = context.get("variety_id") if context else None
    else:
        explicit = []
    varieties = (_rows(session, """
        SELECT variety_id, variety_name, source_variety_id, source_url
        FROM ricedata.rice_variety WHERE variety_id = :variety_id
    """, {"variety_id": variety_id}) if variety_id else explicit)
    if not varieties:
        return {"content": "未找到问题中的品种。请提供完整品种名或国家水稻数据中心品种链接。",
                "evidence": [], "context": None}
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
    lines = [f"## {variety['variety_name']}：本地已收录的表型资料",
             "以下按审定记录分别展示，不能把不同地区、年份的值合并成一个品种固有值。"]
    evidence = []
    source_url = variety.get("source_url")
    if not approvals:
        lines.append("本地未收录该品种的审定记录或表型数值。")
    for approval in approvals[:20]:
        approval_id = approval["approval_id"]
        label = " · ".join(str(part) for part in (
            f"{approval['approval_year']}年" if approval.get("approval_year") else "年份未载明",
            approval.get("approval_region"), approval.get("approval_no")) if part)
        lines.append(f"\n### {label}")
        facts = _rows(session, """
            SELECT m.trait_code, m.trait_name, m.value_numeric, m.value_min, m.value_max,
                   m.value_text, m.unit, m.observation_year, m.source_text
            FROM ricedata.rice_variety_trait_measurement m
            JOIN ricedata.rice_variety_trait_summary s ON s.trait_summary_id = m.trait_summary_id
            WHERE s.approval_id = :approval_id
            ORDER BY m.trait_measurement_id LIMIT 61
        """, {"approval_id": approval_id})
        extras = _rows(session, """
            SELECT trait_code, trait_name, value_numeric, value_min, value_max,
                   value_text, unit, qualifier, source_excerpt AS source_text, source_field
            FROM ricedata.rice_variety_narrative_fact
            WHERE approval_id = :approval_id AND review_status IN ('machine_extracted', 'verified')
            ORDER BY narrative_fact_id LIMIT 61
        """, {"approval_id": approval_id})
        seen = {(row["trait_code"], row.get("value_numeric"), row.get("value_min"),
                 row.get("value_max"), row.get("unit")) for row in facts}
        facts.extend(row for row in extras if (row["trait_code"], row.get("value_numeric"),
                     row.get("value_min"), row.get("value_max"), row.get("unit")) not in seen)
        if facts:
            lines.append("**已结构化性状**（自动抽取项请结合原文核对）：")
            for fact in facts[:60]:
                year = f"（{fact['observation_year']}年）" if fact.get("observation_year") else ""
                excerpt = _excerpt(fact.get("source_text"), 180)
                lines.append(f"- {fact.get('trait_name') or fact['trait_code']}{year}：{_value(fact)}"
                             + (f"；原文：{excerpt}" if excerpt else ""))
            if len(facts) > 60:
                lines.append("- 该记录其余结构化值未在本页展开，请指定指标继续查询。")
        else:
            lines.append("该审定记录暂无已结构化的性状数值。")
        yields = _rows(session, """
            SELECT trial_year, trial_type, yield_kg_per_mu, source_sentence
            FROM ricedata.rice_variety_yield_observation
            WHERE approval_id = :approval_id AND yield_kg_per_mu IS NOT NULL
            ORDER BY evidence_order, yield_observation_id LIMIT 21
        """, {"approval_id": approval_id})
        if yields:
            lines.append("**产量试验记录**：")
            for item in yields[:20]:
                year_label = f"{item['trial_year']}年" if item.get("trial_year") else "年份未明确"
                lines.append(f"- {year_label} · {item['trial_type'] or '试验类型未明确'}："
                             f"{format(Decimal(str(item['yield_kg_per_mu'])).normalize(), 'f')}公斤/亩；"
                             f"原文：{_excerpt(item.get('source_sentence'), 250)}")
            if len(yields) > 20:
                lines.append("- 其余产量记录请指定年份或试验类型查询。")
        if approval.get("yield_performance_text"):
            lines.append("**产量表现原文**：" + _excerpt(approval["yield_performance_text"], 1500))
        summaries = _rows(session, """
            SELECT characteristics_raw_text
            FROM ricedata.rice_variety_trait_summary
            WHERE approval_id = :approval_id AND characteristics_raw_text IS NOT NULL
            ORDER BY trait_summary_id LIMIT 3
        """, {"approval_id": approval_id})
        for summary in summaries:
            lines.append("**特征特性原文**：" + _excerpt(summary["characteristics_raw_text"], 2000))
        if approval.get("suitable_area_text"):
            lines.append("**适宜地区原文**：" + _excerpt(approval["suitable_area_text"], 600))
        if approval.get("approval_opinion_text"):
            lines.append("**审定意见原文**：" + _excerpt(approval["approval_opinion_text"], 800))
        evidence.append({"type": "ricedata_variety", "title": f"{variety['variety_name']} · {label}",
                         "detail": f"审定编号 {approval.get('approval_no') or '未载明'}；"
                                   f"已列出 {min(len(facts), 60)} 项结构化性状。", "priority": 1,
                         **({"url": source_url} if source_url else {})})
    if len(approvals) > 20:
        lines.append("\n本页仅展示前20条审定记录；其余记录请按年份、省份或编号查询。")
    if source_url:
        lines.append(f"\n[国家水稻数据中心原始品种页面]({source_url})")
    return {"content": "\n\n".join(lines), "evidence": evidence,
            "context": {"state": "ricedata_variety_context", "variety_id": variety["variety_id"]}}

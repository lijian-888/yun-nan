"""Authenticated local-only query orchestration; never send institute rows to LLMs."""

from __future__ import annotations

import re
from typing import Any
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .ricedata_trait_facts import TRAIT_BY_CODE, requested_traits
from .ricedata_trait_lookup import _approval_followup, _history_context, lookup_numeric_trait
from .ricedata_variety_identity import has_explicit_subject, resolve_varieties, select_name_matches
from .ricedata_variety_overview import lookup_variety_overview, wants_variety_overview


# Matching names is NOT a cross-source identity merge. Each ID remains scoped.
_QUERY = re.compile(r"数据|资料|信息|表型|特征特性|株高|结实率|粒数|穗长|千粒重|品质|米质|生育期|亩产")
_MATERIAL_ID = re.compile(r"MAT-[A-Za-z0-9]+", re.I)
logger = logging.getLogger(__name__)
_CORE_TRAITS = {
    "seed_setting_rate_pct": ("seed_setting_rate",),
    "thousand_grain_weight_g": ("thousand_grain_weight",),
    "panicle_length_cm": ("panicle_length",),
    "plant_height_cm": ("plant_height",),
    "growth_duration_days": ("growth_duration", "growth_duration_days"),
    "filled_grains_per_panicle": ("filled_grain_count",),
    "grains_per_panicle": ("total_grain_count_per_panicle",),
    "leaf_blast_grade": ("leaf_blast_score",),
}


def source_scope(question: str, default: str = "both") -> str:
    private = bool(re.search(r"院内|农科院|农业科学院", question))
    public = bool(re.search(r"国家水稻数据中心|水稻数据中心|公开(?:数据|库)|RiceData", question, re.I))
    if private and public:
        return "both"
    if private:
        return "institute"
    if public:
        return "public"
    return default


def _rows(session: Session, query: str, params: dict | None = None) -> list[dict]:
    return [dict(row) for row in session.execute(text(query), params or {}).mappings()]


def _latest_state(history: list[Any], name: str) -> dict | None:
    message = next((row for row in history if getattr(row, "role", None) == "assistant"), None)
    states = getattr(message, "operation_state", None) or []
    if any(item.get("state") == "local_query_reset" for item in states):
        return None
    return next((item for item in states if item.get("state") == name), None)


def contains_institute_history(history: list[Any]) -> bool:
    return any(
        any(state.get("state") == "local_data_private" for state in (getattr(item, "operation_state", None) or []))
        or any(card.get("type") == "institute_local" for card in (getattr(item, "evidence", None) or []))
        for item in history)


def _unshadowed_materials(question: str, public: list[dict], private: list[dict]) -> list[dict]:
    combined = [{"display": row["variety_name"],
                 "aliases": [*(row.get("trial_names") or []), *(row.get("former_names") or [])],
                 "source": "public", "record": row} for row in public]
    combined.extend({"display": row["preferred_name"], "aliases": row.get("aliases") or [],
                     "source": "institute", "record": row} for row in private)
    winners = select_name_matches(question, combined, name_key="display", alias_keys=("aliases",), limit=16)
    return [row["record"] for row in winners if row["source"] == "institute"]


def _materials(session: Session, question: str) -> list[dict]:
    identifier = _MATERIAL_ID.search(question)
    if identifier:
        return _rows(session, "SELECT * FROM agent_data.material WHERE material_id=:id",
                     {"id": identifier.group(0).upper()})
    candidates = _rows(session, """
        SELECT m.*, ARRAY(SELECT a.raw_alias FROM agent_data.material_alias a
                          WHERE a.material_id=m.material_id) AS aliases
        FROM agent_data.material m
        WHERE strpos(lower(:q), lower(m.preferred_name)) > 0 OR EXISTS (
            SELECT 1 FROM agent_data.material_alias a WHERE a.material_id=m.material_id
            AND a.raw_alias <> '' AND strpos(lower(:q), lower(a.raw_alias)) > 0)
        ORDER BY char_length(m.preferred_name) DESC, m.material_id LIMIT 100
    """, {"q": question})
    return select_name_matches(question, candidates, name_key="preferred_name", alias_keys=("aliases",))


def _core_answer(session: Session, material: dict, question: str, traits: list) -> dict:
    mid, name = material["material_id"], material["preferred_name"]
    context = {"state": "local_material_context", "material_id": mid,
               "trait_codes": [spec.code for spec in traits]}
    if material.get("needs_review"):
        return {"content": f"院内治理库找到 **{name}（{mid}）**，但材料身份标记为待复核。"
                           "请先确认身份，当前不将其表型作为已确认的品种数值。",
                "evidence": [], "context": context}
    conditions = ""
    params: dict = {"id": mid}
    if traits:
        codes = list({code for spec in traits for code in _CORE_TRAITS.get(spec.code, (spec.code,))})
        conditions = " AND trait_code=ANY(CAST(:codes AS text[]))"
        params["codes"] = codes
    # Remove material IDs/names before interpreting an explicit observation year.
    selector = question.replace(mid, "").replace(name, "")
    years = re.findall(r"(?<!\d)((?:19|20)\d{2})(?:年)?(?!\d)", selector)
    if years:
        conditions += " AND (trial_year=ANY(CAST(:years AS integer[])) OR EXTRACT(YEAR FROM observed_on)=ANY(CAST(:years AS integer[])))"
        params["years"] = list(map(int, years))
    observations = _rows(session, "SELECT * FROM agent_data.phenotype WHERE material_id=:id" + conditions +
                         " ORDER BY trial_year NULLS LAST, measurement_event_id, phenotype_value_id LIMIT 61", params)
    lines = [f"### 院内治理数据：{name}（{mid}）",
             "以下是院内观测，不是省级审定结果；按来源记录展示，不与公开审定数值合并或取平均。"]
    if not observations:
        lines.append("院内该材料未收录所查询指标的观测记录。" if traits else "院内该材料暂未收录表型观测。")
    for row in observations[:60]:
        # Counts of subsamples/whole plants are not automatically per-panicle counts.
        scope_note = ""
        if any(spec.code == "filled_grains_per_panicle" for spec in traits) and "穗" not in str(row["raw_header"]):
            scope_note = "；原字段未明确每穗口径，不能直接认定为每穗实粒数"
        value = row["raw_value"] or row["value_text"] or (
            str(row["value_numeric"]) if row["value_numeric"] is not None else "未提供数值")
        when = str(row["observed_on"] or row["trial_year"] or "观测年份未明确")
        status = "；该数值待复核，不作为确认值" if row["quality_status"] != "accepted" else ""
        lines.append(f"- **{row['raw_header']}：{value}**（原单位：{row['unit'] or '未注明'}）；"
                     f"年份/日期：{when}；地点：{row['observation_location'] or '未注明'}；"
                     f"来源文件编号：{row['source_file_id']}，工作表：{row['sheet_name']}，行：{row['row_number']}；"
                     f"观测记录：{row['phenotype_value_id']}{scope_note}{status}。")
    if len(observations) > 60:
        lines.append("记录超过60条，当前展示前60条；请指定观测年份或原始文件编号缩小范围。")
    return {"content": "\n\n".join(lines), "context": context,
            "evidence": [{"type": "institute_local", "title": f"院内治理数据 · {name} · {mid}",
                          "detail": f"仅在系统内查询；展示{min(len(observations), 60)}条原始观测，未发送外部模型。", "priority": 1}]}


def lookup_local_variety_data(session: Session, question: str, history: list[Any] | None = None,
                              *, institute_enabled: bool = False, variety_id: int | None = None) -> dict | None:
    """Compose separate sources or ask for identity; DB faults never become not-found.

    Runs inside a savepoint so a failed read does not poison chat persistence.
    Only the already authenticated institute chat route calls this function.
    """
    history = history or []
    if not (_QUERY.search(question) or requested_traits(question) or _approval_followup(question)):
        return None
    if re.search(r"推荐|综合评价|排名|预测|筛选|哪些品种|所有品种|天气", question):
        return None
    if re.search(r"基因|测序|基因组|位点|QTL|GWAS", question, re.I) and not requested_traits(question):
        return None
    prior = _latest_state(history, "local_source_context")
    scope = source_scope(question, (prior or {}).get("scope", "both"))
    traits = requested_traits(question)
    public_result = None
    institute_result = None
    source_errors = []
    public_matches = []
    core_matches = []
    explicit = has_explicit_subject(question)
    if scope != "institute":
        try:
            with session.begin_nested():
                public_matches = resolve_varieties(session, question)
                public_result = lookup_variety_overview(session, question, variety_id=variety_id, history_items=history)
                if public_result is None:
                    public_result = lookup_numeric_trait(session, question, history)
                if public_result is None and public_matches:
                    # A named basic profile is still a grounded local query, not free-form generation.
                    public_result = lookup_variety_overview(session, question + " 全部表型数据", history_items=history)
        except SQLAlchemyError as exc:
            logger.warning("Public local query unavailable: %s", type(exc).__name__)
            source_errors.append("公开数据库暂时无法查询，不能据此认定数据不存在。")
    if scope != "public":
        if not institute_enabled:
            source_errors.append("院内查询尚未启用，本次未检索院内数据。")
        else:
            try:
                with session.begin_nested():
                    core_matches = _materials(session, question)
                    if public_matches and core_matches and not _MATERIAL_ID.search(question):
                        core_matches = _unshadowed_materials(question, public_matches, core_matches)
                    core_context = _latest_state(history, "local_material_context")
                    if not core_matches and not explicit and core_context:
                        core_matches = _rows(session, "SELECT * FROM agent_data.material WHERE material_id=:id",
                                             {"id": core_context["material_id"]})
                    if len(core_matches) == 1:
                        core_traits = traits
                        if not traits and not explicit and core_context:
                            core_traits = [TRAIT_BY_CODE[code] for code in core_context.get("trait_codes", [])
                                           if code in TRAIT_BY_CODE]
                        institute_result = _core_answer(session, core_matches[0], question, core_traits)
                    elif len(core_matches) > 1:
                        institute_result = {"content": "院内找到多个材料，请使用材料编号确认：\n\n" +
                            "\n".join(f"- {m['preferred_name']}（{m['material_id']}）" for m in core_matches),
                            "evidence": [], "context": {"state": "local_query_reset", "reason": "ambiguous_material"}}
            except SQLAlchemyError as exc:
                logger.warning("Institute local query unavailable: %s", type(exc).__name__)
                source_errors.append("院内数据库暂时无法查询，不能据此认定数据不存在。")
    if public_matches and core_matches and not _MATERIAL_ID.search(question):
        # The same name in two datasets does not prove the same biological identity.
        options = ([{"label": f"公开品种：{m['variety_name']}（{m['source_variety_id']}）",
                     "answer": f"只查国家水稻数据中心 {m['source_url']}的" +
                               ("、".join(t.name for t in traits) or "全部表型数据")} for m in public_matches] +
                   [{"label": f"院内材料：{m['preferred_name']}（{m['material_id']}）",
                     "answer": f"只查院内 {m['material_id']}的" +
                               ("、".join(t.name for t in traits) or "全部表型数据")} for m in core_matches])
        return {"content": "两个来源均找到材料，但尚未确认是同一身份。请先选择查询对象；不会仅凭同名合并。",
                "evidence": [], "context": {"state": "local_query_reset", "reason": "cross_source_identity"},
                "contains_private": True,
                "pending": {"state": "local_identity_clarification", "options": options}}
    results = []
    if public_result:
        results.append(("国家水稻数据中心", public_result))
    if institute_result:
        results.append(("云南农科院院内治理库", institute_result))
    if not results:
        if not explicit and not traits and not source_errors:
            return None
        return {"content": "\n\n".join(source_errors) if source_errors else
                "未找到可确认的品种或材料。请提供完整名称、院内材料编号或品种详情页链接。",
                "evidence": [], "contains_private": scope == "institute",
                "context": {"state": "local_query_reset", "reason": "subject_not_found"}}
    content = (results[0][1]["content"] if len(results) == 1 else
               "\n\n---\n\n".join(f"## {name}\n\n{result['content']}" for name, result in results))
    scope_evidence = []
    if scope == "both" and not core_matches and institute_enabled and not any("院内数据库" in e for e in source_errors):
        scope_evidence.append({"type": "query_scope", "title": "查询范围", "priority": len(results) + 1,
                               "detail": "已检索公开品种库及院内治理库；院内未找到可确认的对应材料，未按同名自动合并。"})
    if source_errors:
        content += "\n\n" + "\n".join(source_errors)
    contexts = [result["context"] for _, result in results if result.get("context")
                and result["context"].get("state") != "local_query_reset"]
    return {"content": content, "evidence": [card for _, result in results for card in result.get("evidence", [])] + scope_evidence,
            "contains_private": bool(core_matches) or scope == "institute",
            "context": contexts[0] if len(contexts) == 1 else {"state": "local_query_reset", "reason": "multiple_sources"},
            "extra_contexts": [{"state": "local_source_context", "scope": scope}],
            "pending": next((r["pending"] for _, r in results if r.get("pending")), None)}

"""Approval-aware, source-grounded answers to specific RiceData trait questions."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ricedata_trait_facts import TRAIT_BY_CODE, requested_traits
from .ynaas_reference import _explicit_name_mention


_SOURCE_ID = re.compile(r"ricedata\.cn/variety/varis/(\d+)\.htm", re.I)
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_REGIONS = {
    "四川": ("四川", "川审"), "福建": ("福建", "闽审"), "云南": ("云南", "滇审"),
    "江苏": ("江苏", "苏审"), "浙江": ("浙江", "浙审"), "安徽": ("安徽", "皖审"),
    "江西": ("江西", "赣审"), "湖北": ("湖北", "鄂审"), "湖南": ("湖南", "湘审"),
    "广东": ("广东", "粤审"), "广西": ("广西", "桂审"), "海南": ("海南", "琼审"),
    "贵州": ("贵州", "黔审"), "重庆": ("重庆", "渝审"), "河南": ("河南", "豫审"),
    "山东": ("山东", "鲁审"), "辽宁": ("辽宁", "辽审"), "吉林": ("吉林", "吉审"),
    "黑龙江": ("黑龙江", "黑审"), "上海": ("上海", "沪审"), "河北": ("河北", "冀审"),
}


def _rows(session: Session, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(statement), params).mappings().all()]


def _number(value: Any) -> str:
    if value is None:
        return ""
    return format(Decimal(str(value)).normalize(), "f")


def _approval_label(approval: dict) -> str:
    parts = [str(approval["approval_year"]) + "年" if approval.get("approval_year") else "年份未载明"]
    if approval.get("approval_region"):
        parts.append(str(approval["approval_region"]))
    if approval.get("approval_no"):
        parts.append(str(approval["approval_no"]))
    return " · ".join(parts)


def _find_varieties(session: Session, question: str) -> list[dict]:
    source_id = _SOURCE_ID.search(question)
    if source_id:
        return _rows(session, """
            SELECT variety_id, variety_name, source_variety_id, source_url
            FROM ricedata.rice_variety WHERE source_variety_id = :source_id LIMIT 2
        """, {"source_id": source_id.group(1)})
    possible = _rows(session, """
        SELECT variety_id, variety_name, source_variety_id, source_url,
               trial_names, former_names
        FROM ricedata.rice_variety
        WHERE variety_name IS NOT NULL AND (
            strpos(lower(:question), lower(variety_name)) > 0 OR
            EXISTS (SELECT 1 FROM unnest(COALESCE(trial_names, ARRAY[]::text[]) ||
                       COALESCE(former_names, ARRAY[]::text[])) AS alias_name
                    WHERE alias_name <> '' AND strpos(lower(:question), lower(alias_name)) > 0)
        )
        ORDER BY char_length(variety_name) DESC, variety_id LIMIT 80
    """, {"question": question})
    matched = [row for row in possible if any(
        _explicit_name_mention(question, name)
        for name in (row["variety_name"], *(row.get("trial_names") or []), *(row.get("former_names") or []))
    )]
    if not matched:
        return []
    # A longer explicit cultivar name wins over an embedded shorter name.
    longest = max(len(row["variety_name"]) for row in matched)
    return [row for row in matched if len(row["variety_name"]) == longest][:8]


def _approval_selection(question: str, approvals: list[dict]) -> list[dict]:
    exact = [item for item in approvals if item.get("approval_no") and item["approval_no"] in question]
    if exact:
        return exact
    years = set(_YEAR.findall(question))
    region_hints = {region for region, aliases in _REGIONS.items() if any(alias in question for alias in aliases)}
    if not years and not region_hints:
        return []
    result = approvals
    if years:
        result = [item for item in result if str(item.get("approval_year")) in years]
    if region_hints:
        result = [item for item in result if any(
            region in str(item.get("approval_region") or "") or
            any(alias in str(item.get("approval_no") or "") for alias in _REGIONS[region])
            for region in region_hints
        )]
    return result


def _outcome(
    content: str,
    *,
    evidence: list[dict] | None = None,
    pending: dict | None = None,
    context: dict | None = None,
) -> dict:
    return {"content": content, "evidence": evidence or [], "pending": pending, "context": context}


def _approval_followup(question: str) -> bool:
    """Recognize short edits to an approval selector, not broad province questions."""
    compact = re.sub(r"[\s？?。！!，,]+", "", question)
    if not compact or len(compact) > 40:
        return False
    if any(word in compact for word in ("哪些", "所有品种", "适宜种植", "种什么", "推荐", "比较", "报告", "怎么", "如何", "为什么", "有什么", "介绍")):
        return False
    region_or_year = bool(_YEAR.search(compact)) or any(
        alias in compact for aliases in _REGIONS.values() for alias in aliases
    )
    ordinal = bool(re.fullmatch(r"(?:第)?[一二三四五六七八九十\d]+(?:条|个)?", compact))
    if not (region_or_year or ordinal):
        return False
    return bool(
        ordinal or compact.startswith(("那", "换", "改", "如果", "再看", "看下", "查", "请看"))
        or compact.endswith(("呢", "的", "结果", "审定", "省定"))
        or bool(re.fullmatch(r"(?:19|20)\d{2}年?", compact))
        or any(compact in {alias, alias + "省"} or compact.startswith(alias + "20")
               for aliases in _REGIONS.values() for alias in aliases)
        or bool(_YEAR.match(compact) and len(compact) <= 20 and any(
            alias in compact for aliases in _REGIONS.values() for alias in aliases
        ))
    )


def _history_context(session: Session, history_items: list[Any]) -> dict | None:
    """Read structured state from the last answer; recover pre-upgrade answers once."""
    last_assistant = next((item for item in history_items if getattr(item, "role", None) == "assistant"), None)
    if not last_assistant:
        return None
    states = getattr(last_assistant, "operation_state", None) or []
    for state in states:
        if state.get("state") in {"ricedata_trait_context", "ricedata_trait_clarification"}:
            code = state.get("trait_code")
            variety_id = state.get("variety_id")
            if code in TRAIT_BY_CODE and isinstance(variety_id, int):
                return {"variety_id": variety_id, "trait_code": code,
                        "approval_id": state.get("approval_id")}
    # Compatibility for deterministic answers persisted before per-turn state
    # was introduced. Never infer context from a generative model answer.
    if not any(state.get("state") == "completed" and
               state.get("label") == "已按品种、审定记录和原始文本完成数据库查询" for state in states):
        return None
    headline = str(getattr(last_assistant, "content", "") or "").splitlines()[0]
    specs = requested_traits(headline)
    varieties = _find_varieties(session, headline) if len(specs) == 1 else []
    if len(specs) != 1 or len(varieties) != 1:
        return None
    variety = varieties[0]
    approvals = _rows(session, """
        SELECT approval_id, approval_no, approval_year, approval_region
        FROM ricedata.rice_variety_approval WHERE variety_id = :variety_id
    """, {"variety_id": variety["variety_id"]})
    selected = _approval_selection(headline.replace(str(variety["variety_name"]), ""), approvals)
    return {"variety_id": variety["variety_id"], "trait_code": specs[0].code,
            "approval_id": selected[0]["approval_id"] if len(selected) == 1 else None}


def lookup_numeric_trait(
    session: Session,
    question: str,
    history_items: list[Any] | None = None,
) -> dict | None:
    """Return None only when this is not a specific numeric trait request.

    Follow-up turns use persisted lookup state, not model memory.
    Every value is scoped to one approval; a missing approval value stays missing.
    """
    history_items = history_items or []
    if any(word in question for word in ("比较", "推荐", "综合", "排名", "预测", "报告", "筛选", "哪些品种", "所有品种")):
        return None
    specs = requested_traits(question)
    followup = _approval_followup(question)
    if not specs and not followup:
        return None
    context = _history_context(session, history_items) if history_items else None
    if not specs and not context:
        return _outcome("请提供要查询的品种和具体指标；仅凭省份无法确定您想查哪条数据。")
    if len(specs) > 1:
        names = "、".join(spec.name for spec in specs)
        return _outcome(f"您提到了多个指标（{names}）。请先指定要查询哪一个具体指标；我会按审定记录给出原始数值。")
    spec = specs[0] if specs else TRAIT_BY_CODE.get(context["trait_code"]) if context else None
    if not spec:
        return None
    varieties = [] if followup and not specs else _find_varieties(session, question)
    explicit_variety = bool(varieties)
    if not varieties and context:
        varieties = _rows(session, """
            SELECT variety_id, variety_name, source_variety_id, source_url
            FROM ricedata.rice_variety WHERE variety_id = :variety_id
        """, {"variety_id": context["variety_id"]})
    if not varieties:
        return _outcome(f"未找到问题中的品种，无法查询“{spec.name}”。请提供完整品种名或国家水稻数据中心品种链接。")
    if len(varieties) > 1:
        return _outcome("找到多个同名或相近品种，请提供品种详情页链接或源品种编号：" +
                        "；".join(f"{v['variety_name']}（{v['source_variety_id']}）" for v in varieties))
    variety = varieties[0]
    approvals = _rows(session, """
        SELECT approval_id, approval_no, approval_year, approval_region
        FROM ricedata.rice_variety_approval
        WHERE variety_id = :variety_id
        ORDER BY approval_year, approval_region, approval_id
    """, {"variety_id": variety["variety_id"]})
    if not approvals:
        return _outcome(f"找到品种 {variety['variety_name']}，但本地没有审定记录，无法核对“{spec.name}”的具体值。",
                        context={"state": "ricedata_trait_context", "variety_id": variety["variety_id"],
                                 "trait_code": spec.code, "approval_id": None})
    # A province/year that happens to be part of a cultivar name is not an
    # approval selector (e.g. a cultivar whose name ends in 2015).
    selection_question = question.replace(str(variety["variety_name"]), "")
    selected = _approval_selection(selection_question, approvals)
    if context and not selected:
        ordinal = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*(?:条|个)|^\s*(\d+)\s*$", selection_question)
        if ordinal:
            digit = ordinal.group(1) or ordinal.group(2)
            index = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                     "七": 7, "八": 8, "九": 9, "十": 10}.get(digit)
            index = index or (int(digit) if digit.isdigit() else 0)
            if 1 <= index <= len(approvals):
                selected = [approvals[index - 1]]
    if not selected and context and not explicit_variety and not followup:
        selected = [item for item in approvals if item["approval_id"] == context.get("approval_id")]
    if len(approvals) == 1 and not selected and (
        _YEAR.search(selection_question) or any(alias in selection_question for aliases in _REGIONS.values() for alias in aliases)
    ):
        return _outcome(
            f"未找到与您指定的年份或省份相符的审定记录。{variety['variety_name']}在本地可用的记录是："
            f"{_approval_label(approvals[0])}。请核对后再查询{spec.name}。",
            context={"state": "ricedata_trait_context", "variety_id": variety["variety_id"],
                     "trait_code": spec.code, "approval_id": None},
        )
    if len(approvals) > 1 and len(selected) != 1:
        prefix = "未能唯一确定您说的审定记录。" if selected else "该品种有多条审定记录，指标值可能不同。"
        options = selected or approvals
        labels = "\n".join(f"{index}. {_approval_label(item)}" for index, item in enumerate(options, 1))
        return _outcome(
            f"{prefix}请问您要查询 **{variety['variety_name']}的{spec.name}** 对应哪条审定记录？\n\n{labels}\n\n"
            "请回复年份、省份或审定编号。即使某条记录没有该指标，也会明确告诉您未收录。",
            pending={"state": "ricedata_trait_clarification", "variety_id": variety["variety_id"],
                     "trait_code": spec.code},
            context={"state": "ricedata_trait_context", "variety_id": variety["variety_id"],
                     "trait_code": spec.code, "approval_id": None},
        )
    approval = selected[0] if selected else approvals[0]
    resolved_context = {"state": "ricedata_trait_context", "variety_id": variety["variety_id"],
                        "trait_code": spec.code, "approval_id": approval["approval_id"]}
    if spec.code == "yield_kg_per_mu":
        observations = _rows(session, """
            SELECT yield_kg_per_mu, trial_year, covered_years, trial_type,
                   trial_region, source_sentence, review_status, warnings
            FROM ricedata.rice_variety_yield_observation
            WHERE approval_id = :approval_id AND yield_kg_per_mu IS NOT NULL
            ORDER BY evidence_order, yield_observation_id LIMIT 30
        """, {"approval_id": approval["approval_id"]})
        label = _approval_label(approval)
        source_url = variety.get("source_url")
        if not observations:
            return _outcome(f"**{variety['variety_name']} · {label}**：本地这条审定记录未收录可核对的亩产值。",
                            context=resolved_context)
        lines = []
        evidence = []
        trial_labels = {"regional_trial": "区域试验", "production_trial": "生产试验",
                        "variety_comparison": "品种比较试验"}
        for item in observations:
            year = f"{item['trial_year']}年" if item.get("trial_year") else (
                "覆盖" + "/".join(map(str, item.get("covered_years") or [])) + "年（具体对应年份待核）"
                if item.get("covered_years") else "年份未明确"
            )
            trial = trial_labels.get(item.get("trial_type"), item.get("trial_type") or "试验类型未明确")
            warning = "；原抽取标记待核" if item.get("review_status") == "review" else ""
            value = _number(item["yield_kg_per_mu"])
            excerpt = str(item.get("source_sentence") or "").strip()
            lines.append(f"- **{value}公斤/亩**；{year}；{trial}{warning}。原文：{excerpt}")
            evidence.append({"type": "ricedata_trait", "title": f"{variety['variety_name']} · {label} · 亩产",
                             "detail": f"{value}公斤/亩；{year}；{trial}；{excerpt[:250]}", "priority": 1,
                             **({"url": source_url} if source_url else {})})
        note = "\n\n该审定记录包含多个产量值；请结合试验年份和试验类型辨认，待核项不可当作最终精确结论。" if len(observations) > 1 else ""
        link = f"\n\n[国家水稻数据中心原始品种页面]({source_url})" if source_url else ""
        return _outcome(f"**{variety['variety_name']} · {label}的亩产记录**：\n\n" + "\n".join(lines) + note + link,
                        evidence=evidence, context=resolved_context)
    measurements = _rows(session, """
        SELECT m.value_numeric, m.value_min, m.value_max, m.value_text, m.unit,
               m.observation_year, m.observation_region, m.source_text,
               '特征特性' AS source_field
        FROM ricedata.rice_variety_trait_measurement m
        JOIN ricedata.rice_variety_trait_summary s ON s.trait_summary_id = m.trait_summary_id
        WHERE s.approval_id = :approval_id AND m.trait_code = :trait_code
        ORDER BY m.observation_year NULLS LAST, m.trait_measurement_id LIMIT 20
    """, {"approval_id": approval["approval_id"], "trait_code": spec.code})
    supplemental = _rows(session, """
        SELECT value_numeric, value_min, value_max, value_text, unit,
               NULL::smallint AS observation_year, NULL::text AS observation_region,
               source_excerpt AS source_text, source_field, qualifier
        FROM ricedata.rice_variety_narrative_fact
        WHERE approval_id = :approval_id AND trait_code = :trait_code
          AND review_status IN ('machine_extracted', 'verified')
        ORDER BY narrative_fact_id LIMIT 20
    """, {"approval_id": approval["approval_id"], "trait_code": spec.code})
    # Prefer the curated trait table when the supplemental source repeats it.
    seen = {(row["value_numeric"], row["value_min"], row["value_max"], row["unit"])
            for row in measurements}
    facts = measurements + [row for row in supplemental if
                            (row["value_numeric"], row["value_min"], row["value_max"], row["unit"]) not in seen]
    label = _approval_label(approval)
    source_url = variety.get("source_url")
    if not facts:
        raw_sections = _rows(session, """
            SELECT a.yield_performance_text, a.cultivation_text,
                   a.suitable_area_text, a.approval_opinion_text,
                   s.characteristics_raw_text
            FROM ricedata.rice_variety_approval a
            LEFT JOIN ricedata.rice_variety_trait_summary s ON s.approval_id = a.approval_id
            WHERE a.approval_id = :approval_id LIMIT 5
        """, {"approval_id": approval["approval_id"]})
        possible_raw: list[str] = []
        for section in raw_sections:
            for field, raw in section.items():
                if not raw:
                    continue
                for alias in sorted(spec.aliases, key=len, reverse=True):
                    at = raw.find(alias)
                    if at >= 0:
                        source_name = {"yield_performance_text": "产量表现",
                                       "characteristics_raw_text": "特征特性",
                                       "cultivation_text": "栽培技术要点",
                                       "suitable_area_text": "适宜地区",
                                       "approval_opinion_text": "审定意见"}.get(field, field)
                        possible_raw.append(f"{source_name}：{raw[max(0, at - 35):at + 130].strip()}")
                        break
        if possible_raw:
            detail = ("原文提到了该指标，但自动抽取未得到可靠的结构化数值；请人工核对以下原文，"
                      "不能把其中疑似数字直接当作已确认结果：\n\n" +
                      "\n".join(f"- {item}" for item in possible_raw[:3]))
        else:
            detail = "本地该条审定记录的已结构化数据及已保存原文中均未检索到该指标数值。"
        return _outcome(
            f"**{variety['variety_name']} · {label}**：未找到“{spec.name}”的已结构化可核对数值。"
            f"\n\n{detail}\n\n不能用其他省份或年份的数值代替。" +
            (f"\n\n[查看原始品种页面]({source_url})" if source_url else ""),
            evidence=[{"type": "ricedata_trait", "title": label,
                       "detail": "原文待人工核对。" if possible_raw else "该审定记录未检索到该指标；未跨审定记录借值。",
                       "priority": 1}],
            context=resolved_context,
        )
    lines: list[str] = []
    evidence: list[dict] = []
    for fact in facts:
        raw_value = (f"{_number(fact['value_min'])}～{_number(fact['value_max'])}"
                     if fact.get("value_min") is not None and fact.get("value_max") is not None
                     else _number(fact.get("value_numeric")) or str(fact.get("value_text") or "").strip())
        qualifier = str(fact.get("qualifier") or "")
        if not qualifier and ("左右" in str(fact.get("value_text") or "") or "约" in str(fact.get("value_text") or "")):
            qualifier = "约"
        when = f"（{fact['observation_year']}年检测）" if fact.get("observation_year") else ""
        unit = fact.get("unit") or spec.unit
        value = f"{qualifier}{raw_value}{unit}"
        source_field = {"yield_performance_text": "产量表现", "cultivation_text": "栽培技术要点",
                        "suitable_area_text": "适宜地区", "approval_opinion_text": "审定意见",
                        "variety_source_text": "品种来源"}.get(fact.get("source_field"), "特征特性")
        excerpt = str(fact.get("source_text") or "").strip()
        lines.append(f"- **{value}**{when}，来源：{source_field}。原文：{excerpt}")
        evidence.append({"type": "ricedata_trait", "title": f"{variety['variety_name']} · {label} · {spec.name}",
                         "detail": f"{value}；{source_field}；{excerpt[:250]}", "priority": 1,
                         **({"url": source_url} if source_url else {})})
    note = "\n\n同一审定记录出现多个检测值，已逐条列出，未擅自取平均。" if len(facts) > 1 else ""
    link = f"\n\n[国家水稻数据中心原始品种页面]({source_url})" if source_url else ""
    return _outcome(f"**{variety['variety_name']} · {label}的{spec.name}**：\n\n" + "\n".join(lines) + note + link,
                    evidence=evidence, context=resolved_context)

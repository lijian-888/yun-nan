"""Approval-scoped final-answer guard for specific public numeric fact queries.

This complements, rather than replaces, source retrieval and human review.
It fails closed when a model moves a real measurement to another approval.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .research_agent import ResearchAgentError
from .ricedata_trait_facts import TRAIT_BY_CODE
from .ricedata_trait_lookup import _REGIONS


_MEASURE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(%|％|厘米|cm|毫米|mm|天|粒|公斤|千克|kg|克|g)(?![A-Za-z])", re.I)
_RANGE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[～~—-]\s*(\d+(?:\.\d+)?)\s*(%|％|厘米|cm|毫米|mm|天|粒|公斤|千克|kg|克|g)", re.I)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_UNIT = {"％": "%", "厘米": "cm", "毫米": "mm", "公斤": "kg", "千克": "kg", "克": "g"}
_SPLIT = re.compile(r"[，,。；;\n|]")


def _values(value: str) -> set[tuple[Decimal, str]]:
    found = {(Decimal(number), _UNIT.get(unit.lower(), unit.lower()))
             for number, unit in _MEASURE.findall(value)}
    for start, _, unit in _RANGE.findall(value):
        found.add((Decimal(start), _UNIT.get(unit.lower(), unit.lower())))
    return found


def _nearest_trait(clause: str, before: int) -> str | None:
    hits = []
    for spec in TRAIT_BY_CODE.values():
        for alias in spec.aliases:
            at = clause.rfind(alias, 0, before)
            if at >= 0:
                hits.append((at + len(alias), len(alias), spec.code))
    return max(hits)[2] if hits else None


def validate_scoped_fact_answer(answer: str, scope: dict | None) -> None:
    """Check each measured value against its trait and approval, not just evidence text.

    Unsupported free-form layouts are rejected for a retry, never "fixed" by
    silently changing an approval label or substituting another record's value.
    """
    if not scope or scope.get("state") != "numeric_fact_scope":
        return
    approvals = {row["approval_id"]: row for row in scope.get("approvals") or []}
    bindings = []
    for item in scope.get("bindings") or []:
        for value in _values(str(item.get("value") or "")):
            bindings.append((value, item))
    bound_units = {value[1] for value, _ in bindings}
    if bindings and not _MEASURE.search(answer):
        raise ResearchAgentError("模型未回答已查到的指标数值，本轮未展示或保存回答，请重试。")
    for match in _MEASURE.finditer(answer):
        value = (Decimal(match.group(1)), _UNIT.get(match.group(2).lower(), match.group(2).lower()))
        # The generic evidence guard has already checked all numbers. This
        # scoped guard only owns the queried metric's unit: a sourced relative
        # yield change in % must not invalidate a kg/mu yield answer.
        if bindings and value[1] not in bound_units:
            continue
        candidates = [item for candidate_value, item in bindings if candidate_value == value]
        if not candidates:
            raise ResearchAgentError("模型回答中的指标数值未绑定到本轮所查审定记录，本轮未展示或保存回答，请重试。")
        start = max((m.end() for m in _SPLIT.finditer(answer, 0, match.start())), default=0)
        end_match = _SPLIT.search(answer, match.end())
        end = end_match.start() if end_match else len(answer)
        clause = answer[start:end]
        local_offset = match.start() - start
        trait_code = _nearest_trait(clause, local_offset)
        if trait_code:
            candidates = [item for item in candidates if item["trait_code"] == trait_code]
        line_start = answer.rfind("\n", 0, match.start()) + 1
        line_end = answer.find("\n", match.end())
        current_line = answer[line_start:line_end if line_end >= 0 else len(answer)]
        scope_text = current_line if "|" in current_line else clause
        compact = re.sub(r"国家水稻数据中心|水稻数据中心|RiceData", "", scope_text, flags=re.I)
        regions = {region for region, aliases in _REGIONS.items()
                   if any(alias in compact for alias in aliases)}
        if not regions and not _YEAR.search(compact):
            previous_lines = [line.strip() for line in answer[:start].splitlines() if line.strip()]
            if previous_lines and (previous_lines[-1].startswith(("**", "###"))):
                compact += " " + previous_lines[-1]
                regions = {region for region, aliases in _REGIONS.items()
                           if any(alias in compact for alias in aliases)}
        if regions:
            candidates = [item for item in candidates if any(
                region in str(approvals[item["approval_id"]].get("approval_region") or "") or
                any(alias in str(approvals[item["approval_id"]].get("approval_no") or "")
                    for alias in _REGIONS[region])
                for region in regions)]
        years = set(_YEAR.findall(compact))
        if years and (regions or "审定" in compact or "省定" in compact) and not any(
            token in compact for token in ("试验", "区试", "续试", "检测")
        ):
            candidates = [item for item in candidates
                          if str(approvals[item["approval_id"]].get("approval_year")) in years]
        named_approval = [row["approval_no"] for row in approvals.values()
                          if row.get("approval_no") and row["approval_no"] in compact]
        if named_approval:
            candidates = [item for item in candidates
                          if approvals[item["approval_id"]].get("approval_no") in named_approval]
        if not candidates:
            raise ResearchAgentError("模型把指标数值关联到错误的性状或审定记录，本轮未展示或保存回答，请重试。")
        if len(approvals) > 1 and not regions and not named_approval and not years:
            raise ResearchAgentError("模型未说明指标数值对应哪条审定记录，本轮未展示或保存回答，请重试。")
        if all(item.get("status") != "confirmed" for item in candidates):
            if (re.search(r"已确认|确认为|准确值|最终亩产", scope_text) or
                    not re.search(r"候选|待核对|未核实|未确认|仅见于原文|按原文", answer)):
                raise ResearchAgentError("模型把待核对的原文候选数值写成确认值，本轮未展示或保存回答，请重试。")

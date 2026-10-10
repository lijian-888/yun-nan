"""Resolve a RiceData cultivar by its full name, display name, or recorded aliases."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


_SOURCE_ID = re.compile(r"ricedata\.cn/variety/varis/(\d+)\.htm", re.I)
_PARENTHESIZED = re.compile(r"[（(].*$")


def name_spans(question: str, name: str | None) -> list[tuple[int, int]]:
    if not name or not name.strip():
        return []
    haystack, needle = question.casefold(), name.strip().casefold()
    offset = 0
    spans = []
    while (start := haystack.find(needle, offset)) >= 0:
        end = start + len(needle)
        before = haystack[start - 1] if start else ""
        after = haystack[end] if end < len(haystack) else ""
        joined_left = needle[0].isascii() and needle[0].isalnum() and before.isascii() and before.isalnum()
        joined_right = needle[-1].isascii() and needle[-1].isalnum() and after.isascii() and after.isalnum()
        if not joined_left and not joined_right:
            spans.append((start, end))
        offset = start + 1
    return spans


def explicit_name_mention(question: str, name: str | None) -> bool:
    return bool(name_spans(question, name))


def select_name_matches(question: str, candidates: list[dict], *, name_key: str = "variety_name",
                        alias_keys: tuple[str, ...] = ("trial_names", "former_names"), limit: int = 8) -> list[dict]:
    """Suppress contained names at the SAME occurrence, not other named subjects."""
    mentions = []
    for index, row in enumerate(candidates):
        name = str(row.get(name_key) or "")
        names = [name, _PARENTHESIZED.sub("", name).strip()]
        for key in alias_keys:
            names.extend(row.get(key) or [])
        for alias in names:
            # Bare numeric labels in institute spreadsheets are not global identities.
            if re.fullmatch(r"\d+", str(alias).strip()):
                continue
            mentions.extend((start, end, index) for start, end in name_spans(question, alias))
    winners = [(start, index) for start, end, index in mentions if not any(
        other_start <= start and other_end >= end and other_end - other_start > end - start
        for other_start, other_end, _ in mentions)]
    indexes = list(dict.fromkeys(index for _, index in sorted(winners)))
    return [candidates[index] for index in indexes[:limit]]


def has_explicit_subject(question: str) -> bool:
    """Conservative omission check. An unknown subject must not inherit an old ID.

    Only recognized follow-up vocabulary can be omitted; unfamiliar wording
    asks for confirmation instead of silently reusing a previous cultivar.
    """
    from .ricedata_trait_facts import TRAIT_BY_CODE
    if _SOURCE_ID.search(question) or re.search(r"MAT-[A-Za-z0-9]+", question, re.I):
        return True
    if re.fullmatch(r"\s*(?:第)?[一二三四五六七八九十\d]+(?:条|个)?\s*[。?？]?", question):
        return False
    value = question
    value = re.sub(r"[\u4e00-\u9fff]{1,6}审[\u4e00-\u9fff]{0,5}\d{5,12}号?", "", value)
    aliases = {alias for spec in TRAIT_BY_CODE.values() for alias in spec.aliases}
    for alias in sorted(aliases, key=len, reverse=True):
        value = value.replace(alias, "")
    value = re.sub(r"(?:19|20)\d{2}年?|第?[一二三四五六七八九十\d]+(?:条|个)", "", value)
    for token in sorted(("国家水稻数据中心", "云南省农业科学院", "云南农科院", "全部审定记录", "全部表型数据",
                         "之前这个品种", "这个品种", "该品种", "上一个品种", "这种水稻", "这些品种",
                         "农科院", "院内", "公开", "只看", "仅看", "只查", "仅查", "特征特性", "审定记录",
                         "表型", "农艺", "考种", "性状", "数据", "资料", "信息", "结果", "全部", "所有", "完整", "全套",
                         "是多少", "有多少", "有没有", "怎么样", "如何", "查看", "查询", "请问", "请查", "告诉我", "给我",
                         "改查", "再看", "看下", "选", "查", "看", "那", "它", "其", "的", "呢", "多少", "是否有", "多年",
                         "四川", "福建", "云南", "江苏", "浙江", "安徽", "江西", "湖北", "湖南", "广东", "广西", "海南",
                         "贵州", "重庆", "河南", "山东", "辽宁", "吉林", "黑龙江", "上海", "河北", "国家", "国审", "省定", "审定", "省"),
                        key=len, reverse=True):
        value = value.replace(token, "")
    return bool(re.sub(r"[\s，,。！？?！；;：:（）()、]+", "", value))


def resolve_varieties(session: Session, question: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Resolve independent mentions without letting `3号` steal `国稻3号`."""
    source_id = _SOURCE_ID.search(question)
    if source_id:
        return [dict(row) for row in session.execute(text("""
            SELECT variety_id, variety_name, source_variety_id, source_url, trial_names, former_names
            FROM ricedata.rice_variety WHERE source_variety_id = :source_id LIMIT 2
        """), {"source_id": source_id.group(1)}).mappings()]
    candidates = [dict(row) for row in session.execute(text("""
        SELECT variety_id, variety_name, source_variety_id, source_url, trial_names, former_names
        FROM ricedata.rice_variety
        WHERE variety_name IS NOT NULL AND (
            strpos(lower(:question), lower(variety_name)) > 0 OR
            (regexp_replace(variety_name, '[（(].*$', '') <> '' AND
             strpos(lower(:question), lower(regexp_replace(variety_name, '[（(].*$', ''))) > 0) OR
            EXISTS (SELECT 1 FROM unnest(COALESCE(trial_names, ARRAY[]::text[]) ||
                         COALESCE(former_names, ARRAY[]::text[])) AS alias_name
                    WHERE alias_name <> '' AND strpos(lower(:question), lower(alias_name)) > 0)
        )
        ORDER BY char_length(regexp_replace(variety_name, '[（(].*$', '')) DESC, variety_id
        LIMIT 300
    """), {"question": question}).mappings()]
    return select_name_matches(question, candidates, limit=limit)

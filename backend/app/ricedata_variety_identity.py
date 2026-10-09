"""Resolve a RiceData cultivar by its full name, display name, or recorded aliases."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


_SOURCE_ID = re.compile(r"ricedata\.cn/variety/varis/(\d+)\.htm", re.I)
_PARENTHESIZED = re.compile(r"[（(].*$")


def explicit_name_mention(question: str, name: str | None) -> bool:
    if not name or not name.strip():
        return False
    haystack, needle = question.casefold(), name.strip().casefold()
    offset = 0
    while (start := haystack.find(needle, offset)) >= 0:
        end = start + len(needle)
        before = haystack[start - 1] if start else ""
        after = haystack[end] if end < len(haystack) else ""
        joined_left = needle[0].isascii() and needle[0].isalnum() and before.isascii() and before.isalnum()
        joined_right = needle[-1].isascii() and needle[-1].isalnum() and after.isascii() and after.isalnum()
        if not joined_left and not joined_right:
            return True
        offset = start + 1
    return False


def resolve_varieties(session: Session, question: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Return only best-length explicit matches; never let `3号` steal `国稻3号`."""
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
    scored = []
    for row in candidates:
        names = (row["variety_name"], _PARENTHESIZED.sub("", row["variety_name"]).strip(),
                 *(row.get("trial_names") or []), *(row.get("former_names") or []))
        matched_length = max((len(name.strip()) for name in names if explicit_name_mention(question, name)), default=0)
        if matched_length:
            scored.append((matched_length, row))
    if not scored:
        return []
    best = max(length for length, _ in scored)
    return [row for length, row in scored if length == best][:limit]

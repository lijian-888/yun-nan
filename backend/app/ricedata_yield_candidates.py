"""Read-only, unverified mu-yield candidates from an approval's original text.

These candidates are intentionally not inserted into the structured yield table.
An explicit 亩 basis is required; kg/hm2 and area-unspecified 单产 are excluded.
"""

from __future__ import annotations

import re
from decimal import Decimal


_YIELD = re.compile(
    r"(?P<label>(?:平均|一般|最高|最低|约)?(?:亩产|每亩(?:平均)?产(?:量)?))"
    r"\s*(?:约|达|为|是|:|：)?\s*"
    r"(?P<value>\d{1,5}(?:\.\d{1,3})?)\s*"
    r"(?P<unit>公斤|千克|[Kk][Gg])(?:\s*/\s*(?:亩|[Mm][Uu]))?"
    r"(?![A-Za-z0-9/㎡²])"
)


def extract_yield_candidates(source_text: str | None) -> list[dict]:
    """Return literal excerpts only; no unit conversion or inferred approval year."""
    if not source_text:
        return []
    found = []
    for match in _YIELD.finditer(source_text):
        start = max(source_text.rfind(mark, 0, match.start()) for mark in "。；;\n") + 1
        ends = [pos for mark in "。；;\n" if (pos := source_text.find(mark, match.end())) >= 0]
        end = min(ends) + 1 if ends else len(source_text)
        found.append({
            "value": Decimal(match.group("value")),
            "literal": match.group(0).strip(),
            "excerpt": source_text[start:end].strip()[:800],
            "status": "unverified_original",
        })
    return found

"""Human-readable rice query formatting. No data writes or model-generated facts."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .ricedata_trait_facts import TRAIT_BY_CODE, extract_narrative_facts


UNIT_LABELS = {
    "d": "天", "day": "天", "days": "天", "日": "天",
    "cm": "厘米", "mm": "毫米", "m": "米", "g": "克", "kg": "公斤",
    "grain": "粒", "grains": "粒", "10k/mu": "万穗/亩", "grade": "级",
    "ratio": "", "index": "", "score": "分", "leaf": "片", "panicle": "穗",
    "mg/kg": "毫克/千克", "kg/mu": "公斤/亩", "％": "%", "℃": "℃",
}
TRIAL_LABELS = {"regional_trial": "区域试验", "production_trial": "生产试验",
                "variety_comparison": "品种比较试验"}
QUALITY_CODES = {
    "brown_rice_rate_pct", "milled_rice_rate_pct", "head_rice_rate_pct", "protein_content_pct",
    "amylose_content_pct", "chalkiness_pct", "chalky_grain_rate_pct", "gel_consistency_mm",
    "grain_length_mm", "grain_width_mm", "length_width_ratio", "transparency_grade",
    "alkali_spreading_value_grade", "grain_fragrance", "taste_score", "moisture_content_pct",
    "cadmium_mg_kg", "selenium_mg_kg", "anthocyanin_mg_kg",
}


def number(value: Any) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def cell(value: Any) -> str:
    """Escape database text as plain Markdown table content, not markup."""
    value = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return re.sub(r"([\\|`*_\[\]<>])", r"\\\1", value)


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join(["| " + " | ".join(map(cell, headers)) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(map(cell, row)) + " |" for row in rows)])


def trait_label(row: dict) -> str:
    code = row.get("trait_code")
    spec = TRAIT_BY_CODE.get(code)
    source = str(row.get("source_text") or "")
    if code == "growth_duration_days":
        if "出苗至成熟" in source:
            return "出苗至成熟日数"
        return "全生育期" if "全生育期" in source else "生育期"
    if code == "protein_content_pct" and "粗蛋白" in source:
        return "粗蛋白含量"
    if code == "taste_score" and "感官" in source and "食味" not in source:
        return "感官评分"
    if code == "anthocyanin_mg_kg" and "矢车菊素" in source and "花青素" not in source:
        return "矢车菊素含量"
    if spec:
        return spec.name
    label = str(row.get("trait_name") or "")
    return label if re.search(r"[\u4e00-\u9fff]", label) else "其他性状（名称待核）"


def group_label(row: dict) -> str:
    code = str(row.get("trait_code") or "")
    if code in QUALITY_CODES:
        return "稻米品质"
    if any(marker in code for marker in ("blast", "blight", "planthopper", "virus", "smut", "tolerance", "lodging")):
        return "抗病与耐逆"
    return "农艺性状"


def unit_label(row: dict) -> str:
    raw = str(row.get("unit") or "").strip()
    if raw in {"grain", "grains"} and row.get("trait_code") in {"grains_per_panicle", "filled_grains_per_panicle"}:
        return "粒/穗"
    if raw == "panicle" and row.get("trait_code") == "panicle_number_per_plant":
        return "穗/株"
    if raw in UNIT_LABELS:
        return UNIT_LABELS[raw]
    if re.search(r"[A-Za-z]", raw):
        return "（单位待核）"
    return raw


def display_value(row: dict) -> str:
    numeric, low, high = row.get("value_numeric"), row.get("value_min"), row.get("value_max")
    if low is not None and high is not None:
        value = f"{number(low)}～{number(high)}"
    elif numeric is not None:
        value = number(numeric)
    elif low is not None:
        value = "≥" + number(low)
    elif high is not None:
        value = "≤" + number(high)
    else:
        # Qualitative observations already contain their meaning; do not append
        # a numerical unit, or turn their nearby source numbers into values.
        return str(row.get("value_text") or "未提供数值").strip()
    literal = str(row.get("value_text") or "")
    qualifier = str(row.get("qualifier") or "")
    # Older measurements store approximation only in the literal source. Read
    # the SAME labelled value, never qualifiers belonging to another indicator.
    if row.get("trait_code") in TRAIT_BY_CODE and row.get("source_text"):
        matched = [f for f in extract_narrative_facts("yield_performance_text", row["source_text"])
                   if f["trait_code"] == row["trait_code"] and
                   (f.get("value_numeric"), f.get("value_min"), f.get("value_max")) == (numeric, low, high)]
        if len(matched) == 1:
            literal += " " + matched[0]["value_text"]
            qualifier = qualifier or matched[0].get("qualifier") or ""
    bound = re.search(r"以上|以下", literal + qualifier)
    prefix = "约" if re.search(r"约|近|左右", literal + qualifier) else ""
    suffix = bound.group(0) if bound else ""
    if qualifier not in {"", "约", "以上", "以下"} and not prefix:
        prefix = qualifier
    return f"{prefix}{value}{unit_label(row)}{suffix}"


def trial_label(value: str | None) -> str:
    return TRIAL_LABELS.get(value, "试验类型待核" if value else "未注明试验类型")


def yield_year(row: dict) -> str:
    if row.get("trial_year"):
        return f"{row['trial_year']}年"
    covered = row.get("covered_years") or []
    if covered:
        suffix = "（平均）" if row.get("is_aggregate") else "（具体年份对应待核）"
        return "/".join(map(str, covered)) + "年" + suffix
    return "年份未明确"


def yield_change(row: dict) -> str:
    value = row.get("relative_change_pct")
    if value is None:
        return "未提供"
    value = Decimal(str(value))
    return ("增产" if value > 0 else "减产" if value < 0 else "") + number(abs(value)) + "%"


def source_excerpts(sections: list[tuple[str, Any]]) -> list[dict]:
    """Keep originals once per record; excerpts contained in a full paragraph
    do not need another copy. Different approvals stay in separate cards.
    """
    result = []
    normalize = lambda s: re.sub(r"\s+", "", s).replace("，", ",").replace("％", "%")
    for title, raw in sections:
        value = str(raw or "").strip()
        if not value:
            continue
        normalized = normalize(value)
        if any(normalized in normalize(item["text"]) for item in result):
            continue
        result.append({"title": title, "text": value[:4000] +
                       ("…（原文较长，仅展示节选；完整资料请查看来源页面）" if len(value) > 4000 else "")})
    return result

"""Conservative numeric fact extraction from every RiceData approval narrative.

This supplements (never replaces) the already structured trait measurements.
Every extracted value retains its approval, source field and literal excerpt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


PARSER_VERSION = "approval-numeric-v1"
SOURCE_FIELDS = (
    "yield_performance_text", "cultivation_text", "suitable_area_text",
    "approval_opinion_text", "variety_source_text",
)


@dataclass(frozen=True)
class TraitSpec:
    code: str
    name: str
    aliases: tuple[str, ...]
    unit: str


# The labels are explicit: an arbitrary number near a trait name is not a fact.
# Keep the vocabulary shared by ETL and the query planner.
TRAITS = (
    TraitSpec("amylose_content_pct", "直链淀粉含量", ("直链淀粉含量", "直链淀粉"), "%"),
    TraitSpec("filled_grains_per_panicle", "每穗实粒数", ("每穗实粒数", "穗实粒数", "每穗实粒"), "粒/穗"),
    TraitSpec("grains_per_panicle", "每穗总粒数", ("每穗总粒数", "穗总粒数", "每穗粒数"), "粒/穗"),
    TraitSpec("seed_setting_rate_pct", "结实率", ("结实率",), "%"),
    TraitSpec("effective_panicles_10k_per_mu", "亩有效穗数", ("每亩有效穗", "亩有效穗", "有效穗数"), "万穗/亩"),
    TraitSpec("thousand_grain_weight_g", "千粒重", ("千粒重",), "克"),
    TraitSpec("plant_height_cm", "株高", ("株高",), "厘米"),
    TraitSpec("panicle_length_cm", "穗长", ("穗长",), "厘米"),
    TraitSpec("growth_duration_days", "全生育期", ("全生育期", "生育期"), "天"),
    TraitSpec("brown_rice_rate_pct", "糙米率", ("糙米率", "出糙率"), "%"),
    TraitSpec("milled_rice_rate_pct", "精米率", ("精米率",), "%"),
    TraitSpec("head_rice_rate_pct", "整精米率", ("整精米率",), "%"),
    TraitSpec("protein_content_pct", "蛋白质含量", ("蛋白质含量", "蛋白质"), "%"),
    TraitSpec("chalkiness_pct", "垩白度", ("垩白度",), "%"),
    TraitSpec("chalky_grain_rate_pct", "垩白粒率", ("垩白粒率",), "%"),
    TraitSpec("gel_consistency_mm", "胶稠度", ("胶稠度",), "毫米"),
    TraitSpec("grain_length_mm", "粒长", ("粒长",), "毫米"),
    TraitSpec("length_width_ratio", "长宽比", ("长宽比",), ""),
    TraitSpec("transparency_grade", "透明度", ("透明度",), "级"),
    TraitSpec("alkali_spreading_value_grade", "碱消值", ("碱消值",), "级"),
)
# These fields are already structured in rice_variety_trait_measurement. They
# participate in exact lookup but are not newly parsed from unrelated prose.
QUERY_ONLY_TRAITS = (
    TraitSpec("yield_kg_per_mu", "亩产", ("平均亩产", "亩产", "单产"), "公斤/亩"),
    TraitSpec("blast_reaction", "稻瘟病抗性结论", ("稻瘟病抗性结论", "稻瘟病抗性"), ""),
    TraitSpec("bacterial_blight_reaction", "白叶枯病抗性结论", ("白叶枯病抗性结论", "白叶枯病抗性"), ""),
    TraitSpec("bacterial_blight_grade", "白叶枯病等级", ("白叶枯病等级",), "级"),
    TraitSpec("leaf_blast_grade", "叶瘟等级", ("叶瘟等级",), "级"),
    TraitSpec("panicle_blast_grade", "穗瘟等级", ("穗瘟等级",), "级"),
    TraitSpec("panicle_neck_blast_grade", "穗颈瘟等级", ("穗颈瘟等级",), "级"),
    TraitSpec("neck_blast_loss_grade", "穗颈瘟损失率最高级", ("穗颈瘟损失率最高级",), "级"),
    TraitSpec("blast_resistance_index", "稻瘟病综合抗性指数", ("稻瘟病综合抗性指数",), ""),
    TraitSpec("brown_planthopper_reaction", "褐飞虱抗性结论", ("褐飞虱抗性结论", "褐飞虱抗性"), ""),
    TraitSpec("brown_planthopper_grade", "褐飞虱等级", ("褐飞虱等级",), "级"),
    TraitSpec("sheath_blight_reaction", "纹枯病抗性结论", ("纹枯病抗性结论", "纹枯病抗性"), ""),
    TraitSpec("sheath_blight_grade", "纹枯病等级", ("纹枯病等级",), "级"),
    TraitSpec("rice_false_smut_grade", "稻曲病等级", ("稻曲病等级",), "级"),
    TraitSpec("stripe_virus_grade", "条纹叶枯病等级", ("条纹叶枯病等级",), "级"),
    TraitSpec("rice_planthopper_grade", "稻飞虱等级", ("稻飞虱等级",), "级"),
    TraitSpec("cold_tolerance_grade", "耐冷性等级", ("耐冷性等级", "耐冷等级"), "级"),
    TraitSpec("heat_tolerance_grade", "耐热性等级", ("耐热性等级", "耐热等级"), "级"),
    TraitSpec("lodging_resistance", "抗倒性", ("抗倒性", "抗倒伏能力"), ""),
    TraitSpec("plant_architecture", "株型", ("株型",), ""),
    TraitSpec("tillering_ability", "分蘖力", ("分蘖力",), ""),
    TraitSpec("awn", "芒性", ("芒性",), ""),
    TraitSpec("grain_fragrance", "香味", ("香味",), ""),
    TraitSpec("empty_grain_rate_pct", "空壳率", ("空壳率",), "%"),
    TraitSpec("panicle_rate_pct", "成穗率", ("成穗率",), "%"),
    TraitSpec("panicle_number_per_plant", "单株有效穗", ("单株有效穗",), "穗/株"),
    TraitSpec("main_stem_leaf_count", "主茎叶片数", ("主茎叶片数",), "片"),
    TraitSpec("accumulated_temperature_c", "≥10℃积温", ("积温",), "℃"),
    TraitSpec("heading_days", "播始历期", ("播始历期",), "天"),
    TraitSpec("moisture_content_pct", "检测水分", ("检测水分", "水分含量"), "%"),
    TraitSpec("taste_score", "食味评分", ("食味评分", "感官评分"), "分"),
    TraitSpec("grain_width_mm", "粒宽", ("粒宽",), "毫米"),
    TraitSpec("cadmium_mg_kg", "糙米镉含量", ("糙米镉含量", "镉含量"), "mg/kg"),
    TraitSpec("selenium_mg_kg", "硒含量", ("硒含量",), "mg/kg"),
    TraitSpec("anthocyanin_mg_kg", "花青素含量", ("花青素含量", "矢车菊素含量"), "mg/kg"),
    TraitSpec("fertility_threshold_c", "育性转换临界温度", ("育性转换临界温度",), "℃"),
    TraitSpec("sterile_plant_rate_pct", "不育株率", ("不育株率",), "%"),
    TraitSpec("pollen_sterility_rate_pct", "花粉不育度", ("花粉不育度",), "%"),
    TraitSpec("stigma_exsertion_rate_pct", "柱头外露率", ("柱头外露率",), "%"),
    TraitSpec("double_stigma_exsertion_rate_pct", "双边外露率", ("双边外露率",), "%"),
)
TRAIT_BY_CODE = {item.code: item for item in (*TRAITS, *QUERY_ONLY_TRAITS)}

_NUMBER = r"(?P<first>\d{1,5}(?:\.\d{1,3})?)"
_RANGE = r"(?:\s*(?:-|~|～|—|－|至|到)\s*(?P<last>\d{1,5}(?:\.\d{1,3})?))?"
_UNIT = r"(?P<unit>万?粒|万?穗|万|%|％|克|g|厘米|cm|毫米|mm|天|日|级)?"
_AFTER = r"(?P<after>左右|以上|以下|约)?"
_PATTERNS = {
    spec.code: re.compile(
        rf"(?P<label>{'|'.join(re.escape(a) for a in sorted(spec.aliases, key=len, reverse=True))})"
        rf"\s*(?:为|达|是|约|近|:|：)?\s*{_NUMBER}{_RANGE}\s*{_UNIT}\s*{_AFTER}"
    ) for spec in TRAITS
}
_PERCENT_CODES = {spec.code for spec in TRAITS if spec.unit == "%"}
_UNIT_ALIASES = {"％": "%", "g": "克", "cm": "厘米", "mm": "毫米", "日": "天"}


def requested_traits(question: str) -> list[TraitSpec]:
    """Only route explicit metric questions, not broad variety summaries."""
    hits: list[tuple[int, int, TraitSpec]] = []
    for spec in (*TRAITS, *QUERY_ONLY_TRAITS):
        for alias in spec.aliases:
            for match in re.finditer(re.escape(alias), question):
                hits.append((match.start(), match.end(), spec))
    # "精米率" is contained in "整精米率"; only the longest explicit label
    # may claim that span of the question.
    winners = [hit for hit in hits if not any(
        other[0] <= hit[0] and other[1] >= hit[1]
        and other[1] - other[0] > hit[1] - hit[0] for other in hits
    )]
    return list({spec.code: spec for _, _, spec in winners}.values())


def extract_narrative_facts(source_field: str, source_text: str) -> list[dict]:
    if source_field not in SOURCE_FIELDS or not source_text:
        return []
    text = source_text.replace("，", ",").replace("％", "%")
    found: list[dict] = []
    occupied: list[tuple[int, int]] = []
    # Longest labels first prevents "精米率" from matching inside "整精米率".
    for spec in sorted(TRAITS, key=lambda s: max(map(len, s.aliases)), reverse=True):
        for match in _PATTERNS[spec.code].finditer(text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            number = Decimal(match.group("first"))
            last = Decimal(match.group("last")) if match.group("last") else None
            unit = _UNIT_ALIASES.get(match.group("unit") or "", match.group("unit") or "")
            if spec.code in _PERCENT_CODES and unit not in {"", "%"}:
                continue
            if spec.unit == "粒/穗" and unit not in {"", "粒"}:
                continue
            if spec.unit == "万穗/亩" and unit not in {"", "万", "万穗", "穗"}:
                continue
            if spec.unit in {"克", "厘米", "毫米", "天", "级"} and unit not in {"", spec.unit}:
                continue
            if spec.code in _PERCENT_CODES and (number > 100 or (last is not None and last > 100)):
                continue
            if last is not None and last < number:
                continue
            qualifier = "约" if ("约" in match.group(0) or "近" in match.group(0) or match.group("after") == "左右") else (match.group("after") or "")
            sentence_start = max(text.rfind("。", 0, match.start()), text.rfind("；", 0, match.start()), text.rfind(";", 0, match.start())) + 1
            sentence_end_candidates = [pos for marker in ("。", "；", ";") if (pos := text.find(marker, match.end())) >= 0]
            sentence_end = min(sentence_end_candidates) + 1 if sentence_end_candidates else len(text)
            found.append({
                "trait_code": spec.code,
                "trait_name": spec.name,
                "value_numeric": number if last is None else None,
                "value_min": number if last is not None else None,
                "value_max": last,
                "value_text": match.group(0),
                "unit": spec.unit,
                "qualifier": qualifier,
                "source_field": source_field,
                "source_start": match.start(),
                "source_excerpt": text[sentence_start:sentence_end].strip()[:800],
                "parser_version": PARSER_VERSION,
                "review_status": "machine_extracted",
            })
            occupied.append(match.span())
    return sorted(found, key=lambda row: row["source_start"])

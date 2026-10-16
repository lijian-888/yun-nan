"""Immutable PDF/CSV exports for parent recommendation v2 snapshots."""

from __future__ import annotations

import csv
import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _all_pairs(result: dict[str, Any]):
    for key in ("recommendations", "backup_recommendations", "pending_verification", "excluded_combinations"):
        yield from result.get(key) or []


def recommendation_v2_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["声明", result.get("disclaimer", "表型与系谱辅助推荐")])
    writer.writerow(["候选来源", result.get("candidate_source", "国家水稻数据中心")])
    writer.writerow(["育种目标", result.get("breeding_goal", "")])
    writer.writerow(["目标地区", result.get("target_region", "")])
    writer.writerow(["引擎版本", result.get("engine_version", "")])
    writer.writerow([])
    writer.writerow(["等级", "母本编号", "母本", "父本编号", "父本", "目标匹配", "证据覆盖", "可信度",
                     "母本贡献", "父本贡献", "亲缘结论", "数据缺口", "排除原因"])
    for item in _all_pairs(result):
        writer.writerow([
            item.get("tier"), item.get("female_parent", {}).get("variety_id"),
            item.get("female_parent", {}).get("name"), item.get("male_parent", {}).get("variety_id"),
            item.get("male_parent", {}).get("name"), item.get("target_match"), item.get("evidence_coverage"),
            item.get("confidence"), "；".join(item.get("female_contributions") or []),
            "；".join(item.get("male_contributions") or []),
            "；".join((item.get("kinship") or {}).get("reasons") or []),
            "；".join(item.get("data_gaps") or []), "；".join(item.get("exclusion_reasons") or []),
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def recommendation_v2_pdf(result: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    font = "STSong-Light"
    try:
        pdfmetrics.getFont(font)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    styles = getSampleStyleSheet()
    for name in ("Title", "Heading1", "Heading2", "BodyText"):
        styles[name].fontName = font
        styles[name].wordWrap = "CJK"
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                                 topMargin=14 * mm, bottomMargin=14 * mm,
                                 title="表型与系谱辅助亲本推荐报告")
    story: list[Any] = [Paragraph("表型与系谱辅助亲本推荐报告", styles["Title"]), Spacer(1, 4 * mm)]
    story.append(Paragraph(str(result.get("disclaimer") or ""), styles["BodyText"]))
    story.append(Paragraph(f"育种目标：{result.get('breeding_goal') or '未填写'}", styles["BodyText"]))
    story.append(Paragraph(f"目标地区：{result.get('target_region') or '未限定'}", styles["BodyText"]))
    story.append(Paragraph(
        f"候选来源：{result.get('candidate_source') or '国家水稻数据中心'}；候选 {result.get('candidate_count', 0)} 个；"
        f"组合 {result.get('pair_count', 0)} 个；引擎 {result.get('engine_version') or '—'}。",
        styles["BodyText"],
    ))
    story.append(Spacer(1, 4 * mm))
    rows = [["等级", "亲本组合", "目标匹配", "覆盖度", "可信度"]]
    pairs = list(_all_pairs(result))
    for item in pairs[:100]:
        rows.append([
            str(item.get("tier") or ""),
            f"{item.get('female_parent', {}).get('name', '')} × {item.get('male_parent', {}).get('name', '')}",
            f"{float(item.get('target_match') or 0):.0%}",
            f"{float(item.get('evidence_coverage') or 0):.0%}",
            str(item.get("confidence") or ""),
        ])
    table = Table(rows or [["—"]], colWidths=[20 * mm, 75 * mm, 27 * mm, 27 * mm, 25 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEFE8")),
        ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#8AA69C")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    for index, item in enumerate(pairs[:30], 1):
        story.append(Paragraph(
            f"{index}. {item.get('female_parent', {}).get('name', '')} × {item.get('male_parent', {}).get('name', '')}",
            styles["Heading2"],
        ))
        story.append(Paragraph(
            "母本贡献：" + ("；".join(item.get("female_contributions") or []) or "暂无明确偏好证据"),
            styles["BodyText"],
        ))
        story.append(Paragraph(
            "父本贡献：" + ("；".join(item.get("male_contributions") or []) or "暂无明确偏好证据"),
            styles["BodyText"],
        ))
        story.append(Paragraph(
            "亲缘：" + ("；".join((item.get("kinship") or {}).get("reasons") or []) or "未形成结论"),
            styles["BodyText"],
        ))
        if item.get("data_gaps"):
            story.append(Paragraph("数据缺口：" + "；".join(item["data_gaps"]), styles["BodyText"]))
        if item.get("exclusion_reasons"):
            story.append(Paragraph("排除原因：" + "；".join(item["exclusion_reasons"]), styles["BodyText"]))
    if len(pairs) > 100:
        story.append(Paragraph("报告表格仅列示前100个快照结果，完整数据请使用CSV导出。", styles["BodyText"]))
    document.build(story)
    return buffer.getvalue()


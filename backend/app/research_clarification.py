"""Small, deterministic pre-answer clarifications for underspecified research tasks.

These questions are UI state, not model-generated conclusions. A researcher may
answer or explicitly skip; skipped details must never be silently assumed.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ricedata_trait_facts import requested_traits
from .ricedata_trait_lookup import variety_context_from_question
from .research_question_routing import is_general_explanation, is_system_capability_question


_PARENT_REQUEST = re.compile(r"(?:推荐|选择|筛选|选)(?:[^。！？?]{0,12})(?:亲本|杂交组合)|(?:亲本|杂交组合)(?:[^。！？?]{0,12})(?:推荐|选择|筛选)")
_BROAD_DATA = re.compile(r"(?:表型|品种|农艺性状)数据(?:信息|情况)?|(?:的|所有|全部)数据(?:信息|情况)?|有什么数据|查(?:一下|看)?(?:品种)?资料")
_ALL = re.compile(r"全部|所有|完整|概览|总览|都看|不限指标|不限定指标")
_TRAIT_GROUP = re.compile(r"产量|抗病|耐逆|品质|米质|考种|生育期|株型|农艺性状|系谱|亲本来源|审定")
_TARGET_TRAIT = re.compile(r"高产|丰产|产量|稳产|抗病|稻瘟|白叶枯|耐冷|耐旱|耐盐|抗倒伏|品质|米质|直链淀粉|株高|生育期|结实率|千粒重")
_RICE_TYPE = re.compile(r"籼稻|粳稻|籼型|粳型|籼粳|不限(?:类型|籼粳)")
_REGION = re.compile(r"云南|四川|广西|江西|福建|广东|湖南|湖北|浙江|江苏|安徽|贵州|海南|重庆|东北|华南|长江|西南|高海拔|低海拔|生态区|种植区|不限(?:地区|区域|生态区)")


def clarification_for_question(
    session: Session,
    original_question: str,
    *,
    supplement: str = "",
    attempt: int = 0,
) -> dict[str, Any] | None:
    """Return one useful question, or None when enough scope is present.

    At most two prompts are issued for one original turn. If the researcher
    still cannot supply details, the normal answer path must state limits.
    """
    if attempt >= 2:
        return None
    original = original_question.strip()
    direct_parent_task = bool(_PARENT_REQUEST.search(original) and not re.search(r"如何|怎么|怎样|为什么|什么是", original))
    if is_system_capability_question(original) or (is_general_explanation(original) and not direct_parent_task):
        return None
    extra = supplement.strip()
    combined = f"{original} {extra}"
    if _PARENT_REQUEST.search(original) and not re.search(r"不需要推荐|不要推荐|无需推荐", original):
        missing: list[str] = []
        if not _TARGET_TRAIT.search(combined):
            missing.append("希望改良哪些目标性状（如产量、抗病或米质）")
        if not _RICE_TYPE.search(combined):
            missing.append("偏向籼稻还是粳稻")
        region_text = combined.replace("云南农科院", "").replace("云南省农业科学院", "")
        if not _REGION.search(region_text):
            missing.append("主要面向哪个种植地区或生态区")
        if not missing:
            return None
        return {
            "kind": "parent_target",
            "question": "为了缩小亲本候选范围，请补充：" + "；".join(missing) + "？已有的目标性状会保留；若暂时不确定，可跳过并查看有条件限制的初步建议。",
        }
    if not _BROAD_DATA.search(original) or len(original) > 140:
        return None
    # A precise metric question already has an approval-aware database route.
    if requested_traits(original):
        return None
    variety_context = variety_context_from_question(session, combined)
    if variety_context:
        variety_id = variety_context["variety_id"]
        name = session.execute(text("""
            SELECT variety_name FROM ricedata.rice_variety WHERE variety_id = :variety_id
        """), {"variety_id": variety_id}).scalar_one()
        approvals = session.execute(text("""
            SELECT approval_year, approval_region, approval_no
            FROM ricedata.rice_variety_approval
            WHERE variety_id = :variety_id
            ORDER BY approval_year, approval_region, approval_id LIMIT 8
        """), {"variety_id": variety_id}).mappings().all()
        approval_hint = ""
        if len(approvals) > 1 and not re.search(r"(?:19|20)\d{2}|广西|江西|福建|云南|四川|安徽|湖北|湖南|江苏|浙江|广东|国家", extra):
            examples = "、".join(
                f"{row['approval_year']}年{row['approval_region'] or ''}" for row in approvals[:4]
            )
            approval_hint = f"；它有多条审定记录（如{examples}），请指定其中一条，或说“全部审定记录”"
        if (_ALL.search(combined) or requested_traits(extra) or _TRAIT_GROUP.search(extra)
                or _TRAIT_GROUP.search(original.replace("农艺性状数据", "").replace("品种数据", ""))):
            return None
        return {
            "kind": "variety_data",
            "variety_id": variety_id,
            "question": f"您想查看 **{name}** 的哪些数据？例如株高、结实率、产量或品质；也可以说“全部表型数据”{approval_hint}。不确定时可跳过，我会只概述已有数据并注明审定记录差异。",
        }
    return {
        "kind": "variety_data",
        "question": "请补充要查询的品种名称或品种详情页链接，以及想看的指标（如株高、产量或结实率）；也可以说明想看全部数据。若暂时不确定，可跳过，我只能介绍可查询的数据范围。",
    }


def expanded_question(original: str, answer: str, *, skipped: bool) -> str:
    if skipped:
        return (
            f"原始问题：{original}\n此前已补充：{answer.strip() or '无'}\n用户选择暂不继续补充条件。请仅依据已有可核对数据回答；"
            "未指定的品种、审定年份、省份、生态区或目标阈值必须标为未知。"
            "不得把不同审定记录的数值合并为同一结果，也不要给出无依据的确定性推荐。"
        )
    return (
        f"{original}\n用户补充条件：{answer.strip()}\n"
        "只按已明确的条件和可核对数据回答；其余未指定条件保持未知，不能自行补造。"
    )

"""Separate system help / knowledge explanations from literal cultivar facts.

These hints only route requests. They never fabricate cultivar measurements or
replace model reasoning for open-ended questions.
"""
import json
import logging
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

_PREFIX = r"(?:(?:请问|请|现在|目前|当前|这里|你们|你|本系统|这个系统|系统|平台|智能体|数据库|院内|云南农科院|农科院|国家水稻数据中心|水稻数据中心)\s*)*"
_CAPABILITIES = re.compile(
    r"^" + _PREFIX + r"(?:"
    r"(?:有哪些|有什么|都有什么|哪些|什么)(?:类型的|种类的)?(?:数据|资料|信息)(?:可以|可|能)?(?:查询|查|查看|检索|使用|用)?"
    r"|(?:可以|能|支持)(?:查询|查|检索)(?:哪些|什么)(?:数据|资料|信息)?"
    r"|(?:有哪些|有什么)(?:功能|能力)"
    r"|(?:能|可以)(?:做什么|做些什么|帮我做什么|回答哪些问题)"
    r"|(?:介绍|说明)(?:一下)?(?:系统|平台|你的)?(?:功能|能力|数据范围|查询范围)"
    r"|(?:数据|查询)(?:范围|目录)(?:是什么|有哪些)?"
    r"|(?:数据|记录)(?:数量|规模)(?:是多少|有多少)?"
    r")(?:呢|啊|吗|一下)?[。！？?！\s]*$"
)
_EXPLANATION = re.compile(
    r"为什么|为何|什么是|什么叫|是什么意思|有什么(?:含义|意义)|含义|原理|区别|关系|"
    r"(?:如何|怎么|怎样).{0,14}(?:分析|评价|评估|计算|理解|处理|清洗|整理|设计|建模|预测|编写|开发|导入|导出|种植|施肥|防治|解决|使用|选择|推荐)|"
    r"(?:解释|讲解|翻译|写一|撰写|建议|方案|流程|步骤|注意事项)"
)


def is_system_capability_question(question: str) -> bool:
    return bool(_CAPABILITIES.fullmatch(question.strip()))


def is_general_explanation(question: str) -> bool:
    return bool(_EXPLANATION.search(question))


def build_system_capability_evidence(session, question: str, *, institute_enabled: bool,
                                     selected_by_model: bool = False,
                                     include_counts: bool | None = None) -> tuple[str, list[dict]] | None:
    """Collect model evidence, never a ready-to-display assistant answer.

    Count public records only when requested. Private materials, identifiers and
    values are never returned here; private query availability is metadata only.
    """
    if not selected_by_model and not is_system_capability_question(question):
        return None
    groups = (
        ("品种基本信息", "ricedata.rice_variety"),
        ("各省及国家审定记录", "ricedata.rice_variety_approval"),
        ("已整理性状指标", "ricedata.rice_variety_trait_measurement"),
        ("审定原文补充指标", "ricedata.rice_variety_narrative_fact"),
        ("产量试验记录", "ricedata.rice_variety_yield_observation"),
        ("品种系谱关系", "ricedata.rice_pedigree_edge"),
        ("基因资料", "ricedata.rice_gene"),
    )
    if include_counts is None:
        include_counts = bool(re.search(r"多少|数量|规模|几条|几种", question))
    catalog = []
    counts = {}
    unavailable = []
    for label, relation in groups:
        try:
            with session.begin_nested():
                allowed = session.scalar(text("SELECT COALESCE(has_table_privilege(to_regclass(:name), 'SELECT'), false)"), {"name": relation})
                if not allowed:
                    unavailable.append(label)
                    continue
                # Relation names are fixed above, never user/model SQL.
                item = {"category": label, "accessible": True}
                if include_counts:
                    count = session.scalar(text(f"SELECT count(*) FROM {relation}"))
                    counts[label] = count
                    item["record_count"] = count
                    item["has_records"] = bool(count)
                else:
                    item["has_records"] = bool(session.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {relation} LIMIT 1)")))
                catalog.append(item)
        except SQLAlchemyError as exc:
            logger.warning("Catalog status unavailable: %s", type(exc).__name__)
            unavailable.append(label)
    private_available = False
    if institute_enabled:
        try:
            with session.begin_nested():
                private_available = bool(session.scalar(text("SELECT COALESCE(has_table_privilege(to_regclass('agent_data.material'), 'SELECT'), false) AND COALESCE(has_table_privilege(to_regclass('agent_data.phenotype'), 'SELECT'), false)")))
        except SQLAlchemyError:
            pass
    facts = {
        "data_sources": [{"name": "国家水稻数据中心（RiceData）", "accessible": bool(catalog)},
                         {"name": "云南省农业科学院院内治理数据", "query_entry_accessible": private_available}],
        "public_catalog": catalog,
        "unconfirmed_categories": unavailable,
        "institute_query_entry_accessible": private_available,
        "supported_dialogue_operations": ["查询已有品种、审定、性状、产量、系谱、基因资料",
            "按省份、年份、审定编号区分指标；范围不明时追问",
            "一般知识解释、研究方法讨论和文字整理", "提供查询证据及来源"],
        "limits": ["目录有记录不等于每个品种数据齐全",
            "未能确认访问状态不等于数据不存在",
            "未证明完整五性评价、基因型预测、长势预测或杂交验证能力，不得承诺这些功能已可用",
            "院内数据须授权查询；本轮只核对入口权限，没有读取材料明细",
            "不得从目录推断已收录测序文件、基因组文件或某个品种的具体指标"],
    }
    context = ("本轮系统能力核验结果（事实与约束，不是预设回答）：\n"
               + json.dumps(facts, ensure_ascii=False)
               + "\n请理解用户问的是功能、数据范围还是数量，再据此组织回答。"
                 "能力问题侧重能帮用户做什么；只有用户询问数量时才展示记录数。"
                 "不要照抄此 JSON、数据库字段或内部规则，不要要求用户先给品种名。")
    card = {"type": "system_capabilities", "title": "当前系统能力与数据目录核验",
            "detail": "本轮实时核验可访问目录；仅按需统计公开记录，不读取私有材料明细。"}
    if include_counts:
        card["counts"] = counts
    return context, [card]

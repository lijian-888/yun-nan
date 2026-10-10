"""Separate system help / knowledge explanations from literal cultivar facts.

These hints only route requests. They never fabricate cultivar measurements or
replace model reasoning for open-ended questions.
"""
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


def system_capability_answer(session, question: str, *, institute_enabled: bool) -> dict | None:
    """Describe the current accessible DB catalog, not sample cultivar rows.

    Public counts are queried live. Private materials, identifiers and values
    are never returned here; private query availability is metadata only.
    """
    if not is_system_capability_question(question):
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
    lines = ["## 当前可以查询和讨论的内容", "这个对话框不只用于品种查询，也可以回答科研知识、方法解释、分析思路及一般问题。",
             "### 已接入的数据", "以下根据本次系统数据库的可访问情况核对，不是样本数据："]
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
                count = session.scalar(text(f"SELECT count(*) FROM {relation}"))
                counts[label] = count
                lines.append(f"- {label}：{count:,}条记录。" if count else f"- {label}：当前尚无记录。")
        except SQLAlchemyError as exc:
            logger.warning("Catalog status unavailable: %s", type(exc).__name__)
            unavailable.append(label)
    if unavailable:
        lines.append("本次未能确认可访问状态：" + "、".join(unavailable) + "。这不代表数据不存在。")
    private_available = False
    if institute_enabled:
        try:
            with session.begin_nested():
                private_available = bool(session.scalar(text("SELECT COALESCE(has_table_privilege(to_regclass('agent_data.material'), 'SELECT'), false) AND COALESCE(has_table_privilege(to_regclass('agent_data.phenotype'), 'SELECT'), false)")))
        except SQLAlchemyError:
            pass
    lines.append("- 云南农科院院内材料及表型：" + ("已接入授权查询入口，按材料身份和来源单独查询。" if private_available else "本次尚未确认可用的授权查询入口。"))
    lines.extend(["### 你可以这样提问",
                  "- “先农8号的表型数据”——查看指定品种已有记录。",
                  "- “D优130的直链淀粉含量是多少”——多条审定记录时先选择省份、年份或编号。",
                  "- “某个基因的功能是什么”或“两个品种有什么系谱关系”——查询已有基因、系谱资料。",
                  "- “什么是稳产性”“如何评价稻米品质”“帮我整理育种研究思路”——由大模型解释、分析，不要求先填品种名。",
                  "### 使用边界",
                  "品种具体数值必须有数据库或来源证据；未找到时会明确说明。一般知识会与数据库事实区分。",
                  "并非每个品种都具备完整五性、测序或基因组文件；是否能提供某项资料，以实际收录及账号权限为准。"])
    return {"content": "\n\n".join(lines), "evidence": [{"type": "system_capabilities", "title": "当前数据库目录核验", "detail": "公开数据数量为本次实时统计；院内仅核对查询入口权限，未读取私有材料明细。", "counts": counts}],
            "response_kind": "system_capabilities"}

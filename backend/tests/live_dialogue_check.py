"""Opt-in paid-provider smoke checks; real read-only DB, no stored fixtures.

python live_dialogue_check.py --run-live
Only public questions and source metadata are sent. No users/tasks are created.
"""
import argparse
import asyncio

from app.ai_gateway import provider_settings
from app.dialogue_orchestration import plan_dialogue, validate_fact_measurements, dialogue_response_guidance
from app.main import SessionLocal, build_dialogue_evidence
from app.research_agent import stream_research_reply


CASES = [
    ("你有国家水稻数据中心的数据吗", "capabilities", []),
    ("云南农科院的数据接入了吗", "capabilities", []),
    ("能查RiceData吗", "capabilities", []),
    ("现在我可以查询什么资料", "capabilities", []),
    ("你都能帮我做什么", "capabilities", []),
    ("你有国稻3号的数据吗", "variety_fact", []),
    ("什么是直链淀粉含量", "general", []),
    ("如何评价稳产性", "general", []),
    ("数据库和知识库有什么区别", "general", []),
    ("帮我写一段项目介绍", "general", []),
    ("D优130的直链淀粉含量是多少", "variety_fact", []),
    ("结实率是多少", "variety_fact", [{"role":"user", "content":"查询中9优838选的特征特性"}]),
    ("那江西", "variety_fact", [{"role":"user", "content":"查询国稻3号的株高"},
        {"role":"assistant", "content":"有多条审定记录，请选择江西或浙江。"}]),
    ("帮我推荐杂交亲本", "research_task", []),
    ("Wx基因有什么功能", "reference", []),
    ("你好", "general", []),
]


async def main():
    provider = provider_settings()
    gate = asyncio.Semaphore(2)
    async def check(case):
        question, expected, history = case
        async with gate:
            plan = await plan_dialogue(provider=provider, question=question, history=history)
        print("PLAN", question, plan.model_dump(), flush=True)
        assert plan.intent == expected, (question, plan.intent, expected)
        return plan
    await asyncio.gather(*(check(case) for case in CASES))
    for question in ("你有国家水稻数据中心的数据吗", "云南农科院的数据接入了吗",
                     "什么是直链淀粉含量", "D优130在2006年福建审定的直链淀粉含量是多少",
                     "D优130的直链淀粉含量是多少"):
        plan = await plan_dialogue(provider=provider, question=question, history=[])
        with SessionLocal() as session:
            context, cards, states = await build_dialogue_evidence(session, plan, question, [],
                actor="只读模型验证", project_id="", external=provider.external)
        async for event in stream_research_reply(user_prompt=question, evidence_context=context, memory_state=None,
                                                 response_guidance=dialogue_response_guidance(plan)):
            if event.get("type") != "complete":
                continue
            answer = event["content"]
            if plan.intent == "variety_fact":
                validate_fact_measurements(answer, context)
            if plan.intent == "capabilities":
                assert "未找到可确认的品种" not in answer and "record_count" not in answer
                assert len(answer) < 400, "An availability question should not receive a long catalog"
            if plan.intent == "general":
                assert "GB/T" not in answer and len(answer) < 500, answer
            if "2006年福建" in question:
                assert "24.1" in answer and "福建" in answer, answer
            if question == "D优130的直链淀粉含量是多少":
                assert any(s.get("state") == "research_clarification" for s in states)
                assert "24.1%" not in answer, answer
            print("ANSWER", question, event.get("response_mode", "model"), answer, flush=True)
    print("LIVE_CHECK_OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true")
    if not parser.parse_args().run_live:
        parser.error("Use --run-live to authorize provider calls")
    asyncio.run(main())

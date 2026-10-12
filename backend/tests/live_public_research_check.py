"""Opt-in public search and model-answer check; no stored answers or DB writes."""
import argparse
import asyncio
import json

from app.ai_gateway import provider_settings
from app.dialogue_orchestration import dialogue_response_guidance, plan_dialogue
from app.research_agent import stream_research_reply
from app.research_search import build_public_web_context, search_public_references


CASES = [
    ("水稻倒伏问题和什么有关，通过上网检索和本地知识来回答我。", ("倒伏",)),
    ("水稻稻瘟病和什么问题有关，通过上网检索和本地知识来回答我。", ("稻瘟病",)),
    ("玉米密植会怎样影响产量？请查找网络资料回答。", ("密植", "密度")),
]


async def main(answer: bool):
    provider = provider_settings()
    for index, (question, subjects) in enumerate(CASES):
        plan = await plan_dialogue(provider=provider, question=question, history=[])
        print("PLAN", json.dumps(plan.model_dump(), ensure_ascii=False), flush=True)
        assert plan.needs_web and any(subject in plan.web_question for subject in subjects)
        assert "知识库" not in plan.web_query and "回答我" not in plan.web_query
        sources, note = await search_public_references(question, model_requested=plan.needs_web,
            research_question=plan.web_question, search_query=plan.web_query, provider=provider)
        print("SOURCES", json.dumps([{"title": item.title, "url": item.url,
            "method": item.retrieval_method} for item in sources], ensure_ascii=False), flush=True)
        print("NOTICE", note, flush=True)
        assert sources, (question, note)
        assert all("anythingllm" not in (item.title + item.url).lower() for item in sources)
        if answer and index == 0:
            context = build_public_web_context(sources, note, question=question,
                research_question=plan.web_question, search_query=plan.web_query)
            async for event in stream_research_reply(user_prompt=question,
                    evidence_context="本次问题未检索到可用于支撑结论的本地知识库资料。",
                    public_web_context=context, memory_state=None,
                    response_guidance=dialogue_response_guidance(plan)):
                if event.get("type") == "complete":
                    content = event["content"]
                    print("ANSWER", event.get("response_mode"), content, flush=True)
                    assert any(item.url in content for item in sources), "Answer must cite a retrieved source"
                    assert "没有返回与水稻倒伏直接相关" not in content
    print("LIVE_PUBLIC_RESEARCH_OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--answer", action="store_true")
    options = parser.parse_args()
    if not options.run_live:
        parser.error("Use --run-live to run paid provider checks")
    asyncio.run(main(options.answer))

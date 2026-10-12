"""Real public literature recommendation checks, with no database writes."""
import argparse
import asyncio
import json

from app.ai_gateway import provider_settings
from app.dialogue_orchestration import dialogue_response_guidance, plan_dialogue
from app.research_agent import stream_research_reply
from app.research_search import build_public_web_context, search_public_references


async def main():
    provider = provider_settings()
    for index, question in enumerate((
        "检索一篇目前最火的水稻育种相关的文献。",
        "推荐一篇最有影响力的水稻育种综述，并说明依据。",
    )):
        plan = await plan_dialogue(provider=provider, question=question, history=[])
        print("PLAN", json.dumps(plan.model_dump(), ensure_ascii=False), flush=True)
        sources, note = await search_public_references(question, model_requested=plan.needs_web,
            research_question=plan.web_question, search_query=plan.web_query, provider=provider)
        print("SOURCES", json.dumps([{"title": x.title, "url": x.url} for x in sources], ensure_ascii=False), flush=True)
        print("NOTICE", note, flush=True)
        assert sources, "A missing popularity rank must not discard every relevant paper"
        if index == 0:
            context = build_public_web_context(sources, note, question=question,
                research_question=plan.web_question, search_query=plan.web_query)
            async for event in stream_research_reply(user_prompt=question, memory_state=None,
                    evidence_context="本次问题未检索到可用于支撑结论的本地知识库资料。",
                    public_web_context=context, response_guidance=dialogue_response_guidance(plan)):
                if event.get("type") == "complete":
                    answer = event["content"]
                    print("ANSWER", answer, flush=True)
                    assert any(x.url in answer for x in sources)
                    assert "暂不能依据本轮检索结果给出具体结论" not in answer
    print("LIVE_PUBLIC_LITERATURE_OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true")
    if not parser.parse_args().run_live:
        parser.error("Use --run-live to execute paid provider checks")
    asyncio.run(main())

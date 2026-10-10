"""Opt-in real server/model read-only checks, no users or sample rows created."""
import argparse
import asyncio

from app.ai_gateway import provider_settings, prepare_egress
from app.dialogue_orchestration import plan_dialogue, dialogue_response_guidance, validate_fact_measurements
from app.main import SessionLocal, build_dialogue_evidence
from app.business_data_query import load_business_catalog
from app.research_agent import stream_research_reply


async def main():
    provider=provider_settings()
    with SessionLocal() as session:
        shared=load_business_catalog(session)
        all_data=load_business_catalog(session,admin=True)
        print('CATALOG',len(shared),len(all_data),flush=True)
        assert 'core.genotype_call' in {r['dataset_id'] for r in shared}
        assert 'ai.ricedata_five_trait_comprehensive_evaluation' in {r['dataset_id'] for r in shared}
        assert 'governance.qc_issue' not in {r['dataset_id'] for r in shared}
    cases=[
        ('查询D优130已有的五大性状评价结果，说明覆盖度和缺失项',None),
        ('查询基因型样本表中的样本数量，按匹配状态分组',None),
        ('只查院内CXCDD1447的千粒重', '24.69'),
        ('D优130在2006年福建审定的直链淀粉含量是多少', '24.1'),
    ]
    for question,expected in cases:
        plan=await plan_dialogue(provider=provider,question=question,history=[])
        print('PLAN',question,plan.model_dump(),flush=True)
        with SessionLocal() as session:
            context,cards,states=await build_dialogue_evidence(session,plan,question,[],
                actor='服务器只读联调',project_id='',external=provider.external,
                provider=provider,admin=False,model_history=[])
        print('EVIDENCE',[(c.get('title'),c.get('detail')) for c in cards],flush=True)
        assert cards, ('No actual source cards',question,states)
        if expected:
            assert expected in context,(question,'Expected value not in actual query evidence')
        safe=prepare_egress([context],provider=provider)
        async for event in stream_research_reply(user_prompt=question,evidence_context=safe.texts[0],
                memory_state=None,response_guidance=dialogue_response_guidance(plan)):
            if event.get('type')!='complete':
                continue
            if plan.intent=='variety_fact':
                validate_fact_measurements(event['content'],context)
            if expected:
                assert expected in event['content'],(question,'Verified value absent from final answer')
            print('ANSWER',event['content'],flush=True)
    print('LIVE_BUSINESS_CHECK_OK',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-live',action='store_true')
    if not parser.parse_args().run_live:
        parser.error('Use --run-live to authorize model calls')
    asyncio.run(main())

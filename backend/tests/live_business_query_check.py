"""Opt-in real server/model read-only checks, no users or sample rows created."""
import argparse
import asyncio

from app.ai_gateway import provider_settings, prepare_egress
from app.dialogue_orchestration import plan_dialogue, dialogue_response_guidance, polish_business_answer, validate_fact_measurements
from app.ricedata_fact_guard import validate_scoped_fact_answer
from app.main import SessionLocal, build_dialogue_evidence
from app.business_data_query import load_business_catalog
from app.research_agent import stream_research_reply


async def main(only_case: int | None = None):
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
        ('目前院内材料的各类数据完整程度怎么样？请分别列出有数据和缺失的材料数量、覆盖率，并按覆盖率从低到高排序。',
         None),
        ('D优130的直链淀粉含量是多少', None),
        ('D优130在2003年四川审定的直链淀粉含量是多少', None),
        ('抚粳3号（辽粳64）辽宁1997年的亩产是多少', '556.6'),
    ]
    for index,(question,expected) in enumerate(cases, 1):
        if only_case is not None and index != only_case:
            continue
        plan=await plan_dialogue(provider=provider,question=question,history=[])
        print('PLAN',question,plan.model_dump(),flush=True)
        with SessionLocal() as session:
            context,cards,states=await build_dialogue_evidence(session,plan,question,[],
                actor='服务器只读联调',project_id='',external=provider.external,
                provider=provider,admin=False,model_history=[])
        print('EVIDENCE',[(c.get('title'),c.get('detail')) for c in cards],flush=True)
        assert cards, ('No actual source cards',question,states)
        if index == 5:
            assert any(c.get('source_dataset') == 'ai.completeness_summary' for c in cards), (
                'Completeness question did not select cached aggregate', cards)
            assert 'snapshot_refreshed_at' in context, 'Snapshot freshness absent from evidence'
        if expected:
            assert expected in context,(question,'Expected value not in actual query evidence')
        if index == 6:
            assert any(s.get('state') == 'research_clarification' for s in states), states
        safe=prepare_egress([context],provider=provider)
        async for event in stream_research_reply(user_prompt=question,evidence_context=safe.texts[0],
                memory_state=None,response_guidance=dialogue_response_guidance(plan)):
            if event.get('type')!='complete':
                continue
            answer = polish_business_answer(event['content'], question) if plan.intent == 'database' else event['content']
            print('ANSWER',answer,flush=True)
            if index == 5:
                assert '快照' in answer or '截至' in answer, 'Snapshot freshness absent from answer'
            if plan.intent=='variety_fact':
                validate_fact_measurements(answer,context)
                validate_scoped_fact_answer(answer, next(
                    (state for state in states if state.get('state') == 'numeric_fact_scope'), None))
            if expected:
                assert expected in answer,(question,'Verified value absent from final answer')
            if index in {6, 7}:
                assert '24.1%' not in answer, (question, 'Borrowed a value from the Fujian approval')
            if index == 8:
                assert '待核对' in answer or '候选' in answer, 'Raw-text candidate was presented as confirmed'
    print('LIVE_BUSINESS_CHECK_OK',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-live',action='store_true')
    parser.add_argument('--case',type=int,choices=range(1,9))
    args=parser.parse_args()
    if not args.run_live:
        parser.error('Use --run-live to authorize model calls')
    asyncio.run(main(args.case))

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.ai_gateway import AIProviderSettings
from app.business_data_query import (
    BusinessPlan, BusinessQueryError, DataQuery, build_business_evidence,
    compile_business_query, execute_business_query, load_business_catalog, _safe_value,
    _subject_source_hints,
)
from app.research_agent import ResearchAgentError


def dataset(name, columns):
    return {"dataset_id":name,"query_view":name.replace('.', '__'),"columns":[{"name":c,"type":"text"} for c in columns]}


class QueryCompilerTests(unittest.TestCase):
    catalog = [dataset('core.material', ['material_id','preferred_name','has_genotype']),
               dataset('core.phenotype_value', ['material_id','trait_code','value_numeric']),
               dataset('ricedata.rice_variety', ['variety_id','variety_name']),
               dataset('public.variety_basic', ['variety_id','variety_name'])]

    def test_filter_values_are_bound_not_executable_sql(self):
        attack = "x%' OR 1=1; DROP TABLE core.material; --"
        query = DataQuery.model_validate({"relation":"core.material", "columns":["material_id"],
            "filters":[{"field":"preferred_name","op":"contains","value":attack}]})
        sql, params = compile_business_query(query,self.catalog)
        self.assertNotIn('DROP',sql)
        self.assertIn(':v0',sql)
        self.assertIn("DROP",params['v0'])
        self.assertIn('\\%',params['v0'])

    def test_unknown_schema_private_table_and_sql_expression_are_rejected(self):
        for relation in ('keycloak.user_entity','public.research_message','core.material;DROP TABLE x'):
            with self.assertRaises(BusinessQueryError):
                compile_business_query(DataQuery(relation=relation),self.catalog)
        for column in ('password','pg_sleep(60)','preferred_name; SELECT 1'):
            with self.assertRaises(BusinessQueryError):
                compile_business_query(DataQuery(relation='core.material',columns=[column]),self.catalog)
        with self.assertRaises(ValidationError):
            DataQuery.model_validate({'relation':'core.material','sql':'delete from x'})

    def test_pagination_and_plan_sizes_are_bounded(self):
        for fields in ({'limit':101},{'offset':10001},{'limit':0},{'joins':[{}]*4}):
            with self.assertRaises(ValidationError):
                DataQuery.model_validate({'relation':'core.material',**fields})
        with self.assertRaises(ValidationError):
            BusinessPlan.model_validate({'queries':[{'relation':'core.material'}]*5})
        with self.assertRaises(BusinessQueryError):
            compile_business_query(DataQuery(relation='core.material',offset=30),self.catalog)

    def test_semantic_join_and_aggregation(self):
        query = DataQuery.model_validate({'relation':'core.material','columns':['a0.material_id'],
            'joins':[{'relation':'core.phenotype_value','left':'a0.material_id','right':'material_id'}],
            'aggregates':[{'field':'a1.value_numeric','op':'avg','alias':'mean_value'}],
            'group_by':['a0.material_id'],'order_by':[{'field':'mean_value','direction':'desc'}]})
        sql,_=compile_business_query(query,self.catalog)
        self.assertIn('LEFT JOIN',sql)
        self.assertIn('avg(a1."value_numeric")',sql)
        self.assertIn('GROUP BY',sql)
        self.assertIn('"mean_value" DESC',sql)

    def test_unproven_identity_join_is_rejected(self):
        for left,right in (('preferred_name','trait_code'),('material_id','trait_code')):
            with self.assertRaises(BusinessQueryError):
                compile_business_query(DataQuery.model_validate({'relation':'core.material',
                    'joins':[{'relation':'core.phenotype_value','left':left,'right':right}]}),self.catalog)
        with self.assertRaises(BusinessQueryError):
            compile_business_query(DataQuery.model_validate({'relation':'ricedata.rice_variety',
                'joins':[{'relation':'public.variety_basic','left':'variety_id','right':'variety_id'}]}),self.catalog)

    def test_embedded_secret_and_path_keys_are_removed(self):
        clean=_safe_value({'material_id':'MAT-1','metadata':{'storage_path':'/sensitive','password':'secret',
                        'trait':{'value':24.1},'email':'owner@example.com'}})
        self.assertEqual(clean['metadata'],{'trait':{'value':24.1}})

    def test_shared_flag_does_not_allow_private_attachments_or_credentials(self):
        from app.ai_gateway import prepare_egress, AIEgressBlockedError
        provider=AIProviderSettings('cherryin','https://example.invalid/v1','test','',True)
        with patch.dict(os.environ, {'ALLOW_SHARED_BUSINESS_EGRESS':'true'}):
            with self.assertRaises(AIEgressBlockedError):
                prepare_egress(['private research'],provider=provider,contains_private_material=True)
            with self.assertRaises(AIEgressBlockedError):
                prepare_egress(['password=not-a-real-credential'],provider=provider)


@unittest.skipUnless(os.getenv('TEST_RICEDATA_DSN'),'Set TEST_RICEDATA_DSN')
class RealBusinessApiTests(unittest.TestCase):
    def setUp(self):
        import uuid
        from fastapi.testclient import TestClient
        from app import main, auth
        self.main, self.auth = main, auth
        self.engine=create_engine(os.environ['TEST_RICEDATA_DSN'])
        self.connection=self.engine.connect()
        self.transaction=self.connection.begin()
        self.session=Session(bind=self.connection,join_transaction_mode='create_savepoint')
        self.users={}
        for role in ('researcher','field_admin'):
            suffix=str(uuid.uuid4())
            user=auth.CurrentUser(suffix,'business-test-'+suffix,'Rollback only',frozenset({role}))
            self.users[role]=user
            self.session.add(main.PlatformAccount(username=user.username,display_name=user.display_name,
                business_role=role,keycloak_subject=user.id,institution_id=main.INSTITUTION_ID,active=True))
        self.session.flush()
        self.user=self.users['researcher']
        main.app.dependency_overrides[auth.get_current_user]=lambda:self.user
        main.app.dependency_overrides[main.get_business_query_session]=lambda:self.session
        self.client=TestClient(main.app,base_url='http://localhost')

    def tearDown(self):
        self.client.close()
        self.main.app.dependency_overrides.clear()
        self.session.close()
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def test_researcher_and_admin_catalogs_have_distinct_scopes(self):
        response=self.client.get('/api/research/business-data/catalog')
        self.assertEqual(response.status_code,200,response.text)
        shared={d['dataset_id'] for d in response.json()['datasets']}
        self.assertIn('core.genotype_call',shared)
        self.assertNotIn('governance.qc_issue',shared)
        self.user=self.users['field_admin']
        response=self.client.get('/api/research/business-data/catalog')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('governance.qc_issue',{d['dataset_id'] for d in response.json()['datasets']})

    def test_real_read_is_audited_and_private_or_admin_reads_are_rejected(self):
        response=self.client.post('/api/research/business-data/query',json={
            'relation':'ricedata.rice_variety','columns':['variety_name'],
            'filters':[{'field':'source_variety_id','op':'eq','value':'601360'}]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('D优130',response.json()['rows'][0]['variety_name'])
        count=self.session.scalar(text("SELECT count(*) FROM public.permission_audit WHERE actor_id=:actor AND action='business_data_read'"),{'actor':self.user.id})
        self.assertEqual(count,1)
        for relation in ('governance.qc_issue','public.research_message','keycloak.user_entity'):
            response=self.client.post('/api/research/business-data/query',json={'relation':relation})
            self.assertEqual(response.status_code,422,response.text)
        response=self.client.post('/api/research/business-data/query',json={'relation':'core.genomic_asset','columns':['storage_path']})
        self.assertEqual(response.status_code,422,response.text)

    def test_unapproved_account_is_denied_before_query(self):
        from app.auth import CurrentUser
        self.user=CurrentUser('unapproved-test','nonexistent-business-account','Not approved',frozenset({'researcher'}))
        response=self.client.post('/api/research/business-data/query',json={'relation':'core.material'})
        self.assertEqual(response.status_code,403,response.text)


@unittest.skipUnless(os.getenv('TEST_RICEDATA_DSN'),'Set TEST_RICEDATA_DSN')
class RealBusinessQueryTests(unittest.TestCase):
    def setUp(self):
        self.egress = patch.dict(os.environ, {'ALLOW_SHARED_BUSINESS_EGRESS':'true'})
        self.egress.start()
        self.addCleanup(self.egress.stop)
        self.engine=create_engine(os.environ['TEST_RICEDATA_DSN'])
        self.session=Session(self.engine)
        if not self.session.scalar(text("select to_regclass('agent_query.dataset_registry')")):
            self.session.close(); self.engine.dispose(); self.skipTest('Apply catalog migration first')
    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def test_shared_catalog_covers_core_ai_reference_and_keeps_admin_private_separate(self):
        shared=load_business_catalog(self.session)
        admin=load_business_catalog(self.session,admin=True)
        ids={r['dataset_id'] for r in shared}
        self.assertIn('core.genotype_call',ids)
        self.assertIn('ai.ricedata_five_trait_comprehensive_evaluation',ids)
        self.assertIn('raw.source_record',ids)
        self.assertNotIn('governance.qc_issue',ids)
        self.assertIn('governance.qc_issue',{r['dataset_id'] for r in admin})
        self.assertNotIn('public.research_message',{r['dataset_id'] for r in admin})
        self.assertNotIn('public.platform_account',{r['dataset_id'] for r in admin})
        self.assertTrue(all(not r['dataset_id'].startswith('keycloak.') for r in admin))
        for r in admin:
            self.assertFalse(any('path' in c['name'] or 'password' in c['name'] for c in r['columns']))

    def test_real_rows_and_missing_rows_are_not_samples(self):
        result=execute_business_query(self.session,DataQuery.model_validate({'relation':'ricedata.rice_variety',
            'columns':['source_variety_id','variety_name'],'filters':[{'field':'source_variety_id','op':'eq','value':'601360'}]}))
        self.assertEqual(result['returned_rows'],1)
        self.assertIn('D优130',result['rows'][0]['variety_name'])
        missing=execute_business_query(self.session,DataQuery.model_validate({'relation':'ricedata.rice_variety',
            'filters':[{'field':'source_variety_id','op':'eq','value':'NONEXISTENT-TEST-99999'}]}))
        self.assertEqual(missing['rows'],[])
        self.assertEqual(missing['status'],'no_matching_rows')

    def test_unauthorized_admin_dataset_and_hidden_columns_fail_closed(self):
        with self.assertRaises(BusinessQueryError):
            execute_business_query(self.session,DataQuery(relation='governance.qc_issue'))
        with self.assertRaises(BusinessQueryError):
            execute_business_query(self.session,DataQuery(relation='core.genomic_asset',columns=['storage_path']))

    def test_identity_register_distinguishes_public_variety_from_institute_material(self):
        hints=_subject_source_hints(self.session,'查询D优130已有五性评价')
        self.assertTrue(any(h['source']=='ricedata' and h['name']=='D优130' for h in hints))
        self.assertFalse(any(h['source']=='core' and h['name']=='D优130' for h in hints))
        provider=AIProviderSettings('cherryin','https://example.invalid/v1','test','',True)
        stages=[{'relations':['ai.ricedata_five_trait_comprehensive_evaluation'],'clarification':''},
            {'queries':[{'relation':'ai.ricedata_five_trait_comprehensive_evaluation',
                'columns':['variety_name','scored_dimension_count','missing_dimensions'],
                'filters':[{'field':'variety_name','op':'eq','value':'D优130'}]}],'clarification':''}]
        with patch('app.business_data_query.model_json_request',new=AsyncMock(side_effect=stages)) as model:
            context,_,_=asyncio.run(build_business_evidence(self.session,'查询D优130已有五性评价',provider=provider))
        selection_input=model.await_args_list[0].kwargs['data']
        self.assertTrue(any(h['source']=='ricedata' for h in selection_input['matched_subjects']))
        self.assertFalse(any(r['dataset']=='ai.five_trait_comprehensive_evaluation' for r in selection_input['catalog']))
        self.assertIn('D优130',context)

    def test_model_selection_and_plan_result_become_query_evidence(self):
        provider=AIProviderSettings('cherryin','https://example.invalid/v1','test','',True)
        stages=[{'relations':['ricedata.rice_variety'],'clarification':''},
                {'queries':[{'relation':'ricedata.rice_variety','columns':['variety_name'],
                  'filters':[{'field':'source_variety_id','op':'eq','value':'601360'}]}],'clarification':''}]
        with patch('app.business_data_query.model_json_request',new=AsyncMock(side_effect=stages)) as model:
            context,cards,states=asyncio.run(build_business_evidence(self.session,'D优130的品种名称',provider=provider))
        self.assertEqual(model.await_count,2)
        self.assertIn('D优130',context)
        self.assertEqual(cards[0]['type'],'shared_business_database')
        self.assertEqual(states[0]['egress'],'approved_shared')

    def test_model_unknown_dataset_does_not_execute_queries(self):
        provider=AIProviderSettings('cherryin','https://example.invalid/v1','test','',True)
        with patch('app.business_data_query.model_json_request',new=AsyncMock(return_value={'relations':['public.research_message'],'clarification':''})), \
             patch('app.business_data_query.execute_business_query') as execute:
            with self.assertRaises(ResearchAgentError):
                asyncio.run(build_business_evidence(self.session,'查看私人会话',provider=provider))
            execute.assert_not_called()

    def test_external_business_analysis_requires_explicit_approval(self):
        provider=AIProviderSettings('cherryin','https://example.invalid/v1','test','',True)
        with patch.dict(os.environ, {'ALLOW_SHARED_BUSINESS_EGRESS':'false'}), \
             patch('app.business_data_query.model_json_request') as model:
            with self.assertRaisesRegex(ResearchAgentError, '尚未授权'):
                asyncio.run(build_business_evidence(self.session,'院内数据',provider=provider))
            model.assert_not_called()

    def test_invalid_proxy_name_can_be_corrected_but_cannot_execute(self):
        provider=AIProviderSettings('cherryin','https://example.invalid/v1','test','',True)
        stages=[{'relations':['ricedata.rice_variety'],'clarification':''},
            {'queries':[{'relation':'ricedata__rice_variety'}],'clarification':''},
            {'queries':[{'relation':'ricedata.rice_variety','columns':['variety_name'],
                'filters':[{'field':'source_variety_id','op':'eq','value':'601360'}]}],'clarification':''}]
        with patch('app.business_data_query.model_json_request',new=AsyncMock(side_effect=stages)) as model:
            context,_,_=asyncio.run(build_business_evidence(self.session,'D优130的名称',provider=provider))
        self.assertEqual(model.await_count,3)
        self.assertIn('D优130',context)
        schema_input=model.await_args_list[1].kwargs['data']['datasets'][0]
        self.assertNotIn('query_view',schema_input)
        self.assertIn('correction',model.await_args_list[2].kwargs['data'])


if __name__=='__main__': unittest.main()

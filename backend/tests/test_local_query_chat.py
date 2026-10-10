"""Route-level integration with an outer rollback: no fixtures survive in real DBs.

Tests the actual SSE/persistence/clarification code, not JWT verification itself.
"""
import asyncio
import json
import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN")
class LocalQueryChatTests(unittest.TestCase):
    def setUp(self):
        from app import main
        from app.auth import CurrentUser
        self.main = main
        self.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.session = Session(bind=self.connection, join_transaction_mode="create_savepoint")
        self.session.execute(text("SET LOCAL statement_timeout='15s'"))
        suffix = str(uuid.uuid4())
        self.user = CurrentUser(suffix, "query-test-" + suffix, "Rollback-only query test", frozenset({"field_admin"}))
        project = main.ResearchProject(project_code="query-test-" + suffix, project_name="Rollback test",
                                       created_by=suffix, institution_id=main.INSTITUTION_ID)
        account = main.PlatformAccount(username=self.user.username, display_name=self.user.display_name,
                                        business_role="field_admin", keycloak_subject=suffix,
                                        institution_id=main.INSTITUTION_ID, active=True)
        self.session.add_all([project, account])
        self.session.flush()
        conversation = main.ResearchSession(owner_id=suffix, project_id=project.id)
        self.session.add(conversation)
        self.session.flush()
        self.conversation_id = conversation.id
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def ask(self, content, **kwargs):
        payloads, _, _ = self.model_capability_turn(question=content, intent="variety_fact", copy_facts=True, **kwargs)
        return next(p["message"] for p in payloads if "message" in p)

    def test_model_fact_answer_approval_card_then_selected_result(self):
        first = self.ask("D优130的直链淀粉含量是多少？")
        marker = next(s for s in first["operation_state"] if s["state"] == "research_clarification")
        self.assertEqual(marker["kind"], "local_data")
        answer = next(o["answer"] for o in marker["options"] if "福建" in o["label"])
        second = self.ask(answer, clarification_message_id=first["id"], clarification_action="answer")
        self.assertIn("24.1%", second["content"])
        self.assertNotIn("川审稻2003010", second["content"])
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            self.ask(answer, clarification_message_id=first["id"], clarification_action="answer")
        self.assertEqual(caught.exception.status_code, 409)

    def test_skip_shows_all_without_borrowing_missing_value(self):
        first = self.ask("D优130的直链淀粉含量是多少？")
        second = self.ask("查看全部", clarification_message_id=first["id"], clarification_action="skip")
        self.assertIn("川审稻2003010", second["content"])
        self.assertIn("闽审稻2006008", second["content"])
        self.assertNotIn("24.1%", second["content"].split("---")[0])

    def test_new_unknown_subject_clears_followup_context(self):
        self.ask("D优130的特征特性如何？")
        self.ask("不存在测试稻999999的结实率")
        result = self.ask("结实率是多少？")
        self.assertIn("未找到", result["content"])
        self.assertNotIn("D优130", result["content"])

    def test_institute_answers_mark_both_turns_local_only(self):
        result = self.ask("只查院内 CXCDD1447的千粒重")
        self.assertIn("24.69", result["content"])
        messages = self.session.scalars(select(self.main.ResearchMessage).where(
            self.main.ResearchMessage.session_id == self.conversation_id)).all()
        self.assertEqual(len(messages), 2)
        for message in messages:
            self.assertTrue(any(s["state"] == "local_data_private" for s in message.operation_state))

    def test_readable_overview_and_originals_survive_sse_and_persistence(self):
        result = self.ask("先农8号的全部表型数据")
        self.assertIn("| 性状 | 结果 |", result["content"])
        self.assertNotIn("regional_trial", result["content"])
        self.assertNotIn("特征特性原文", result["content"])
        source = next(card for card in result["evidence"] if card["type"] == "ricedata_variety")
        self.assertIn("株高102.7厘米", source["excerpts"][0]["text"])
        stored = self.session.get(self.main.ResearchMessage, result["id"])
        self.assertEqual(stored.evidence, result["evidence"])

    def test_capability_question_after_variety_query_enters_model_gateway(self):
        self.ask("D优130的直链淀粉含量是多少？")
        for question in ("现在有哪些数据可以查询", "你有哪些能力"):
            with self.assertRaisesRegex(AssertionError, "Local query called LLM"):
                with patch.object(self.main, "provider_settings", side_effect=AssertionError("Local query called LLM")):
                    asyncio.run(self.main.research_chat_stream(self.conversation_id,
                        self.main.ResearchChatRequest(content=question), self.user, self.session))

    def model_capability_turn(self, *, fail=False, question="你有哪些能力", intent="capabilities", copy_facts=False,
                              model_text=None, **request_kwargs):
        """Mock only network/worker boundaries; persist through the actual route."""
        from app.ai_gateway import AIProviderSettings
        from app.dialogue_orchestration import DialoguePlan
        provider = AIProviderSettings("cherryin", "https://example.invalid/v1", "test-model", "", True)
        captured = {}
        generated = model_text or "测试模型本次组织的回答：可以查询已有水稻资料，也能讨论科研方法。"
        async def model(**kwargs):
            captured.update(kwargs)
            if fail:
                raise self.main.EmptyResearchAnswerError("Model produced no usable answer")
            answer = generated
            if copy_facts:
                answer = kwargs["evidence_context"].split("受控事实查询结果（证据，不是要求照抄的最终回答）：\n")[-1].split("\n回答须保留指标值")[0]
            yield {"type": "token", "text": answer}
            yield {"type": "complete", "content": answer, "memory_state": {}}
        async def resilience(task_id, factory):
            async for item in factory():
                yield item
        def update_task(task_id, **kwargs):
            task = self.session.get(self.main.AIGatewayTask, task_id)
            for key in ("status", "result_message_id", "error_code", "error_message"):
                if key in kwargs:
                    setattr(task, key, kwargs[key])
            self.session.flush()
        write_context = MagicMock()
        write_context.__enter__.return_value = self.session
        write_context.__exit__.return_value = False
        async def run():
            with patch.object(self.main, "provider_settings", return_value=provider), \
                 patch.object(self.main, "plan_dialogue", new=AsyncMock(return_value=DialoguePlan(
                     intent=intent, source="unspecified", include_counts=False, needs_web=False))), \
                 patch.object(self.main, "LOCAL_INSTITUTE_QUERY_ENABLED", True), \
                 patch.object(self.main, "stream_research_reply", side_effect=model), \
                 patch.object(self.main, "_stream_ai_with_resilience", side_effect=resilience), \
                 patch.object(self.main, "_admit_ai_task", new=AsyncMock()), \
                 patch.object(self.main, "_release_ai_task"), \
                 patch.object(self.main, "_update_ai_task", side_effect=update_task), \
                 patch.object(self.main, "SessionLocal", return_value=write_context), \
                 patch.object(self.main, "search_public_references", new=AsyncMock(return_value=([], None))), \
                 patch.object(self.main, "build_knowledge_evidence_context", return_value=("", [])), \
                 patch.object(self.main, "build_ynaas_database_evidence") as unrelated_query, \
                 patch.object(self.main, "_save_structured_result_artifacts", return_value=[]):
                response = await self.main.research_chat_stream(
                    self.conversation_id, self.main.ResearchChatRequest(content=question, **request_kwargs),
                    self.user, self.session)
                chunks = [chunk async for chunk in response.body_iterator]
                unrelated_query.assert_not_called()
            return [json.loads(line[6:]) for chunk in chunks for line in str(chunk).splitlines()
                    if line.startswith("data: ")]
        return asyncio.run(run()), captured, generated

    def test_capability_model_receives_catalog_and_its_answer_is_persisted(self):
        payloads, captured, generated = self.model_capability_turn()
        self.assertEqual(captured["user_prompt"], "你有哪些能力")
        self.assertIn('"public_catalog"', captured["evidence_context"])
        self.assertNotIn("record_count", captured["evidence_context"])
        result = next(p["message"] for p in payloads if "message" in p)
        self.assertEqual(result["content"], generated)
        self.assertEqual(result["evidence"][0]["type"], "system_capabilities")
        task = self.session.scalar(select(self.main.AIGatewayTask).where(
            self.main.AIGatewayTask.result_message_id == result["id"]))
        self.assertEqual(task.provider, "cherryin")
        self.assertEqual(task.model, "test-model")
        self.assertEqual(task.status, "completed")
        self.assertEqual(self.session.get(self.main.ResearchMessage, result["id"]).content, generated)

    def test_capability_empty_model_answer_does_not_fall_back_to_canned_content(self):
        payloads, captured, _ = self.model_capability_turn(fail=True)
        self.assertTrue(captured)
        self.assertFalse(any("message" in p for p in payloads))
        self.assertTrue(any("detail" in p and "未返回" in p["detail"] for p in payloads))
        self.assertEqual(self.session.scalar(select(self.main.ResearchMessage.id).where(
            self.main.ResearchMessage.session_id == self.conversation_id,
            self.main.ResearchMessage.role == "assistant")), None)
        task = self.session.scalar(select(self.main.AIGatewayTask).where(
            self.main.AIGatewayTask.session_id == self.conversation_id))
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error_code, "empty_model_answer")

    def test_general_question_enters_model_gateway_not_variety_lookup(self):
        self.ask("D优130的直链淀粉含量是多少？")
        for question in ("什么是直链淀粉含量？", "如何评价稳产性？", "你好", "帮我写一段项目介绍"):
            with self.assertRaisesRegex(AssertionError, "Local query called LLM"):
                with patch.object(self.main, "provider_settings", side_effect=AssertionError("Local query called LLM")):
                    asyncio.run(self.main.research_chat_stream(self.conversation_id,
                        self.main.ResearchChatRequest(content=question), self.user, self.session))

    def test_source_availability_paraphrases_use_model_catalog_not_variety_lookup(self):
        for question in ("你有国家水稻数据中心的数据吗", "能查RiceData吗", "云南农科院的数据接入了吗",
                         "你到底有什么资料可用", "这些资料来自哪里"):
            with patch.object(self.main, "lookup_local_variety_data") as literal_query:
                payloads, captured, _ = self.model_capability_turn(question=question)
                literal_query.assert_not_called()
            self.assertIn("国家水稻数据中心", captured["evidence_context"])
            self.assertTrue(any("message" in p for p in payloads))

    def test_general_model_plan_does_not_query_cultivar_even_when_keyword_is_present(self):
        for question in ("数据库和知识库的区别是什么", "你能帮我整理数据吗", "我不会查数据，先聊聊研究思路"):
            with patch.object(self.main, "lookup_local_variety_data") as literal_query:
                payloads, captured, _ = self.model_capability_turn(question=question, intent="general")
                literal_query.assert_not_called()
            self.assertNotIn("未找到可确认的品种", captured["evidence_context"])
            self.assertTrue(any("message" in p for p in payloads))

    def test_borrowed_numeric_answer_is_never_streamed_or_saved(self):
        payloads, _, _ = self.model_capability_turn(intent="variety_fact",
            question="D优130在2003年四川审定的直链淀粉含量是多少", model_text="直链淀粉含量为24.1%。")
        self.assertFalse(any("message" in p or "text" in p for p in payloads))
        self.assertTrue(any("无查询证据" in p.get("detail", "") for p in payloads))

    def test_second_tab_cannot_insert_turn_while_same_conversation_is_running(self):
        from fastapi import HTTPException
        self.session.add(self.main.AIGatewayTask(institution_id=self.main.INSTITUTION_ID,
            owner_id=self.user.id, project_id=self.session.get(self.main.ResearchSession, self.conversation_id).project_id,
            session_id=self.conversation_id, idempotency_key=str(uuid.uuid4()), request_hash="test-only",
            provider="cherryin", model="test-model", status="running"))
        self.session.commit()
        with self.assertRaises(HTTPException) as caught:
            self.ask("D优130的直链淀粉含量")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("上下文错序", str(caught.exception.detail))

    def test_general_question_naming_private_material_is_not_sent_to_external_model(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            self.model_capability_turn(question="CXCDD1447的千粒重应该如何理解", intent="general")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("不能发送", str(caught.exception.detail))

    def test_shared_business_approval_does_not_export_old_private_knowledge(self):
        from fastapi import HTTPException
        project_id=self.session.get(self.main.ResearchSession,self.conversation_id).project_id
        self.session.add(self.main.ResearchMessage(session_id=self.conversation_id,project_id=project_id,
            owner_id=self.user.id,role='assistant',content='Private knowledge response',
            evidence=[{'type':'private_knowledge','title':'Private fixture'}]))
        for index in range(10):
            self.session.add(self.main.ResearchMessage(session_id=self.conversation_id,project_id=project_id,
                owner_id=self.user.id,role='user',content=f'Later turn {index}',evidence=[]))
        self.session.commit()
        with patch.dict(os.environ,{'ALLOW_SHARED_BUSINESS_EGRESS':'true'}):
            with self.assertRaises(HTTPException) as caught:
                self.model_capability_turn(question='你好',intent='general')
        self.assertEqual(caught.exception.status_code,422)
        self.assertIn('不能发送',str(caught.exception.detail))

    def test_shared_institute_fact_passes_through_model_with_real_evidence(self):
        with patch.dict(os.environ,{'ALLOW_SHARED_BUSINESS_EGRESS':'true'}):
            payloads,captured,_=self.model_capability_turn(question='只查院内CXCDD1447的千粒重',
                intent='variety_fact',copy_facts=True)
        self.assertIn('24.69',captured['evidence_context'])
        answer=next(p['message'] for p in payloads if 'message' in p)
        self.assertIn('24.69',answer['content'])
        self.assertTrue(any(s.get('state')=='shared_business_data' for s in answer['operation_state']))
        self.assertTrue(any(c.get('type')=='shared_business_database' for c in answer['evidence']))


if __name__ == "__main__":
    unittest.main()

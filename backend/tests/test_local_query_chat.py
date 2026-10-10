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
        async def run():
            with patch.object(self.main, "provider_settings", side_effect=AssertionError("Local query called LLM")), \
                 patch.object(self.main, "LOCAL_INSTITUTE_QUERY_ENABLED", True):
                response = await self.main.research_chat_stream(
                    self.conversation_id, self.main.ResearchChatRequest(content=content, **kwargs),
                    self.user, self.session)
                chunks = [chunk async for chunk in response.body_iterator]
            payloads = [json.loads(line[6:]) for chunk in chunks for line in str(chunk).splitlines()
                        if line.startswith("data: ")]
            return next(p["message"] for p in payloads if "message" in p)
        return asyncio.run(run())

    def test_approval_card_then_selected_result_without_llm(self):
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
                self.ask(question)

    def model_capability_turn(self, *, fail=False):
        """Mock only network/worker boundaries; persist through the actual route."""
        from app.ai_gateway import AIProviderSettings
        provider = AIProviderSettings("cherryin", "https://example.invalid/v1", "test-model", "", True)
        captured = {}
        generated = "测试模型本次组织的回答：可以查询已有水稻资料，也能讨论科研方法。"
        async def model(**kwargs):
            captured.update(kwargs)
            if fail:
                raise self.main.EmptyResearchAnswerError("Model produced no usable answer")
            yield {"type": "token", "text": generated}
            yield {"type": "complete", "content": generated, "memory_state": {}}
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
                    self.conversation_id, self.main.ResearchChatRequest(content="你有哪些能力"),
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
                self.ask(question)


if __name__ == "__main__":
    unittest.main()

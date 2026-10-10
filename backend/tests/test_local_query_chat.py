"""Route-level integration with an outer rollback: no fixtures survive in real DBs.

Tests the actual SSE/persistence/clarification code, not JWT verification itself.
"""
import asyncio
import json
import os
import unittest
import uuid
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()

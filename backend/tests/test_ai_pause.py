"""The pause control must keep one in-flight task, never submit another turn."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class AIPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_then_resume_wakes_the_same_stream(self):
        from app import main

        task = SimpleNamespace(id="task-1", institution_id="institute-1", owner_id="user-1",
                               task_type="research_chat", status="running", updated_at=None)
        user = SimpleNamespace(id="user-1")
        db = MagicMock()
        db.scalar.return_value = task
        with patch.object(main, "sync_platform_account", return_value=SimpleNamespace(institution_id="institute-1")), \
             patch.object(main, "_record_ai_audit"), \
             patch.object(main, "_serialize_ai_task", side_effect=lambda row: {"id": row.id, "status": row.status}), \
             patch.object(main, "_load_ai_task", return_value=task), \
             patch.object(main, "_raise_if_ai_task_cancelled"):
            main.AI_TASK_RUNNING_COROUTINES[task.id] = asyncio.current_task()
            try:
                paused = await main.pause_ai_task(task.id, user, db)
                self.assertEqual(paused, {"id": task.id, "status": "paused"})
                stream = main._ai_pause_events(task.id)
                self.assertIn("当前任务已暂停", await stream.__anext__())
                pending = asyncio.create_task(stream.__anext__())
                await asyncio.sleep(0.02)
                self.assertFalse(pending.done())
                resumed = await main.resume_ai_task(task.id, user, db)
                self.assertEqual(resumed, {"id": task.id, "status": "running"})
                self.assertIn("已继续执行当前任务", await asyncio.wait_for(pending, 1))
                with self.assertRaises(StopAsyncIteration):
                    await stream.__anext__()
                self.assertEqual(db.commit.call_count, 2)
            finally:
                main.AI_TASK_RUNNING_COROUTINES.pop(task.id, None)
                main.AI_TASK_RESUME_EVENTS.pop(task.id, None)

    async def test_paused_time_does_not_consume_model_timeout(self):
        from app import main

        task = SimpleNamespace(attempt_count=1, max_attempts=1, timeout_seconds=0.1)

        async def provider():
            yield {"type": "token", "text": "第一段"}
            yield {"type": "complete", "content": "第一段回答"}

        with patch.object(main, "_load_ai_task", return_value=task), \
             patch.object(main, "_raise_if_ai_task_cancelled"):
            stream = main._stream_ai_with_resilience("task-1", provider)
            self.assertEqual((await stream.__anext__())["text"], "第一段")
            await asyncio.sleep(0.2)
            self.assertEqual((await stream.__anext__())["content"], "第一段回答")
            with self.assertRaises(StopAsyncIteration):
                await stream.__anext__()

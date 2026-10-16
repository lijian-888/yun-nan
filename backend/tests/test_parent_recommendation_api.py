import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.ai_gateway import AIProviderSettings
from app.parent_goal_parser import parse_parent_goal
from app.parent_recommendation_api import RecommendationRunPayload, goal_progress_events
from app.parent_recommendation_engine import ParentRecommendationEngine
from app.parent_recommendation_report import recommendation_v2_csv, recommendation_v2_pdf
from backend.tests.test_parent_recommendation_engine import MemoryRiceDataRepository, candidate, request


class ParentRecommendationApiTests(unittest.TestCase):
    condition = {
        "code": "growth", "label": "生育期120～150天", "field": "growth_duration_days",
        "operator": "between", "value": [120, 150], "unit": "天", "kind": "hard",
    }

    def valid_payload(self, **overrides):
        values = {
            "mode": "conventional", "breeding_goal": "测试育种目标",
            "goal_draft_id": "00000000-0000-0000-0000-000000000001",
            "draft_confirmed": True, "conditions": [self.condition],
        }
        values.update(overrides)
        return RecommendationRunPayload(**values)

    def test_confirmed_model_draft_builds_a_deterministic_request(self):
        domain = self.valid_payload().domain()
        self.assertEqual(domain.mode.value, "conventional")
        self.assertEqual(domain.conditions[0].field, "growth_duration_days")

    def test_unknown_condition_field_is_rejected_instead_of_silently_ignored(self):
        with self.assertRaises(ValidationError):
            RecommendationRunPayload(
                mode="conventional",
                breeding_goal="提高产量",
                goal_draft_id="00000000-0000-0000-0000-000000000001",
                draft_confirmed=True,
                conditions=[{
                    "code": "unsupported", "label": "不存在的指标", "field": "imaginary_trait",
                    "operator": "gte", "value": 1, "kind": "hard",
                }],
            )

    def test_hybrid_candidate_limit_is_bounded_before_database_work(self):
        with self.assertRaisesRegex(ValueError, "候选数量上限"):
            RecommendationRunPayload(
                mode="hybrid_three_line",
                breeding_goal="三系配组",
                goal_draft_id="00000000-0000-0000-0000-000000000001",
                draft_confirmed=True,
                candidate_limit=100,
                conditions=[self.condition],
            ).domain()

    def test_run_cannot_skip_model_draft_confirmation(self):
        with self.assertRaises(ValidationError):
            RecommendationRunPayload(
                mode="conventional", breeding_goal="提高产量",
                goal_draft_id="00000000-0000-0000-0000-000000000001",
                draft_confirmed=False, conditions=[self.condition],
            )

    def test_goal_parser_rejects_unsupported_model_field(self):
        provider = AIProviderSettings("cherryin", "https://example.test/v1", "model", "secret", True)
        invalid = {
            "mode": "conventional", "target_region": "云南", "summary": "草稿",
            "conditions": [{**self.condition, "field": "invented_trait"}],
            "unresolved_requirements": [], "assumptions": [],
        }
        with patch("app.parent_goal_parser.model_json_request", new=AsyncMock(return_value=invalid)):
            with self.assertRaisesRegex(RuntimeError, "未通过系统校验"):
                import asyncio
                asyncio.run(parse_parent_goal(provider=provider, breeding_goal="目标", mode="conventional", target_region="云南"))

    def test_goal_parser_normalizes_model_variation_before_presenting_draft(self):
        provider = AIProviderSettings("cherryin", "https://example.test/v1", "model", "secret", True)
        returned = {
            "mode": "conventional", "target_region": "云南省", "summary": "草稿",
            "conditions": [{
                **self.condition,
                "code": "model-generated-random-code",
                "unit": "日",
            }],
            "unresolved_requirements": [], "assumptions": [],
        }
        with patch("app.parent_goal_parser.model_json_request", new=AsyncMock(return_value=returned)):
            draft = asyncio.run(parse_parent_goal(
                provider=provider,
                breeding_goal="面向云南，全生育期必须在120～150天。",
                mode="conventional",
                target_region="云南",
            ))
        self.assertEqual(draft.target_region, "云南")
        self.assertEqual(draft.conditions[0].unit, "天")
        self.assertEqual(draft.conditions[0].code, "growth_duration_days-hard-every_parent-1")

    def test_slow_goal_parse_emits_progress_heartbeats_before_result(self):
        async def worker(progress):
            await progress("input_validation", "正在检查输入")
            await progress("model_analysis", "正在调用大模型")
            await asyncio.sleep(0.035)
            await progress("rule_validation", "正在校验条件")
            return {"goal_draft_id": "draft-1"}

        async def collect():
            return [event async for event in goal_progress_events(worker, heartbeat_seconds=0.01)]

        events = asyncio.run(collect())
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(events[-1]["payload"]["goal_draft_id"], "draft-1")
        self.assertGreaterEqual(sum(event["type"] == "heartbeat" for event in events), 2)
        self.assertIn("model_analysis", [event.get("stage") for event in events])

    def test_v2_snapshot_exports_do_not_depend_on_legacy_score_fields(self):
        result = ParentRecommendationEngine().recommend(
            request(), MemoryRiceDataRepository([candidate(1, "甲"), candidate(2, "乙")]),
        )
        self.assertTrue(recommendation_v2_csv(result).startswith(b"\xef\xbb\xbf"))
        self.assertTrue(recommendation_v2_pdf(result).startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()

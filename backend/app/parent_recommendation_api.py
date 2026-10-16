"""HTTP and persistence interface for parent recommendation v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .ai_gateway import (
    AIGatewayConcurrencyGate,
    AIGatewayConfigurationError,
    AIEgressBlockedError,
    prepare_egress,
    provider_settings,
)
from .parent_goal_parser import GOAL_PARSER_VERSION, parse_parent_goal
from .parent_recommendation_engine import (
    ConditionKind,
    ConditionScope,
    ConditionSpec,
    KinshipPolicy,
    ParentRecommendationEngine,
    RecommendationMode,
    RecommendationRequest,
)
from .ricedata_parent_repository import RiceDataParentRepository
from .ricedata_trait_facts import TRAIT_BY_CODE
from .parent_recommendation_report import recommendation_v2_csv, recommendation_v2_pdf
from .research_agent import ResearchAgentError


ALLOWED_FIELDS = frozenset({
    "rice_type", "variety_kind", "variety_type", "approval_region",
    "yield_relative_change_pct", "yield_kg_per_mu",
    *TRAIT_BY_CODE.keys(),
})


class ConditionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=80)
    operator: Literal["eq", "neq", "gte", "gt", "lte", "lt", "between", "in", "contains"]
    value: float | str | list[float] | list[str]
    kind: Literal["hard", "preference"]
    scope: Literal["every_parent", "at_least_one", "female", "male", "pair"] = "every_parent"
    importance: int = Field(default=2, ge=1, le=3)
    unit: str = Field(default="", max_length=30)
    require_trusted_evidence: bool = True
    evidence_policy: Literal["consistent", "worst"] = "consistent"

    @model_validator(mode="after")
    def validate_supported_field_and_value(self):
        if self.field not in ALLOWED_FIELDS:
            raise ValueError("当前数据库不支持这个条件字段")
        if self.operator == "between" and (not isinstance(self.value, list) or len(self.value) != 2):
            raise ValueError("区间条件必须提供两个边界值")
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("集合条件必须提供选项列表")
        return self

    def domain(self) -> ConditionSpec:
        value: Any = tuple(self.value) if isinstance(self.value, list) else self.value
        return ConditionSpec(
            code=self.code, label=self.label, field=self.field, operator=self.operator, value=value,
            kind=ConditionKind(self.kind), scope=ConditionScope(self.scope), importance=self.importance,
            unit=self.unit, require_trusted_evidence=self.require_trusted_evidence,
            evidence_policy=self.evidence_policy,
        )


class KinshipPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    depth: Literal[3, 5] = 3
    exclude_through_depth: int = Field(default=3, ge=2, le=5)
    require_complete_pedigree: bool = False


class GoalDraftRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["conventional", "hybrid_three_line", "hybrid_two_line"]
    breeding_goal: str = Field(min_length=2, max_length=2000)
    target_region: str = Field(default="", max_length=40)


class RecommendationRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["conventional", "hybrid_three_line", "hybrid_two_line"]
    breeding_goal: str = Field(min_length=2, max_length=2000)
    goal_draft_id: str = Field(min_length=36, max_length=36)
    draft_confirmed: Literal[True]
    target_region: str = Field(default="", max_length=40)
    conditions: list[ConditionPayload] = Field(min_length=1, max_length=30)
    pinned_variety_ids: list[int] = Field(default_factory=list, max_length=30)
    excluded_variety_ids: list[int] = Field(default_factory=list, max_length=100)
    candidate_limit: int = Field(default=80, ge=2, le=120)
    result_limit: int = Field(default=10, ge=1, le=50)
    kinship: KinshipPayload = Field(default_factory=KinshipPayload)

    def domain(self) -> RecommendationRequest:
        return RecommendationRequest(
            mode=RecommendationMode(self.mode), breeding_goal=self.breeding_goal,
            conditions=tuple(item.domain() for item in self.conditions), target_region=self.target_region,
            pinned_variety_ids=tuple(dict.fromkeys(self.pinned_variety_ids)),
            excluded_variety_ids=tuple(dict.fromkeys(self.excluded_variety_ids)),
            candidate_limit=self.candidate_limit, result_limit=self.result_limit,
            kinship=KinshipPolicy(**self.kinship.model_dump()),
        )


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    female_variety_id: int = Field(gt=0)
    male_variety_id: int = Field(gt=0)
    decision: Literal["accepted", "held", "rejected"]
    comment: str = Field(default="", max_length=2000)


class ReviewerAssignmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=120)
    enabled: bool = True


def ensure_parent_recommendation_schema(session: Session) -> None:
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS parent_goal_draft_v1 (
          id VARCHAR(36) PRIMARY KEY, institution_id VARCHAR(80) NOT NULL,
          owner_id VARCHAR(120) NOT NULL, mode VARCHAR(40) NOT NULL,
          breeding_goal TEXT NOT NULL, target_region VARCHAR(40) NOT NULL DEFAULT '',
          draft_json JSONB NOT NULL, provider_model VARCHAR(160) NOT NULL,
          status VARCHAR(20) NOT NULL DEFAULT 'parsed',
          confirmed_json JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          confirmed_at TIMESTAMPTZ
        )
    """))
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS parent_recommendation_run_v2 (
          id VARCHAR(36) PRIMARY KEY, institution_id VARCHAR(80) NOT NULL,
          project_id VARCHAR(36) NOT NULL, owner_id VARCHAR(120) NOT NULL,
          status VARCHAR(30) NOT NULL DEFAULT 'generated', request_json JSONB NOT NULL,
          result_json JSONB NOT NULL, engine_version VARCHAR(80) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS breeding_reviewer_assignment (
          institution_id VARCHAR(80) NOT NULL, user_id VARCHAR(120) NOT NULL,
          enabled BOOLEAN NOT NULL DEFAULT TRUE, granted_by VARCHAR(120) NOT NULL,
          granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (institution_id,user_id)
        )
    """))
    session.execute(text("ALTER TABLE parent_recommendation_run_v2 ADD COLUMN IF NOT EXISTS goal_draft_id VARCHAR(36)"))
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS parent_recommendation_review_v2 (
          id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL REFERENCES parent_recommendation_run_v2(id),
          female_variety_id BIGINT NOT NULL, male_variety_id BIGINT NOT NULL,
          decision VARCHAR(20) NOT NULL, comment TEXT NOT NULL DEFAULT '',
          reviewer_id VARCHAR(120) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(run_id,female_variety_id,male_variety_id)
        )
    """))
    session.execute(text("CREATE INDEX IF NOT EXISTS ix_parent_run_v2_owner ON parent_recommendation_run_v2(owner_id,created_at DESC)"))
    session.execute(text("CREATE INDEX IF NOT EXISTS ix_parent_goal_draft_v1_owner ON parent_goal_draft_v1(institution_id,owner_id,created_at DESC)"))
    session.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


ProgressReporter = Callable[[str, str], Awaitable[None]]
GoalWorker = Callable[[ProgressReporter], Awaitable[dict[str, Any]]]


async def goal_progress_events(
    worker: GoalWorker,
    *,
    heartbeat_seconds: float = 5.0,
) -> AsyncIterator[dict[str, Any]]:
    """Run goal parsing while keeping proxies and researchers informed.

    The heartbeat is deliberately generated by the API rather than the model
    provider.  A slow provider therefore cannot leave the HTTP connection idle
    long enough for an ingress proxy to manufacture a 504 response.
    """

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    current_stage = "queued"
    started = time.monotonic()

    async def report(stage: str, message: str) -> None:
        await queue.put({"type": "progress", "stage": stage, "message": message})

    task = asyncio.create_task(worker(report))
    try:
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                current_stage = str(event.get("stage") or current_stage)
                yield event
            except TimeoutError:
                if not task.done():
                    elapsed = max(1, int(time.monotonic() - started))
                    yield {
                        "type": "heartbeat",
                        "stage": current_stage,
                        "elapsed_seconds": elapsed,
                        "message": f"仍在处理，已等待 {elapsed} 秒，请不要重复提交。",
                    }
        yield {"type": "result", "payload": await task}
    finally:
        if not task.done():
            task.cancel()


def create_parent_recommendation_router(
    *,
    require_researcher,
    require_field_admin,
    get_session,
    institution_id: str,
    project_id: str,
    ai_gate: AIGatewayConcurrencyGate,
    ai_queue_wait_seconds: float,
) -> APIRouter:
    router = APIRouter(prefix="/api/research/parent-selection", tags=["parent-selection"])

    def input_fingerprint(payload: GoalDraftRequestPayload) -> str:
        canonical = _json({
            "parser_version": GOAL_PARSER_VERSION,
            "mode": payload.mode,
            "breeding_goal": payload.breeding_goal.strip(),
            "target_region": payload.target_region.strip(),
        })
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def cached_goal_draft(session: Session, *, fingerprint: str, owner_id: str) -> dict[str, Any] | None:
        row = session.execute(text("""
            SELECT id,draft_json,provider_model
            FROM parent_goal_draft_v1
            WHERE institution_id=:institution_id AND owner_id=:owner_id
              AND status='draft' AND draft_json->>'_input_fingerprint'=:fingerprint
            ORDER BY created_at DESC LIMIT 1
        """), {
            "institution_id": institution_id,
            "owner_id": owner_id,
            "fingerprint": fingerprint,
        }).mappings().first()
        if not row:
            return None
        draft_payload = dict(row["draft_json"] or {})
        draft_payload.pop("_input_fingerprint", None)
        draft_payload.pop("_parser_version", None)
        return {
            "goal_draft_id": str(row["id"]),
            "candidate_source": "国家水稻数据中心",
            "requires_user_confirmation": True,
            "cache_hit": True,
            **draft_payload,
        }

    async def build_goal_draft(
        payload: GoalDraftRequestPayload,
        *,
        user,
        session: Session,
        progress: ProgressReporter,
    ) -> dict[str, Any]:
        await progress("input_validation", "正在检查育种路线、目标地区和输入安全性")
        provider = provider_settings()
        safe = prepare_egress([payload.breeding_goal, payload.target_region], provider=provider)
        fingerprint = input_fingerprint(payload)

        cached = cached_goal_draft(session, fingerprint=fingerprint, owner_id=user.id)
        if cached:
            await progress("cache_hit", "已找到同一目标的已校验草稿，正在复用稳定结果")
            return cached

        await progress("field_mapping", "正在准备数据库支持的性状字段和可执行条件范围")
        task_id = f"parent-goal-{uuid.uuid4()}"
        acquired = False
        try:
            await ai_gate.acquire(task_id, institution_id, ai_queue_wait_seconds)
            acquired = True
            await progress("model_analysis", "大模型正在识别硬门槛、排序偏好、阈值和作用对象")
            draft = await parse_parent_goal(
                provider=provider,
                breeding_goal=safe.texts[0],
                mode=payload.mode,
                target_region=safe.texts[1],
            )
        finally:
            if acquired:
                ai_gate.release(task_id)

        await progress("rule_validation", "正在校验字段白名单、单位、阈值和育种路线一致性")
        draft_id = str(uuid.uuid4())
        draft_payload = draft.model_dump()
        if safe.redactions:
            draft_payload["assumptions"] = [
                *draft_payload.get("assumptions", []),
                f"输入中有 {safe.redactions} 处直接身份信息已在发送模型前脱敏。",
            ]
        stored_payload = {
            **draft_payload,
            "_input_fingerprint": fingerprint,
            "_parser_version": GOAL_PARSER_VERSION,
        }
        await progress("draft_persistence", "正在保存可供科研人员核对的条件草稿")
        session.execute(text("""
            INSERT INTO parent_goal_draft_v1 (
              id,institution_id,owner_id,mode,breeding_goal,target_region,draft_json,provider_model
            ) VALUES (
              :id,:institution_id,:owner_id,:mode,:goal,:region,CAST(:draft AS jsonb),:provider_model
            )
        """), {"id": draft_id, "institution_id": institution_id, "owner_id": user.id,
                 "mode": payload.mode, "goal": payload.breeding_goal,
                 "region": draft.target_region, "draft": _json(stored_payload),
                 "provider_model": provider.model})
        session.commit()
        await progress("completed", "目标理解完成，正在展示结构化草稿")
        return {
            "goal_draft_id": draft_id,
            "candidate_source": "国家水稻数据中心",
            "requires_user_confirmation": True,
            "cache_hit": False,
            **draft_payload,
        }

    @router.post("/goal-drafts")
    async def create_goal_draft(payload: GoalDraftRequestPayload, user=Depends(require_researcher),
                                session: Session = Depends(get_session)):
        async def ignore_progress(_stage: str, _message: str) -> None:
            return None

        try:
            return await build_goal_draft(
                payload, user=user, session=session, progress=ignore_progress,
            )
        except TimeoutError as exc:
            raise HTTPException(429, "当前大模型任务较多，目标理解排队超时，请稍后重试。") from exc
        except (AIGatewayConfigurationError, AIEgressBlockedError, ResearchAgentError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post("/goal-drafts/stream")
    async def create_goal_draft_stream(
        payload: GoalDraftRequestPayload,
        user=Depends(require_researcher),
        session: Session = Depends(get_session),
    ):
        async def event_stream():
            async def worker(progress: ProgressReporter) -> dict[str, Any]:
                return await build_goal_draft(
                    payload, user=user, session=session, progress=progress,
                )

            try:
                async for event in goal_progress_events(worker):
                    yield _json(event) + "\n"
            except TimeoutError:
                yield _json({
                    "type": "error",
                    "message": "当前大模型任务较多，目标理解排队超时，请稍后重试。",
                }) + "\n"
            except (AIGatewayConfigurationError, AIEgressBlockedError, ResearchAgentError) as exc:
                yield _json({"type": "error", "message": str(exc)}) + "\n"
            except SQLAlchemyError:
                session.rollback()
                yield _json({
                    "type": "error",
                    "message": "目标草稿保存失败，未执行亲本推荐，请稍后重试。",
                }) + "\n"

        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/runs")
    def run(payload: RecommendationRunPayload, user=Depends(require_researcher),
            session: Session = Depends(get_session)):
        draft = session.execute(text("""
            SELECT mode,breeding_goal,target_region,status FROM parent_goal_draft_v1
            WHERE id=:id AND institution_id=:institution_id AND owner_id=:owner_id
        """), {"id": payload.goal_draft_id, "institution_id": institution_id,
                 "owner_id": user.id}).mappings().first()
        if not draft:
            raise HTTPException(422, "请先让大模型理解本次育种目标，再确认条件并执行推荐。")
        if draft["mode"] != payload.mode or draft["breeding_goal"] != payload.breeding_goal:
            raise HTTPException(422, "育种目标或育种路线已在解析后改变，请重新执行大模型目标理解。")
        if str(draft["target_region"] or "") != payload.target_region:
            raise HTTPException(422, "目标地区已在解析后改变，请重新执行大模型目标理解。")
        try:
            request = payload.domain()
            result = ParentRecommendationEngine().recommend(request, RiceDataParentRepository(session))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except SQLAlchemyError as exc:
            session.rollback()
            raise HTTPException(503, "国家水稻数据中心本地数据库查询未完成，请稍后重试。") from exc
        run_id = str(uuid.uuid4())
        session.execute(text("""
            INSERT INTO parent_recommendation_run_v2 (
              id,institution_id,project_id,owner_id,goal_draft_id,request_json,result_json,engine_version
            ) VALUES (
              :id,:institution_id,:project_id,:owner_id,:goal_draft_id,
              CAST(:request AS jsonb),CAST(:result AS jsonb),:engine_version
            )
        """), {"id": run_id, "institution_id": institution_id, "project_id": project_id,
                 "owner_id": user.id, "goal_draft_id": payload.goal_draft_id,
                 "request": _json(payload.model_dump()), "result": _json(result),
                 "engine_version": result["engine_version"]})
        session.execute(text("""
            UPDATE parent_goal_draft_v1 SET status='confirmed',
              confirmed_json=CAST(:confirmed AS jsonb),confirmed_at=now()
            WHERE id=:id AND institution_id=:institution_id AND owner_id=:owner_id
        """), {"id": payload.goal_draft_id, "institution_id": institution_id,
                 "owner_id": user.id, "confirmed": _json({
                     "conditions": [item.model_dump() for item in payload.conditions],
                     "kinship": payload.kinship.model_dump(),
                 })})
        session.commit()
        return {"run_id": run_id, "status": "generated", **result}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str, user=Depends(require_researcher), session: Session = Depends(get_session)):
        row = session.execute(text("""
            SELECT id,status,request_json,result_json,engine_version,created_at
            FROM parent_recommendation_run_v2
            WHERE id=:id AND institution_id=:institution_id AND owner_id=:owner_id
        """), {"id": run_id, "institution_id": institution_id, "owner_id": user.id}).mappings().first()
        if not row:
            raise HTTPException(404, "未找到当前账号创建的亲本推荐记录。")
        reviews = [dict(item) for item in session.execute(text("""
            SELECT female_variety_id,male_variety_id,decision,comment,reviewer_id,created_at
            FROM parent_recommendation_review_v2 WHERE run_id=:id ORDER BY created_at
        """), {"id": run_id}).mappings()]
        return {"run_id": str(row["id"]), "status": row["status"], "request": row["request_json"],
                **dict(row["result_json"]), "reviews": reviews, "created_at": row["created_at"]}

    def owned_result(run_id: str, user, session: Session) -> dict[str, Any]:
        result = session.scalar(text("""
            SELECT result_json FROM parent_recommendation_run_v2
            WHERE id=:id AND institution_id=:institution_id AND owner_id=:owner_id
        """), {"id": run_id, "institution_id": institution_id, "owner_id": user.id})
        if not result:
            raise HTTPException(404, "未找到当前账号创建的亲本推荐记录。")
        return dict(result)

    @router.get("/runs/{run_id}/result.csv")
    def export_csv(run_id: str, user=Depends(require_researcher), session: Session = Depends(get_session)):
        return Response(recommendation_v2_csv(owned_result(run_id, user, session)),
                        media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="parent-selection-{run_id}.csv"'})

    @router.get("/runs/{run_id}/report.pdf")
    def export_pdf(run_id: str, user=Depends(require_researcher), session: Session = Depends(get_session)):
        return Response(recommendation_v2_pdf(owned_result(run_id, user, session)),
                        media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="parent-selection-{run_id}.pdf"'})

    @router.post("/runs/{run_id}/review")
    def review(run_id: str, payload: ReviewPayload, user=Depends(require_researcher),
               session: Session = Depends(get_session)):
        assigned = session.scalar(text("""
            SELECT enabled FROM breeding_reviewer_assignment
            WHERE institution_id=:institution_id AND user_id=:user_id
        """), {"institution_id": institution_id, "user_id": user.id})
        if assigned is not True:
            raise HTTPException(403, "当前账号没有育种审核权限。")
        run_row = session.execute(text("""
            SELECT result_json FROM parent_recommendation_run_v2
            WHERE id=:id AND institution_id=:institution_id
        """), {"id": run_id, "institution_id": institution_id}).mappings().first()
        if not run_row:
            raise HTTPException(404, "未找到亲本推荐记录。")
        result = dict(run_row["result_json"] or {})
        visible = [*(result.get("recommendations") or []), *(result.get("backup_recommendations") or []),
                   *(result.get("pending_verification") or []), *(result.get("excluded_combinations") or [])]
        exists = any(item.get("female_parent", {}).get("variety_id") == payload.female_variety_id and
                     item.get("male_parent", {}).get("variety_id") == payload.male_variety_id for item in visible)
        if not exists:
            raise HTTPException(422, "所选组合不在该推荐快照中。")
        session.execute(text("""
            INSERT INTO parent_recommendation_review_v2 (
              id,run_id,female_variety_id,male_variety_id,decision,comment,reviewer_id
            ) VALUES (
              :id,:run_id,:female,:male,:decision,:comment,:reviewer
            ) ON CONFLICT (run_id,female_variety_id,male_variety_id) DO UPDATE SET
              decision=EXCLUDED.decision,comment=EXCLUDED.comment,reviewer_id=EXCLUDED.reviewer_id,created_at=now()
        """), {"id": str(uuid.uuid4()), "run_id": run_id, "female": payload.female_variety_id,
                 "male": payload.male_variety_id, "decision": payload.decision,
                 "comment": payload.comment, "reviewer": user.id})
        session.execute(text("UPDATE parent_recommendation_run_v2 SET status='reviewed' WHERE id=:id"), {"id": run_id})
        session.commit()
        return {"run_id": run_id, **payload.model_dump(), "reviewer_id": user.id}

    @router.put("/reviewers")
    def assign_reviewer(payload: ReviewerAssignmentPayload, user=Depends(require_field_admin),
                        session: Session = Depends(get_session)):
        session.execute(text("""
            INSERT INTO breeding_reviewer_assignment (institution_id,user_id,enabled,granted_by)
            VALUES (:institution_id,:user_id,:enabled,:granted_by)
            ON CONFLICT (institution_id,user_id) DO UPDATE SET
              enabled=EXCLUDED.enabled,granted_by=EXCLUDED.granted_by,granted_at=now()
        """), {"institution_id": institution_id, "user_id": payload.user_id,
                 "enabled": payload.enabled, "granted_by": user.id})
        session.commit()
        return {"user_id": payload.user_id, "enabled": payload.enabled}

    return router


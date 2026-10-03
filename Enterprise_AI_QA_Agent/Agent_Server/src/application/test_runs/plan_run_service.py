"""Freeze a plan's suite and first run, without a second execution engine."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.application.test_runs.plan_mapping import build_plan_run, validate_frozen_cases
from src.core.request_context import get_request_context, new_id
from src.schemas.case_planning import CasePlanRunRequest, RegressionPlanSource
from src.schemas.run_dispatch import RunDispatchTask

logger = logging.getLogger(__name__)


class CasePlanRunService:
    def __init__(self, *, run_store, suite_store, plan_store, case_service, project_service,
                 session_store=None, mode_registry=None, outbox_table: str | None = None, dispatch_stream: str | None = None):
        self._runs, self._suites, self._plans = run_store, suite_store, plan_store
        self._cases, self._projects, self._sessions = case_service, project_service, session_store
        self._outbox_table, self._stream = outbox_table, dispatch_stream
        self._modes = mode_registry
        if bool(outbox_table) != bool(dispatch_stream):
            raise ValueError("计划运行的 Outbox 表与目标流必须同时配置")

    async def create(self, project_id: str, plan_id: str, request: CasePlanRunRequest,
                     *, created_by: str | None = None, regression: RegressionPlanSource | None = None,
                     require_dispatch: bool = False):
        plan = await self._plans.get(project_id, plan_id)
        if plan is None:
            raise KeyError("当前项目的固定计划不存在")
        bundle, run, items = build_plan_run(plan, request, now=datetime.now(timezone.utc), created_by=created_by,
            regression=regression)
        existing = await self._runs.get_run(run.id)
        if existing:
            if existing.run.creation_hash != run.creation_hash:
                raise ValueError("相同创建请求已经关联其他计划或执行参数")
            logger.info("case_plan_run_replayed", extra={"project_id": project_id, "plan_id": plan_id, "run_id": run.id})
            return existing
        if require_dispatch and (not self._outbox_table or not self._stream or self._modes is None):
            raise RuntimeError("计划运行尚未配置持久派发和模式注册服务")
        if self._modes is not None:
            try:
                mode = self._modes.get(request.mode_key)
            except KeyError:
                raise ValueError("测试模式未注册") from None
            if mode.key != request.mode_key or not mode.is_test_mode:
                raise ValueError("请选择已注册的测试模式")
        await self._projects.require_active(project_id)
        formal = [node for node in plan.decision.items if node.kind != "prepare"]
        cases = await self._cases.get_cases([n.case_id for n in formal])
        versions = await self._cases.get_versions([n.case_version_id for n in formal])
        validate_frozen_cases(plan, cases, versions)
        if regression and any(cases[case_id].active_version_id != version_id
                              for case_id, version_id in regression.version_overrides.items()):
            raise ValueError("回归指定启用版本在提交前发生变化，请重新规划")
        if request.session_id:
            if self._sessions is None:
                raise RuntimeError("计划运行尚未配置会话存储")
            session = await self._sessions.get_session(request.session_id)
            if session is None or session.project_id != project_id:
                raise ValueError("会话不存在或不属于当前项目")
        context = get_request_context()
        task = RunDispatchTask(task_id=f"task_test_run_{run.id}", project_id=project_id, run_id=run.id,
            session_id=run.session_id, request_id=context.request_id if context else new_id("req"),
            trace_id=context.trace_id if context else new_id("trace"))
        try:
            stored = await self._runs.create_planned_run(bundle, run, items, suite_store=self._suites,
                outbox_table=self._outbox_table, event_key=f"test_run_dispatch:{run.id}",
                stream=self._stream, payload=task.model_dump(mode="json"))
        except Exception as exc:
            logger.error("case_plan_run_persist_failed", extra={"project_id": project_id,
                "plan_id": plan_id, "run_id": run.id, "error_type": type(exc).__name__})
            raise
        logger.info("case_plan_run_created", extra={"project_id": project_id, "plan_id": plan_id,
            "run_id": stored.run.id, "item_count": len(stored.items), "dispatch_recorded": bool(self._stream)})
        return stored

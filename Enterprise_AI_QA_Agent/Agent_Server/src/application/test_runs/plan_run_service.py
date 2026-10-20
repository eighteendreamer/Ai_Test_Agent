"""Freeze a plan's suite and first run, without a second execution engine."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.application.test_runs.plan_mapping import (build_plan_run, json_digest, plan_run_identity,
    validate_frozen_cases, regression_source_for_plan)
from src.core.request_context import get_request_context, new_id
from src.schemas.case_planning import CasePlanRunRequest, RegressionPlanSource, case_plan_json
from src.schemas.run_management import RunPlanRevisionRequest, TestRunPlanRevisionRecord
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
        regression = regression_source_for_plan(plan, request, regression)
        existing = await self._runs.get_run(plan_run_identity(plan, request, "run"))
        mapping = existing.run.plan_snapshot.dependency_mapping_version if existing and existing.run.plan_snapshot else "auto"
        bundle, run, items = build_plan_run(plan, request, now=datetime.now(timezone.utc), created_by=created_by,
            regression=regression, dependency_mapping_version=mapping)
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

    async def revise(self, project_id: str, run_id: str,
                     request: RunPlanRevisionRequest) -> TestRunPlanRevisionRecord:
        detail = await self._runs.get_run(run_id)
        if detail is None:
            raise KeyError("测试运行不存在")
        run = detail.run
        if run.project_id != project_id:
            raise KeyError("测试运行不属于当前项目")
        if (run.origin != "native" or run.execution_strategy != "agent"
            or run.plan_snapshot is None or run.creation_key is None):
            raise ValueError("只有基于固定用例计划创建的原生 Agent 运行可以修订")
        await self._projects.require_active(project_id)
        plan = await self._plans.get(project_id, request.plan_id)
        if plan is None:
            raise KeyError("当前项目没有该候选用例计划")
        formal = [node for node in (plan.decision.items if plan.decision else []) if node.kind != "prepare"]
        cases = await self._cases.get_cases([node.case_id for node in formal])
        versions = await self._cases.get_versions([node.case_version_id for node in formal])
        validate_frozen_cases(plan, cases, versions)
        run_request = CasePlanRunRequest(creation_key=run.creation_key, mode_key=run.mode_key,
            session_id=run.session_id)
        now = datetime.now(timezone.utc)
        _candidate_suite, candidate_run, candidate_items = build_plan_run(
            plan, run_request, now=now, created_by=run.created_by,
            regression=run.plan_snapshot.regression,
            dependency_mapping_version=run.plan_snapshot.dependency_mapping_version,
        )
        # Suite identity is only needed by the shared plan-to-Run mapper. The
        # original frozen Suite is deliberately not rewritten by a Run revision.
        request_hash = json_digest({
            "run_id": run_id,
            "expected_revision": request.expected_revision,
            "candidate_plan_id": plan.id,
            "candidate_plan_sha256": json_digest(case_plan_json(plan)),
            "reason": request.reason,
        })
        record = await self._runs.revise_plan(run_id=run_id, request=request,
            request_sha256=request_hash, candidate_run=candidate_run,
            candidate_items=candidate_items, now=now)
        logger.info("case_plan_run_revision_submitted", extra={"project_id": project_id,
            "run_id": run_id, "plan_id": plan.id, "revision": record.revision,
            "added_item_count": len(record.added_item_ids),
            "cancelled_item_count": len(record.cancelled_item_ids)})
        return record

    async def list_revisions(self, project_id: str, run_id: str) -> list[TestRunPlanRevisionRecord]:
        detail = await self._runs.get_run(run_id)
        if detail is None or detail.run.project_id != project_id:
            raise KeyError("测试运行不存在或不属于当前项目")
        return await self._runs.list_plan_revisions(run_id)

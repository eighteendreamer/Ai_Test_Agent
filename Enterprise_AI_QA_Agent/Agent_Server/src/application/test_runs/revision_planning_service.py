"""Replan through the durable planner, then revise only the unclaimed Run."""
from __future__ import annotations

import logging

from src.schemas.case_planning import CasePlanningRequest
from src.schemas.run_management import RunPlanRevisionRequest
from src.schemas.run_revision_planning import (
    RunRevisionPlanningInput, RunRevisionPlanningOutcome, RunRevisionPlanningRequest,
)

logger = logging.getLogger(__name__)


class RunRevisionPlanningService:
    def __init__(self, *, tasks, planner, runs, projects, revision_service, dispatch_stream):
        self._tasks, self._planner, self._runs = tasks, planner, runs
        self._projects, self._revisions, self._stream = projects, revision_service, dispatch_stream

    async def submit(self, project_id: str, run_id: str, request: RunRevisionPlanningRequest, *, execution_trigger=None):
        await self._projects.get(project_id)
        submission = request.model_dump(mode="json", exclude={"creation_key"})
        if execution_trigger is not None:
            submission["execution_trigger"] = execution_trigger
        digest = self._tasks.run_revision_request_hash(run_id, submission)
        existing = await self._tasks.get_by_request(project_id, request.creation_key, request_hash=digest)
        if existing:
            logger.info("run_revision_planning_replayed", extra={"project_id": project_id,
                "run_id": run_id, "task_id": existing.id})
            return existing
        detail = await self._runs.get_run(run_id)
        if detail is None or detail.run.project_id != project_id:
            raise KeyError("测试运行不存在或不属于当前项目")
        run, snapshot = detail.run, detail.run.plan_snapshot
        if (run.origin != "native" or run.execution_strategy != "agent" or snapshot is None
            or not run.creation_key or run.status not in {"queued", "running"}):
            raise ValueError("只有进行中的固定计划原生 Agent 运行可以重规划")
        if run.plan_revision != request.expected_revision:
            raise ValueError("运行计划修订版本已变化，请重新读取运行")
        if not self._stream:
            raise RuntimeError("持久规划的派发流尚未启用")
        original = snapshot.plan
        targets = {node.case_id: node.case_version_id for node in original.decision.items if node.kind == "target"}
        versions = {node.case_id: node.case_version_id for node in original.decision.items if node.kind != "prepare"}
        context = {"kind": "run_revision", "run_id": run_id, "source_plan_id": original.id,
            "expected_revision": request.expected_revision, "reason": request.reason,
            "previous_plan": original.decision.model_dump(mode="json"),
            "items": [{"id": item.id, "plan_item_key": item.plan_item_key, "status": item.status,
                "attempt_no": item.attempt_no, "result_id": item.result_id,
                "fixed_execution": item.status != "queued" or item.attempt_no > 0} for item in detail.items],
            "results": [{"id": result.id, "run_item_id": result.run_item_id,
                "status": result.status, "summary": result.summary, "error_message": result.error_message,
                "verification_results": result.actual.get("verification_results", [])} for result in detail.results],
            "instructions": "保留原目标及其固定版本；已领取项的步骤、前置和数据来源不可更改。"
                "根据新缺口查询项目依据，仅修订未领取部分，沿用可保留项的 key；不能用准备成功替代目标验证。"}
        if execution_trigger is not None:
            context["execution_trigger"] = execution_trigger
        source = RunRevisionPlanningInput(run_id=run_id, source_plan_id=original.id,
            expected_revision=request.expected_revision, reason=request.reason,
            required_case_versions=targets, read_case_versions=versions, context=context,
            execution_trigger=execution_trigger)
        # Fixed scope is not a bounded semantic-recall candidate list (the same
        # distinction is used by regression planning for more than 50 targets).
        planning = CasePlanningRequest(objective=original.request.objective, model_key=original.model_key,
            runtime_context=original.request.runtime_context, environment=original.request.environment,
            required_sources=original.request.required_sources)
        seed = await self._planner.prepare_task(project_id, planning, regression=original.regression)
        seed.run_revision = source
        task = await self._tasks.submit(project_id=project_id, creation_key=request.creation_key,
            seed=seed, request_hash=digest, stream=self._stream)
        logger.info("run_revision_planning_submitted", extra={"project_id": project_id,
            "run_id": run_id, "task_id": task.id, "expected_revision": request.expected_revision})
        return task

    async def finalize(self, project_id: str, task_id: str):
        async def apply(seed, checkpoint):
            source = seed.run_revision
            key = f"planning-task:{task_id}"

            async def committed():
                history = await self._revisions.list_revisions(project_id, source.run_id)
                prior = next((entry for entry in history if entry.idempotency_key == key), None)
                if prior and (prior.candidate_plan_id != checkpoint.plan.id or prior.reason != source.reason
                    or prior.revision != source.expected_revision + 1):
                    raise RuntimeError("规划任务修订历史与固定输入不一致")
                return prior

            # Resolve a committed success before revalidating a changing project
            # or case catalog; a lost acknowledgement must not become rejection.
            prior = await committed()
            if prior is None:
                try:
                    targets = {node.case_id: node.case_version_id for node in checkpoint.plan.decision.items
                        if node.kind == "target"} if checkpoint.plan.decision else {}
                    if (checkpoint.plan.status not in {"planned", "blocked"}
                        or targets != source.required_case_versions):
                        raise ValueError("候选规划未完成或没有完整保留原目标及固定版本")
                    prior = await self._revisions.revise(project_id, source.run_id,
                        RunPlanRevisionRequest(expected_revision=source.expected_revision,
                            idempotency_key=key, plan_id=checkpoint.plan.id, reason=source.reason))
                except (KeyError, ValueError) as exc:
                    prior = await committed()
                    if prior is None:
                        logger.warning("run_revision_planning_rejected", extra={"project_id": project_id,
                            "run_id": source.run_id, "task_id": task_id, "error_type": type(exc).__name__})
                        return RunRevisionPlanningOutcome(run_id=source.run_id, status="rejected", message=str(exc))
            logger.info("run_revision_planning_applied", extra={"project_id": project_id,
                "run_id": source.run_id, "task_id": task_id, "revision": prior.revision})
            return RunRevisionPlanningOutcome(run_id=source.run_id, status="applied",
                revision=prior.revision, message="候选计划已应用到未领取部分")

        return await self._tasks.finalize_run_revision(project_id, task_id, apply)

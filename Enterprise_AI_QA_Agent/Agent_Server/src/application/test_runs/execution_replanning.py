"""Let an admitted execution Agent request the existing durable Run planner."""
from __future__ import annotations

import logging
import json

from pydantic import Field, model_validator
from typing import Literal

from src.application.runtime.case_action_binding import (
    ADMISSION_PROTOCOL, OPERATION_PROTOCOL, bind_case_action, fixed_action_context,
    validate_action_admission_receipt,
)
from src.application.runtime.case_operation import operation_identity
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.application.test_runs.literal_evidence import literal_value
from src.schemas.case_planning import PlanningModel
from src.schemas.run_revision_planning import RunRevisionPlanningRequest

logger = logging.getLogger(__name__)
TOOL_KEY = "run-plan-replanner"
MAX_EXECUTION_TRIGGER_EVIDENCE_BYTES = 32_768


class ExecutionReplanningEvidence(PlanningModel):
    job_id: str = Field(min_length=1, max_length=256)
    output_path_tokens: list[str] = Field(min_length=1, max_length=32,
        description='Literal string keys/indexes into persisted output. Array index 0 is "0", not a JSON number.')


def execution_replanning_enabled(bundle: dict) -> bool:
    raw = bundle.get("case_execution")
    return bool(bundle.get("case_execution_managed") and isinstance(raw, dict)
        and raw.get("action_admission_protocol") == ADMISSION_PROTOCOL
        and raw.get("operation_protocol") == OPERATION_PROTOCOL)


class ExecutionReplanningRequest(PlanningModel):
    action: Literal["inspect", "submit", "status"]
    expected_revision: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    evidence: list[ExecutionReplanningEvidence] = Field(default_factory=list, max_length=12)
    submission_operation_key: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "submit":
            if self.expected_revision is None or not self.reason or not self.reason.strip() or not self.evidence:
                raise ValueError("提交重规划需要当前修订号、具体原因和实际工具证据")
            identities = [(entry.job_id, tuple(entry.output_path_tokens)) for entry in self.evidence]
            if len(set(identities)) != len(identities):
                raise ValueError("重规划工具证据引用不能重复")
            if self.submission_operation_key is not None:
                raise ValueError("提交使用当前_case_action.operation_key")
            self.reason = self.reason.strip()
        else:
            if self.expected_revision is not None or self.reason is not None or self.evidence:
                raise ValueError("查询重规划不接受提交字段")
            if (self.action == "status") != (self.submission_operation_key is not None):
                raise ValueError("只有status需要原提交的submission_operation_key")
        return self


class ExecutionReplanningService:
    def __init__(self, *, planning, tasks, runs, jobs):
        self._planning, self._tasks, self._runs, self._jobs = planning, tasks, runs, jobs
        self._safety = OutputSafetyPolicy()

    @staticmethod
    def _submission_key(job, binding, operation_key=None):
        # Reuse the existing original-attempt operation identity across provider
        # call IDs and lease recovery. Status itself uses a separate read key.
        if operation_key is not None:
            job = job.model_copy(update={"input_payload": {**job.input_payload,
                "_case_action": {"item_key": binding.item_key, "operation_key": operation_key}}})
        return "execution-replan:" + operation_identity(job, binding)["operation_id"]

    @staticmethod
    def _bounded_evidence_bytes(evidence):
        size = len(json.dumps(evidence, ensure_ascii=False, sort_keys=True,
            allow_nan=False, separators=(",", ":")).encode("utf-8"))
        if size > MAX_EXECUTION_TRIGGER_EVIDENCE_BYTES:
            raise ValueError("重规划选中证据超过总大小限制，请缩小output_path_tokens范围")
        return size

    async def execute(self, request: ExecutionReplanningRequest, context):
        if not execution_replanning_enabled(context.context_bundle):
            raise ValueError("执行重规划需要服务器管理的固定执行及逻辑操作策略")
        frozen = fixed_action_context(context.context_bundle)
        job = await self._jobs.get_job(context.tool_job_id)
        if job is None or job.tool_key != TOOL_KEY or job.status.value != "running":
            raise ValueError("执行重规划缺少当前已领取工具任务")
        binding = bind_case_action(bundle=context.context_bundle, arguments=job.input_payload,
            session_id=context.session_id, turn_id=context.turn_id)
        if binding != context.case_action_binding or job.metadata.get("case_action_binding") != binding.model_dump(mode="json"):
            raise ValueError("执行重规划工具任务与原固定身份不一致")
        validate_action_admission_receipt(job, binding, required=True)
        await self._jobs.validate_case_operation(job)
        if request.action == "status":
            task = await self._tasks.get_by_request(binding.project_id,
                self._submission_key(job, binding, request.submission_operation_key))
            if task is None:
                return {"status": "completed", "ok": True, "summary": "该逻辑操作尚无已保存的重规划提交。",
                    "submission_found": False, "run_id": binding.run_id}
            if task.run_revision is None or task.run_revision.run_id != binding.run_id:
                raise ValueError("原重规划提交与当前运行不同")
            return self._task_output(task)
        detail = await self._runs.get_run(binding.run_id)
        if detail is None or detail.run.project_id != binding.project_id:
            raise ValueError("执行重规划运行不属于当前项目")
        if request.action == "inspect":
            return {"status": "completed", "ok": True, "summary": "已读取当前计划；只有未领取部分可修订。",
                "run_id": binding.run_id, "revision": detail.run.plan_revision,
                "execution_plan_id": frozen.plan_id, "current_plan_id": detail.run.plan_snapshot.plan.id,
                "items": [{"id": entry.id, "key": entry.plan_item_key, "status": entry.status,
                    "editable": entry.status == "queued" and entry.attempt_no == 0
                        and not entry.lease_token and not entry.result_id} for entry in detail.items]}
        evidence = []
        for reference in request.evidence:
            source = await self._jobs.get_job(reference.job_id)
            if source is None or source.tool_key == TOOL_KEY or source.status.value not in {"completed", "partial", "failed", "denied"}:
                raise ValueError("重规划依据需要已结算的原工具证据")
            source_binding = source.metadata.get("case_action_binding") or {}
            if ((source.session_id, source.turn_id) != (job.session_id, job.turn_id)
                or any(source_binding.get(key) != getattr(binding, key) for key in
                    ("project_id", "run_id", "run_item_id", "plan_id", "attempt_id", "context_sha256"))):
                raise ValueError("重规划证据不属于当前固定执行")
            await self._jobs.validate_case_operation(source)
            try:
                selected = literal_value(source.output_payload, reference.output_path_tokens)
            except ValueError:
                raise ValueError("重规划证据的output_path_tokens不存在") from None
            safe, _, _ = self._safety.sanitize_tool_output({"selected_value": selected})
            evidence.append({"job_id": source.id, "tool_key": source.tool_key,
                "status": source.status.value, "output_path_tokens": reference.output_path_tokens,
                "output": safe["selected_value"], "security": safe.get("_security")})
        try:
            evidence_bytes = self._bounded_evidence_bytes(evidence)
        except ValueError:
            logger.warning("execution_replanning_evidence_limit_exceeded", extra={
                "run_id": binding.run_id, "tool_job_id": job.id,
                "max_evidence_bytes": MAX_EXECUTION_TRIGGER_EVIDENCE_BYTES})
            raise
        trigger = {"origin_job_id": job.id, "operation_id": operation_identity(job, binding)["operation_id"],
            "binding": binding.model_dump(mode="json"), "evidence": evidence}
        task = await self._planning.submit(binding.project_id, binding.run_id,
            RunRevisionPlanningRequest(creation_key=self._submission_key(job, binding),
                expected_revision=request.expected_revision, reason=request.reason), execution_trigger=trigger)
        logger.info("execution_replanning_submitted", extra={"run_id": binding.run_id,
            "run_item_id": binding.run_item_id, "tool_job_id": job.id, "task_id": task.id,
            "evidence_job_ids": [entry.job_id for entry in request.evidence],
            "evidence_bytes": evidence_bytes})
        return self._task_output(task)

    @staticmethod
    def _task_output(task):
        revision = task.run_revision
        if hasattr(revision, "model_dump"):
            revision = revision.model_dump(mode="json")
        else:
            revision = dict(revision)
        return {"status": "completed", "ok": True, "summary": "重规划任务已保存；请按run_revision判断是否已应用。",
            "submission_found": True, "task_id": task.id, "task_status": task.status,
            "phase": task.phase, "message": task.message,
            "run_revision": revision}

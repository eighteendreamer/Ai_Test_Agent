"""Lease-fenced Agent execution, with durable intent before entering the graph."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from src.application.runtime.tool_job_service import ToolJobService
from src.application.sessions.session_service import SessionService
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.agent_case_receipt import AgentCaseReceiptService, CaseReceiptOutcomeUnknown
from src.application.test_runs.run_service import TestRunService
from src.runtime.store import SessionStore
from src.schemas.case_execution import AgentCaseExecutionCheckpoint
from src.schemas.run_management import RunItemCheckpointRequest, RunEvidenceRef
from src.schemas.session import ApprovalDecisionRequest, CreateSessionRequest, SessionSnapshot
from src.schemas.tool_runtime import ToolExecutionRecord
from src.schemas.model_config import ModelInvocationResult
from src.schemas.session import ToolApprovalStatus
from src.core.request_context import bind_request_context


logger = logging.getLogger(__name__)
CHECKPOINT_KEY = "agent_case_execution"


class AgentCaseRecoveryRequired(RuntimeError):
    """Execution may have effects; do not launch another turn without reconciliation."""


@dataclass(frozen=True)
class AgentCaseApprovalWait:
    session_id: str
    approval_id: str
    tool_job_id: str
    summary: str


class AgentCaseExecutionAdapter:
    # No upstream counterpart (checked: CaseExecutionAdapter, SessionService,
    # RunStore checkpoints and local Agent trajectory course): frozen arbitrary
    # JSON needs graph execution, rather than legacy runner-argument compilation.
    def __init__(self, *, run_service: TestRunService, session_service: SessionService,
        session_store: SessionStore, tool_job_service: ToolJobService,
        model_runtime_service=None, artifact_storage_service=None) -> None:
        self._runs = run_service
        self._sessions = session_service
        self._store = session_store
        self._jobs = tool_job_service
        self._assessment = AgentCaseAssessmentService(tool_job_service)
        self._receipts = AgentCaseReceiptService(models=model_runtime_service,
            objects=artifact_storage_service, assessment=self._assessment)

    async def _load(self, *, run, item, version, attempt_id):
        detail = await self._runs.get_execution_detail(run.id, item.id, attempt_id, supplied_run=run)
        current = next((entry for entry in detail.items if entry.id == item.id), None)
        attempt = next((entry for entry in detail.attempts if entry.id == attempt_id), None)
        if (any(getattr(detail.run, key) != getattr(run, key)
                for key in ("project_id", "mode_key", "execution_strategy"))
            or current is None or current.run_id != run.id or current.lease_token != item.lease_token
            or current.case_id != item.case_id or current.case_version_id != item.case_version_id
            or current.status != "running" or attempt is None or attempt.id != attempt_id
            or attempt.lease_token != item.lease_token or attempt.status != "running"):
            raise ValueError("Agent 执行与当前运行条目或 attempt 租约不一致")
        context = build_agent_case_context(detail, current, version)
        canonical = json.dumps(context.execution_json(), sort_keys=True,
            ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        context_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if attempt.checkpoint_version:
            if attempt.checkpoint_key != CHECKPOINT_KEY:
                raise AgentCaseRecoveryRequired("Agent attempt 已有其他执行策略的检查点，需要核对原执行")
            checkpoint = AgentCaseExecutionCheckpoint.model_validate(attempt.checkpoint_payload)
            if (checkpoint.context_hash != context_hash or checkpoint.project_id != run.project_id
                or checkpoint.run_id != run.id or checkpoint.run_item_id != item.id
                or checkpoint.plan_id != context.plan_id):
                raise ValueError("Agent 检查点与固定项目、运行或完整上下文不一致")
            lineage = {entry.id: entry for entry in detail.attempts if entry.run_item_id == item.id}
            ancestor, visited = attempt, set()
            while ancestor.id != checkpoint.execution_attempt_id:
                if ancestor.id in visited or ancestor.recovered_from_attempt_id not in lineage:
                    raise ValueError("Agent 检查点的执行 attempt 不属于当前恢复链")
                visited.add(ancestor.id)
                ancestor = lineage[ancestor.recovered_from_attempt_id]
            expected_turn = str(uuid5(NAMESPACE_URL,
                f"case-run:{run.id}:{item.id}:{checkpoint.execution_attempt_id}"))
            if checkpoint.turn_id != expected_turn:
                raise ValueError("Agent 检查点 turn 身份不一致")
        else:
            preferences, metadata = {}, {"source": "test_run_agent_execution", "run_id": run.id,
                "run_item_id": item.id, "parent_session_id": run.session_id}
            if run.session_id:
                parent = await self._store.get_session(run.session_id)
                if parent is None or parent.project_id != run.project_id:
                    raise ValueError("Agent 运行的来源会话与正式项目不一致")
                preferences = {"session_mode": parent.session_mode, "runtime_mode": parent.runtime_mode,
                    "preferred_model": parent.preferred_model}
                metadata.update({key: parent.metadata[key]
                    for key in ("environment", "resource_scope", "security_authorization") if key in parent.metadata})
            session = await self._sessions.create_internal_session(CreateSessionRequest(
                title=f"用例执行：{context.target_key}", project_id=run.project_id, mode_key=run.mode_key,
                metadata=metadata, **preferences))
            checkpoint = AgentCaseExecutionCheckpoint(project_id=run.project_id, run_id=run.id,
                run_item_id=item.id, plan_id=context.plan_id, context_hash=context_hash,
                execution_attempt_id=attempt.id, session_id=session.id,
                turn_id=str(uuid5(NAMESPACE_URL, f"case-run:{run.id}:{item.id}:{attempt.id}")), phase="admitted")
            attempt = await self._save(item, attempt, checkpoint)
        session = await self._store.get_session(checkpoint.session_id)
        if (session is None or session.project_id != run.project_id
            or session.metadata.get("run_id") != run.id or session.metadata.get("run_item_id") != item.id):
            raise AgentCaseRecoveryRequired("Agent 检查点的所属会话丢失或归属不一致，需要核对原执行")
        return context, checkpoint, attempt

    async def _save(self, item, attempt, checkpoint):
        saved = await self._runs.save_checkpoint(item.id, RunItemCheckpointRequest(
            lease_token=item.lease_token, checkpoint_key=CHECKPOINT_KEY,
            checkpoint_payload=checkpoint.model_dump(mode="json"), checkpoint_version=attempt.checkpoint_version + 1))
        logger.info("agent_case_execution_checkpoint_saved", extra={"run_id": item.run_id,
            "run_item_id": item.id, "attempt_id": saved.id,
            "execution_attempt_id": checkpoint.execution_attempt_id, "phase": checkpoint.phase,
            "session_id": checkpoint.session_id, "checkpoint_version": saved.checkpoint_version})
        return saved

    async def _snapshot(self, checkpoint):
        for snapshot in reversed(await self._store.list_snapshots(checkpoint.session_id, include_graph_state=True)):
            if snapshot.graph_state.get("turn_id") == checkpoint.turn_id:
                return snapshot
        return None

    def _execution_scope(self, context, checkpoint, item):
        # Browser/Docker allocation services use the existing request context.
        # HTTP and recovered workers must not donate a parent/stale identity.
        return bind_request_context(project_id=context.project_id, run_id=context.run_id,
            run_item_id=context.run_item_id, session_id=checkpoint.session_id,
            turn_id=checkpoint.turn_id, attempt_id=checkpoint.execution_attempt_id,
            worker_id=str(item.lease_owner or "worker"))

    async def execute(self, *, case, version, run, item, attempt_id: str, **kwargs):
        if kwargs.get("approval_id"):
            if not isinstance(kwargs.get("approval_payload"), ApprovalDecisionRequest):
                raise ValueError("Agent 工具审批须通过运行条目审批入口决议")
            return await self.resume_approval(case=case, version=version, run=run, item=item,
                attempt_id=attempt_id, approval_id=kwargs["approval_id"], payload=kwargs["approval_payload"])
        if case.id != item.case_id or case.project_id != run.project_id:
            raise ValueError("Agent 用例与运行项目不一致")
        context, checkpoint, attempt = await self._load(run=run, item=item, version=version, attempt_id=attempt_id)
        snapshot = await self._snapshot(checkpoint)
        if checkpoint.receipt_correction is not None and snapshot is not None:
            return await self._finish(context, checkpoint, attempt, item, snapshot, mode_key=run.mode_key)
        if snapshot is None and checkpoint.phase != "admitted":
            logger.warning("agent_case_execution_requires_reconciliation", extra={"run_id": run.id,
                "run_item_id": item.id, "attempt_id": attempt.id,
                "execution_attempt_id": checkpoint.execution_attempt_id, "session_id": checkpoint.session_id})
            raise AgentCaseRecoveryRequired("已记录 Agent 执行意图但没有可重放快照；需核对工具证据，不能重复操作")
        # The durable running intent precedes every first admission. A lost
        # response can replay a saved snapshot, but cannot erase this intent.
        checkpoint = checkpoint.model_copy(update={"phase": "running"})
        attempt = await self._save(item, attempt, checkpoint)
        with self._execution_scope(context, checkpoint, item):
            snapshot = await self._sessions.execute_case_turn(checkpoint.session_id, context=context,
                attempt_id=checkpoint.execution_attempt_id, mode_key=run.mode_key, managed_by_run=True)
        return await self._finish(context, checkpoint, attempt, item, snapshot, mode_key=run.mode_key)

    async def resume_approval(self, *, case, version, run, item, attempt_id: str,
        approval_id: str, payload: ApprovalDecisionRequest):
        context, checkpoint, attempt = await self._load(run=run, item=item, version=version, attempt_id=attempt_id)
        snapshot = await self._snapshot(checkpoint)
        if snapshot is None:
            raise AgentCaseRecoveryRequired("审批续执行缺少原图快照，需要核对原执行")
        if checkpoint.receipt_correction is not None:
            return await self._finish(context, checkpoint, attempt, item, snapshot, mode_key=run.mode_key)
        approvals = await self._store.list_approvals(checkpoint.session_id)
        approval = next((entry for entry in approvals if entry.id == approval_id), None)
        if approval is None:
            raise ValueError("审批不属于本次 Agent 执行")
        snapshot, checkpoint, attempt = await self._continue_approval(context, checkpoint, attempt,
            item, approval, payload, mode_key=run.mode_key)
        return await self._finish(context, checkpoint, attempt, item, snapshot, mode_key=run.mode_key)

    async def _continue_approval(self, context, checkpoint, attempt, item, approval, payload, *, mode_key):
        job = await self._jobs.get_job(str(approval.metadata.get("tool_job_id") or ""))
        if job is not None:
            if (job.session_id != checkpoint.session_id or job.turn_id != checkpoint.turn_id
                or job.tool_key != approval.tool_key
                or job.call_id != str(approval.metadata.get("call_id") or approval.id)
                or job.input_payload != approval.metadata.get("arguments", {})
                or any(job.metadata.get(key) != value for key, value in {
                    "project_id": context.project_id, "run_id": context.run_id,
                    "run_item_id": item.id, "attempt_id": checkpoint.execution_attempt_id}.items())):
                raise ValueError("审批工具响应检查点不属于本次固定执行身份")
            if job.status.value in {"running", "resume_requested"}:
                job = await self._jobs.recover_observed_output(job.id,
                    reason="执行恢复时发现工具响应已保存，但后续结果交付被中断")
        if job is None or job.status.value not in {"waiting_approval", "completed", "partial", "failed", "denied", "cancelled"}:
            raise AgentCaseRecoveryRequired("审批工具结果未知，需核对原任务，不能重复操作")
        checkpoint = checkpoint.model_copy(update={"phase": "running"})
        attempt = await self._save(item, attempt, checkpoint)
        with self._execution_scope(context, checkpoint, item):
            snapshot = await self._sessions.resume_case_approval(checkpoint.session_id, context=context,
                attempt_id=checkpoint.execution_attempt_id, mode_key=mode_key,
                approval_id=approval.id, payload=payload)
        return snapshot, checkpoint, attempt

    async def _finish(self, context, checkpoint, attempt, item, snapshot: SessionSnapshot, *, mode_key):
        resumed_decisions = set()
        while True:
            checkpoint = checkpoint.model_copy(update={"phase": "snapshot", "snapshot_id": snapshot.id,
                "snapshot_version": snapshot.version})
            attempt = await self._save(item, attempt, checkpoint)
            if snapshot.stage != "waiting_approval":
                break
            session = await self._store.get_session(checkpoint.session_id)
            pending = session.metadata.get("pending_turn", {}).get("pending_approval_ids", [])
            stored = {entry.id: entry for entry in await self._store.list_approvals(checkpoint.session_id)
                if entry.metadata.get("turn_id") == checkpoint.turn_id}
            approvals = [stored[key] for key in pending if key in stored]
            if not approvals or not approvals[0].metadata.get("tool_job_id"):
                raise AgentCaseRecoveryRequired("待审批图缺少当前审批及工具任务，需要核对原执行")
            approval = approvals[0]
            if approval.status == ToolApprovalStatus.pending:
                return AgentCaseApprovalWait(checkpoint.session_id, approval.id,
                    str(approval.metadata["tool_job_id"]), approval.reason)
            if approval.id in resumed_decisions:
                raise AgentCaseRecoveryRequired("已保存审批决议未推进原图，需要核对原执行")
            resumed_decisions.add(approval.id)
            logger.info("agent_case_saved_approval_decision_resumed", extra={"run_id": context.run_id,
                "run_item_id": item.id, "execution_attempt_id": checkpoint.execution_attempt_id,
                "result_attempt_id": attempt.id, "approval_id": approval.id, "decision": approval.status.value})
            snapshot, checkpoint, attempt = await self._continue_approval(context, checkpoint, attempt,
                item, approval, ApprovalDecisionRequest(decision=approval.status, reason=approval.decision_note),
                mode_key=mode_key)
        if snapshot.stage != "completed":
            raise AgentCaseRecoveryRequired(f"Agent 图处于 {snapshot.stage}，需恢复原快照而非重新执行")
        state = snapshot.graph_state
        response = ModelInvocationResult(text=state.get("model_response_text", ""),
            response_summary=state.get("model_response_summary", {}))
        records = [ToolExecutionRecord.model_validate(record) for record in state.get("tool_results", [])]
        async def assess(candidate):
            return await self._assessment.assess(context=context,
                receipt=self._assessment.parse_response(candidate,
                    **({"scene_receipt_protocol":context.scene_receipt_protocol} if context.scene_receipt_protocol else {})), tool_records=records,
                session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
                attempt_id=checkpoint.execution_attempt_id, model_key=state.get("selected_model_key", ""),
                lease_token=item.lease_token)

        if checkpoint.receipt_correction is None:
            try:
                outcome = await assess(response)
            except ValueError as exc:
                if context.receipt_correction_protocol is None:
                    raise
                logger.warning("agent_case_receipt_correction_requested", extra={"run_id": context.run_id,
                    "run_item_id": item.id, "snapshot_id": snapshot.id, "validation_type": type(exc).__name__})
                validation_error = str(exc)
            else:
                validation_error = None
        else:
            outcome, validation_error = None, None
        if checkpoint.receipt_correction is not None or validation_error is not None:
            async def save(updated):
                nonlocal attempt
                attempt = await self._save(item, attempt, updated)
            with self._execution_scope(context, checkpoint, item):
                try:
                    response, checkpoint = await self._receipts.correct(context=context, checkpoint=checkpoint,
                        snapshot=snapshot, records=records, validation_error=validation_error, save=save)
                except CaseReceiptOutcomeUnknown as exc:
                    raise AgentCaseRecoveryRequired(str(exc)) from exc
            try:
                outcome = await assess(response)
            except ValueError as exc:
                logger.warning("agent_case_corrected_receipt_rejected", extra={"run_id": context.run_id,
                    "run_item_id": item.id, "logical_call_id": checkpoint.receipt_correction.call_id,
                    "validation_type": type(exc).__name__})
                raise
            outcome.completion.actual["receipt_correction"] = checkpoint.receipt_correction.model_dump(mode="json")
            outcome.completion.evidence_refs.extend(RunEvidenceRef(evidence_type="receipt_correction",
                evidence_id=checkpoint.receipt_correction.call_id + ":" + key, label="只读回执" + key,
                uri=getattr(checkpoint.receipt_correction, key).uri,
                metadata=getattr(checkpoint.receipt_correction, key).model_dump(mode="json"))
                for key in ("request", "response"))
        outcome.completion.actual["execution_identity"] = {
            "session_id": checkpoint.session_id, "turn_id": checkpoint.turn_id,
            "execution_attempt_id": checkpoint.execution_attempt_id, "result_attempt_id": attempt.id,
            "snapshot_id": snapshot.id}
        return outcome

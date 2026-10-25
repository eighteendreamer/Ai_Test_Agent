"""Prepare one owner-bound continuation without invoking models or tools.

No upstream counterpart (checked: LangGraph 1.2.12 interrupt/pending writes,
LangChain 1.4.6 per-call HITL, integration plan §5): framework delivery does
not prove our ToolJob outcome. Validate both before choosing Command or None.
"""
from __future__ import annotations

import asyncio
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
from src.runtime.framework_approval_submission import assert_active_approval
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.schemas.framework_approval import (
    FrameworkContinuationPlan, NativeToolInterrupt, approval_value_hash,
    framework_approval_id, framework_tool_call_id,
)
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ExecutionEvent, ToolApprovalRequest
from src.schemas.tool_job import ToolJobStatus


logger = logging.getLogger(__name__)
TERMINAL_JOBS = frozenset({ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed,
    ToolJobStatus.denied, ToolJobStatus.cancelled})


class FrameworkApprovalContinuation:
    def __init__(self, settings, *, sessions, jobs):
        if not jobs.supports_approval_transaction(settings) or getattr(sessions, "_settings", None) != settings:
            raise ValueError("Native continuation requires the shared PostgreSQL ToolJob transaction")
        self._settings, self._sessions, self._jobs = settings, sessions, jobs
        self._journal = FrameworkApprovalJournal(settings)

    async def prepare(self, owner, approval_id, decision_id):
        try:
            plan = await asyncio.to_thread(self._prepare, owner, approval_id, decision_id)
        except Exception as exc:
            logger.warning("framework_continuation_prepare_rejected", extra={
                "binding_id": owner.binding.binding_id, "task_id": owner.task_id,
                "approval_id": approval_id, "decision_id": decision_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_continuation_prepared", extra={"binding_id": owner.binding.binding_id,
            "task_id": owner.task_id, "approval_id": approval_id, "decision_id": decision_id,
            "receipt_state": plan.receipt.state, "continue_from_checkpoint": plan.continue_from_checkpoint})
        return plan

    def _prepare(self, owner, approval_id, decision_id):
        binding = owner.binding
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            assert_runtime_owner(cur, self._settings, owner)
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE",
                (owner.task_id,))
            task = cur.fetchone()
            if (task is None or task["operation"] != "approval_resume" or task["status"] != "running"
                or task["session_id"] != binding.session_id or task["turn_id"] != binding.turn_id
                or task["attempt_id"] != owner.attempt_id or task["lease_token"] != owner.owner_token
                or task["worker_id"] != owner.worker_id or task["interrupt_requested"]
                or task["reference_id"] != approval_id or task["input_payload"].get("decision_id") != decision_id
                or task["runtime_binding"] is None or RuntimeBinding.model_validate(task["runtime_binding"]) != binding):
                raise FrameworkApprovalRecoveryRequired("Continuation does not match its owned task")
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_session_table} WHERE id=%s", (binding.session_id,))
            session = cur.fetchone()
            assert_active_approval(session, binding, approval_id, {"execution_state": "running"})
            if session["project_id"] != task["project_id"]:
                raise FrameworkApprovalRecoveryRequired("Continuation project changed after claim")
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_approval_table} WHERE id=%s FOR UPDATE",
                (approval_id,))
            row = cur.fetchone()
            link = self._journal.require_link_in_transaction(cur, binding, approval_id, row)
            approval = ToolApprovalRequest.model_validate(row)
            frozen = ToolApprovalRequest.model_validate(task["input_payload"].get("approval"))
            self._journal.assert_business_link(binding, link, frozen.model_dump(mode="python"))
            receipt = self._journal.get_receipt_in_transaction(cur, approval_id)
            if (approval.id != frozen.id or approval.status != frozen.status
                or approval.decision_note != frozen.decision_note or approval.status.value not in {"approved", "denied"}
                or receipt is None or receipt.decision_id != decision_id
                or approval_value_hash(receipt.decision) != receipt.decision_hash
                or receipt.decision != ({"type": "approve"} if approval.status.value == "approved" else
                    {"type": "reject", "message": approval.decision_note or "Human denied execution."})):
                raise FrameworkApprovalRecoveryRequired("Continuation differs from its frozen business decision")
            native = NativeToolInterrupt.model_validate(link.model_dump(include=set(NativeToolInterrupt.model_fields)))
            call_id = framework_tool_call_id(binding, native)
            job = self._jobs.get_job_in_transaction(cur, link.tool_job_id, for_update=True)
            if (link.binding_id != binding.binding_id or link.tool_schema_version != binding.tool_schema_version
                or link.business_call_id != call_id or approval_id != framework_approval_id(binding, native)
                or job is None or job.id != self._jobs.call_job_id(binding.session_id, binding.turn_id, call_id)
                or job.session_id != binding.session_id or job.turn_id != binding.turn_id
                or job.call_id != call_id or job.tool_key != link.tool_key
                or approval_value_hash(job.input_payload) != approval_value_hash(link.arguments)
                or job.metadata.get("framework_binding_id") != binding.binding_id
                or job.metadata.get("framework_native_identity") != native.model_dump(mode="json")):
                raise FrameworkApprovalRecoveryRequired("Continuation ToolJob identity requires reconciliation")
            consumed = self._journal.require_native_decision_in_transaction(cur, binding, link, receipt)
            unstarted = job.status == ToolJobStatus.waiting_approval and job.started_at is None
            if approval.status.value == "approved":
                safe = job.status in TERMINAL_JOBS or (unstarted and not consumed)
            else:
                safe = unstarted or (job.status == ToolJobStatus.denied and job.started_at is None)
            if not safe:
                raise FrameworkApprovalRecoveryRequired("ToolJob outcome requires reconciliation before continuation")
            if consumed:
                receipt = self._journal.reconcile_delivery_in_transaction(cur, owner, approval_id)
                event = ExecutionEvent(id=f"framework-decision:{decision_id}:delivered", session_id=binding.session_id,
                    type="approval.decision_delivered", timestamp=receipt.delivered_at,
                    turn_id=binding.turn_id, trace_id=task["trace_id"], request_id=task["request_id"],
                    attempt_id=owner.attempt_id, worker_id=owner.worker_id,
                    payload={"task_id": owner.task_id, "turn_id": binding.turn_id, "approval_id": approval_id,
                        "decision_id": decision_id, "tool_job_id": job.id, "interrupt_id": link.interrupt_id})
                self._sessions.append_event_in_transaction(cur, binding.session_id, event,
                    turn_lease_token=owner.owner_token)
            else:
                receipt = self._journal.mark_sending_in_transaction(cur, approval_id)
            return FrameworkContinuationPlan(approval=approval, job=job, link=link, receipt=receipt)

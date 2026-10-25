"""Atomic admission of a business decision and its versioned continuation.

No upstream counterpart (checked: LangChain 1.4.6 HITL, LangGraph 1.2.12,
integration plan §4.3): framework interrupts do not own our task/outbox inbox.
Admission records intent only; it never invokes a graph or claims execution.
"""
from __future__ import annotations

import json
import logging
from uuid import uuid4

from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import (
    FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired, framework_approval_tables,
)
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, lock_owner_row, runtime_owner_table
from src.schemas.agent_task import AgentTaskCommandV2, AgentTaskRecord
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ToolApprovalRequest, ToolApprovalStatus


logger = logging.getLogger(__name__)


def assert_active_approval(session, binding, approval_id, owner):
    """Use the active Turn, not Session.waiting_approval, for admission."""
    metadata = session.get("metadata") or {}
    pending = metadata.get("pending_turn") or {}
    control = metadata.get("control") or {}
    bound = (pending.get("context_bundle") or {}).get("runtime_binding")
    if (pending.get("turn_id") != binding.turn_id or bound is None
        or RuntimeBinding.model_validate(bound) != binding
        or approval_id not in pending.get("pending_approval_ids", [])
        or session["status"] not in {"running", "waiting_approval"}
        or control.get("control_state") in {"interrupt_requested", "interrupted", "cancelled"}
        or owner["execution_state"] in {"cancelled", "failed", "interrupted", "recovery_required"}
        or (session.get("lease_active") and metadata.get("turn_lease_turn_id") != binding.turn_id)):
        raise AgentSubmissionConflict("Approval no longer belongs to the active framework Turn")


def assert_claimable_approval(cur, tasks, session, binding, row, owner):
    """Do not let an obsolete continuation bypass Session input ordering."""
    assert_active_approval(session, binding, row["reference_id"], owner)
    if row["operation"] == "approval_recover":
        from src.runtime.framework_approval_recovery import require_successor_proof

        require_successor_proof(cur, tasks._settings, row)
    cur.execute(f"SELECT * FROM {tasks._settings.database.postgres_approval_table} "
        "WHERE id=%s AND session_id=%s FOR UPDATE", (row["reference_id"], row["session_id"]))
    approval = cur.fetchone()
    journal = FrameworkApprovalJournal(tasks._settings)
    journal.require_link_in_transaction(cur, binding, row["reference_id"], approval)
    receipt = journal.get_receipt_in_transaction(cur, row["reference_id"])
    if (receipt is None or row["input_payload"].get("decision_id") != receipt.decision_id
        or approval["status"] not in {"approved", "denied"}
        or receipt.decision["type"] != ("approve" if approval["status"] == "approved" else "reject")):
        raise FrameworkApprovalRecoveryRequired("Continuation no longer matches its persisted decision")


class FrameworkApprovalSubmission:
    """Reuse the inbox transaction/event facilities without a second scheduler."""

    def __init__(self, tasks):
        self._tasks = tasks
        self._settings = tasks._settings
        self._journal = FrameworkApprovalJournal(self._settings)

    def binding_for(self, session_id, approval_id):
        """Read the canonical binding, including an idempotent closed-Turn retry.

        This is routing information only. The submission transaction rechecks
        the active Turn, approval, link and decision before admitting any work.
        """
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            cur.execute(f"SELECT metadata FROM {self._settings.database.postgres_session_table} WHERE id=%s",
                (session_id,))
            session = cur.fetchone()
            if session is None:
                raise KeyError(session_id)
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_approval_table} "
                "WHERE session_id=%s AND id=%s", (session_id, approval_id))
            approval = cur.fetchone()
            if approval is None:
                raise KeyError(approval_id)
            links, _ = framework_approval_tables(self._settings)
            cur.execute(f"SELECT owner.runtime_binding FROM {links} link "
                f"LEFT JOIN {runtime_owner_table(self._settings)} owner ON owner.binding_id=link.binding_id "
                "WHERE link.approval_id=%s", (approval_id,))
            row = cur.fetchone()
            if row is None:
                pending = (session["metadata"] or {}).get("pending_turn") or {}
                if ((approval["metadata"] or {}).get("framework_binding_id") is not None
                    or (pending.get("context_bundle") or {}).get("runtime_binding") is not None):
                    raise AgentSubmissionConflict("Framework-bound approval requires a valid v2 continuation link")
                return None
            try:
                binding = RuntimeBinding.model_validate(row["runtime_binding"])
            except ValidationError as exc:
                raise FrameworkApprovalRecoveryRequired("Approval canonical runtime binding requires reconciliation") from exc
            if binding.session_id != session_id:
                raise FrameworkApprovalRecoveryRequired("Approval canonical binding belongs to another Session")
            self._journal.require_link_in_transaction(cur, binding, approval_id, approval)
            return binding

    def submit(self, command, approval_id, payload):
        try:
            return self._submit(command, approval_id, payload)
        except Exception as exc:
            logger.warning("framework_approval_submission_rejected", extra={
                "session_id": getattr(command, "session_id", None), "turn_id": getattr(command, "turn_id", None),
                "task_id": getattr(command, "task_id", None), "approval_id": approval_id,
                "error_type": type(exc).__name__,
            })
            raise

    def _submit(self, command, approval_id, payload):
        if (not isinstance(command, AgentTaskCommandV2) or command.operation != "approval_resume"
            or command.task_type != "agent_turn" or command.reference_id != approval_id):
            raise ValueError("v2 approval command identity is invalid")
        command = AgentTaskCommandV2.model_validate(command.model_dump(mode="json"))
        if payload.decision not in {ToolApprovalStatus.approved, ToolApprovalStatus.denied}:
            raise ValueError("Approval decision must be approved or denied")
        tasks, binding = self._tasks, command.runtime_binding
        # A different client_request_id or reason cannot create a second intent.
        effective_key = tasks._idempotency_key("approval_resume_v2", approval_id)
        content_hash = tasks._input_hash("approval_resume_v2", {
            "decision": payload.decision.value, "runtime_binding_fingerprint": binding.fingerprint,
        }, reference_id=approval_id)
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            session = tasks._lock_session(cur, command.session_id)
            try:
                owner = lock_owner_row(cur, self._settings, binding)
            except ValueError as exc:
                raise AgentSubmissionConflict(str(exc)) from exc
            if session["project_id"] != command.project_id:
                raise AgentSubmissionConflict("Session project changed before approval submission")
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_approval_table} "
                "WHERE id=%s AND session_id=%s FOR UPDATE", (approval_id, command.session_id))
            approval = cur.fetchone()
            if approval is None:
                raise KeyError(approval_id)
            metadata = approval.get("metadata") or {}
            if (metadata.get("approval_type") == "ui_recording"
                or metadata.get("source") in {"test_run_case_execution", "test_run_agent_execution"}
                or metadata.get("proxy_child_session_id") or metadata.get("proxy_child_approval_id")):
                raise AgentSubmissionConflict("This approval type must use its dedicated approval endpoint")
            self._journal.require_link_in_transaction(cur, binding, approval_id, approval)
            existing = tasks._find_idempotent(cur, command.session_id, effective_key, content_hash)
            if existing is not None:
                receipt = self._journal.get_receipt_in_transaction(cur, approval_id)
                if (approval["status"] != payload.decision.value or receipt is None
                    or existing["runtime_binding"] != binding.model_dump(mode="json")
                    or existing["input_payload"].get("decision_id") != receipt.decision_id):
                    raise FrameworkApprovalRecoveryRequired("Continuation and decision receipt disagree")
                logger.info("framework_approval_decision_reused", extra={"approval_id": approval_id,
                    "binding_id": binding.binding_id, "task_id": existing["id"], "decision_id": receipt.decision_id})
                return AgentTaskRecord.model_validate(existing)
            assert_active_approval(session, binding, approval_id, owner)
            if approval["status"] != ToolApprovalStatus.pending.value:
                raise AgentSubmissionConflict("Approval already resolved without its v2 continuation")
            cur.execute(f"SELECT id FROM {tasks._tasks} WHERE session_id=%s AND turn_id=%s "
                "AND interrupt_requested=TRUE LIMIT 1", (binding.session_id, binding.turn_id))
            if cur.fetchone() is not None:
                raise AgentSubmissionConflict("Framework Turn has a pending interrupt request")
            cur.execute(f"UPDATE {self._settings.database.postgres_approval_table} "
                "SET status=%s,decision_note=%s,resolved_at=clock_timestamp() WHERE id=%s RETURNING *",
                (payload.decision.value, payload.reason, approval_id))
            approval = cur.fetchone()
            receipt = self._journal.queue_decision_in_transaction(cur, binding, approval_id, decision_id=str(uuid4()))
            input_payload = {"approval": ToolApprovalRequest.model_validate(approval).model_dump(mode="json"),
                "decision_id": receipt.decision_id}
            cur.execute(f"""INSERT INTO {tasks._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 priority,idempotency_key,input_hash,input_payload,reference_id,parent_task_id,root_trace_id,runtime_binding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) RETURNING *""",
                (command.task_id, command.session_id, command.operation, command.turn_id, command.project_id,
                 command.request_id, command.trace_id, command.run_id, command.run_item_id, command.priority,
                 effective_key, content_hash, json.dumps(input_payload, ensure_ascii=False), approval_id,
                 command.parent_task_id, command.root_trace_id, binding.model_dump_json()))
            row = cur.fetchone()
            stream = self._settings.orchestration.agent_task_v2_stream
            tasks._event(cur, row, "approval.resolved", {"approval_id": approval_id,
                "tool_key": approval["tool_key"], "decision": payload.decision.value,
                "decision_id": receipt.decision_id})
            tasks._event(cur, row, "approval.continuation_queued", {"approval_id": approval_id,
                "tool_key": approval["tool_key"], "decision": payload.decision.value,
                "decision_id": receipt.decision_id, "queue": stream})
            cur.execute(f"INSERT INTO {self._settings.database.postgres_task_outbox_table} "
                "(event_key,stream,payload) VALUES (%s,%s,%s::jsonb)",
                (f"agent_turn:{command.task_id}", stream, command.model_dump_json()))
        logger.info("framework_approval_decision_submitted", extra={"session_id": binding.session_id,
            "turn_id": binding.turn_id, "binding_id": binding.binding_id, "approval_id": approval_id,
            "decision_id": receipt.decision_id, "task_id": row["id"], "decision": payload.decision.value,
            "queue": stream})
        return AgentTaskRecord.model_validate(row)

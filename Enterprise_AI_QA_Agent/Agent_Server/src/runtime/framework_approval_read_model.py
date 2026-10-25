"""Read durable application facts without resuming a graph or settling work.

No upstream counterpart (checked: framework approval journal/continuation,
integration plan §5, LangGraph persistence): public business progress joins
application receipts, jobs and leases; native checkpoints are not a UI API.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_continuation import TERMINAL_JOBS
from src.runtime.framework_approval_journal import (
    FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired, framework_approval_tables,
)
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, runtime_owner_table
from src.schemas.approval_progress import FrameworkApprovalProgress
from src.schemas.framework_approval import FrameworkInterruptLink, approval_value_hash
from src.schemas.runtime_binding import RuntimeBinding


logger = logging.getLogger(__name__)


class FrameworkApprovalReadModel:
    def __init__(self, settings):
        self._settings = settings

    def list_turn(self, session_id, turn_id):
        if not turn_id:
            return []
        settings = self._settings
        links, receipts = framework_approval_tables(settings)
        with postgres_connect(settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            # Older/legacy inboxes may have no ToolJob storage at all. Do not
            # join that storage unless this Turn has native business approvals.
            cur.execute(f"SELECT id FROM {settings.database.postgres_approval_table} "
                "WHERE session_id=%s AND metadata->>'turn_id'=%s "
                "AND metadata->>'framework_binding_id' IS NOT NULL "
                "AND COALESCE(metadata->>'approval_type','') <> 'ui_recording' "
                "AND COALESCE(metadata->>'source','') NOT IN ('test_run_case_execution','test_run_agent_execution') "
                "AND COALESCE(metadata->>'proxy_child_session_id','')='' "
                "AND COALESCE(metadata->>'proxy_child_approval_id','')=''", (session_id, turn_id))
            ids = [row["id"] for row in cur.fetchall()]
            if not ids:
                return []
            # All facts used for each phase come from one statement snapshot.
            cur.execute(f"""SELECT to_jsonb(a) AS approval, l.link, statement_timestamp() AS observed_at,
                to_jsonb(r) AS receipt, to_jsonb(j) AS job, to_jsonb(t) AS task,
                o.runtime_binding, o.execution_state, o.task_id AS owner_task_id,
                o.attempt_id AS owner_attempt_id, o.owner_token,
                (o.expires_at > statement_timestamp()
                 AND (s.metadata->>'turn_lease_expires_at')::timestamptz > statement_timestamp()
                 AND s.metadata->>'turn_lease_token'=o.owner_token
                 AND s.metadata->>'turn_lease_turn_id'=o.turn_id) AS lease_active
                FROM {settings.database.postgres_approval_table} a
                LEFT JOIN {links} l ON l.approval_id=a.id
                LEFT JOIN {runtime_owner_table(settings)} o ON o.binding_id=l.binding_id
                LEFT JOIN {receipts} r ON r.approval_id=a.id
                LEFT JOIN {settings.database.postgres_tool_job_table} j ON j.id=l.link->>'tool_job_id'
                LEFT JOIN {settings.database.postgres_agent_task_table} t
                  ON t.session_id=a.session_id AND t.reference_id=a.id
                  AND t.operation='approval_resume' AND t.runtime_binding IS NOT NULL
                JOIN {settings.database.postgres_session_table} s ON s.id=a.session_id
                WHERE a.id=ANY(%s) ORDER BY a.created_at,a.id""", (ids,))
            return [self._project(row, session_id, turn_id) for row in cur.fetchall()]

    def _project(self, row, session_id, turn_id):
        approval, job, receipt, task = (row[key] for key in ("approval", "job", "receipt", "task"))
        item = FrameworkApprovalProgress(approval_id=approval["id"], turn_id=turn_id,
            tool_key=approval["tool_key"], tool_name=approval["tool_name"], decision=approval["status"],
            phase="recovery_required", observed_at=row["observed_at"], decided_at=approval.get("resolved_at"))
        if job:
            item.tool_job_id, item.tool_status = job["id"], job["status"]
        if task:
            item.task_status = task["status"]
        if receipt:
            item.delivery_state = receipt["state"]
            item.delivered_at, item.applied_at = receipt["delivered_at"], receipt["applied_at"]
        try:
            self._validate(row, session_id, turn_id)
            if item.decision == "pending":
                item.phase = "awaiting_decision"
            elif receipt["applied_at"] is not None:
                item.phase = "applied"
                outcome = receipt["proof"]["application"]["outcome"]
                item.outcome = "rejected" if outcome == "human_rejected" else outcome
            elif job["status"] in TERMINAL_JOBS:
                item.phase = "applying_result"
            elif job["status"] == "running":
                item.phase = "executing"
            else:
                item.phase = "decision_submitted"
        except (FrameworkApprovalRecoveryRequired, ValidationError) as exc:
            logger.warning("framework_approval_progress_requires_recovery", extra={
                "session_id": session_id, "turn_id": turn_id, "approval_id": item.approval_id,
                "error_type": type(exc).__name__,
                "reason": str(exc) if isinstance(exc, FrameworkApprovalRecoveryRequired) else "invalid_identity_schema"})
        return item

    @staticmethod
    def _validate(row, session_id, turn_id):
        approval, job, receipt, task = (row[key] for key in ("approval", "job", "receipt", "task"))
        binding = RuntimeBinding.model_validate(row["runtime_binding"])
        link = FrameworkInterruptLink.model_validate(row["link"])
        FrameworkApprovalJournal.assert_business_link(binding, link, approval)
        if (binding.session_id != session_id or binding.turn_id != turn_id
            or link.binding_id != binding.binding_id or job is None
            or job["session_id"] != session_id or job["turn_id"] != turn_id
            or job["tool_key"] != link.tool_key or job["call_id"] != link.business_call_id
            or job["metadata"].get("framework_binding_id") != binding.binding_id
            or approval_value_hash(job["input_payload"]) != approval_value_hash(link.arguments)):
            raise FrameworkApprovalRecoveryRequired("Progress business identity changed")
        if approval["status"] == "pending":
            if receipt or task or job["status"] != "waiting_approval":
                raise FrameworkApprovalRecoveryRequired("Pending approval has execution facts")
            return
        expected = ({"type": "approve"} if approval["status"] == "approved" else
            {"type": "reject", "message": approval.get("decision_note") or "Human denied execution."})
        if (receipt is None or receipt["decision"] != expected
            or receipt["decision_hash"] != approval_value_hash(expected) or task is None
            or task["input_payload"].get("decision_id") != receipt["decision_id"]
            or task["runtime_binding"] != binding.model_dump(mode="json")):
            raise FrameworkApprovalRecoveryRequired("Progress lacks its frozen decision task")
        if receipt["applied_at"] is not None:
            evidence = (receipt["proof"] or {}).get("application") or {}
            if (receipt["state"] != "delivered" or receipt["delivered_at"] is None
                or job["status"] not in TERMINAL_JOBS
                or evidence.get("decision_hash") != receipt["decision_hash"]
                or evidence.get("tool_job_id") != job["id"]
                or evidence.get("tool_job_status") != job["status"]
                or evidence.get("tool_output_hash") != approval_value_hash(job["output_payload"])
                or evidence.get("outcome") not in {"human_rejected", "governance_denied", "tool_result"}):
                raise FrameworkApprovalRecoveryRequired("Applied progress differs from application evidence")
            return
        if (task["status"] in {"failed", "cancelled", "interrupted", "recovery_required"}
            or row["execution_state"] in {"cancelled", "failed", "interrupted", "recovery_required"}
            or (task["status"] == "running" and (not row["lease_active"]
                or row["execution_state"] != "running"
                or row["owner_task_id"] != task["id"] or row["owner_attempt_id"] != task["attempt_id"]
                or row["owner_token"] != task["lease_token"]))
            or (job["status"] == "running" and task["status"] != "running")):
            raise FrameworkApprovalRecoveryRequired("Progress execution authority expired or stopped")

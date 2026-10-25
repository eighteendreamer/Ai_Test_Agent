"""Project native approvals without publishing half of a business identity.

No upstream counterpart (checked: official per-call HITL, existing Session/
ToolJob transaction ports, integration plan §4–5): the framework does not own
business approval records. Reprojection never replaces a human decision or
resets an existing ToolJob.
"""
from __future__ import annotations

import asyncio
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.schemas.framework_approval import (
    NativeToolInterrupt, PreparedFrameworkApproval, approval_value_hash,
    framework_approval_id, framework_tool_call_id,
)
from src.schemas.session import ExecutionEvent, ToolApprovalRequest


logger = logging.getLogger(__name__)


class FrameworkApprovalProjection:
    def __init__(self, settings, *, sessions, jobs):
        if not jobs.supports_approval_transaction(settings) or getattr(sessions, "_settings", None) != settings:
            raise ValueError("Native approval projection requires the shared PostgreSQL ToolJob transaction")
        self._settings, self._sessions, self._jobs = settings, sessions, jobs
        self._journal = FrameworkApprovalJournal(settings)

    async def project(self, owner, proposal):
        return await asyncio.to_thread(self._project, owner, proposal)

    async def load(self, owner, approval_id):
        try:
            return await asyncio.to_thread(self._load, owner, approval_id)
        except Exception as exc:
            logger.warning("framework_approval_projection_load_rejected", extra={
                "binding_id": owner.binding.binding_id, "approval_id": approval_id,
                "error_type": type(exc).__name__})
            raise

    def _load(self, owner, approval_id):
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            assert_runtime_owner(cur, self._settings, owner)
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_approval_table} WHERE id=%s FOR UPDATE",
                (approval_id,))
            row = cur.fetchone()
            self._journal.require_link_in_transaction(cur, owner.binding, approval_id, row)
            return ToolApprovalRequest.model_validate(row)

    def _project(self, owner, proposal):
        try:
            return self._project_transaction(owner, proposal)
        except Exception as exc:
            logger.warning("framework_approval_projection_rejected", extra={
                "binding_id": owner.binding.binding_id, "error_type": type(exc).__name__})
            raise

    def _project_transaction(self, owner, proposal):
        proposal = PreparedFrameworkApproval.model_validate(proposal.model_dump(mode="json"))
        binding, link, approval, job = owner.binding, proposal.link, proposal.approval, proposal.job
        native = NativeToolInterrupt.model_validate(link.model_dump(include=set(NativeToolInterrupt.model_fields)))
        call_id = framework_tool_call_id(binding, native)
        self._journal.assert_business_link(binding, link, approval.model_dump(mode="python"))
        if (link.binding_id != binding.binding_id or link.tool_schema_version != binding.tool_schema_version
            or link.business_call_id != call_id
            or approval.id != link.approval_id or approval.id != framework_approval_id(binding, native)
            or approval.status.value != "pending" or approval.resolved_at is not None or approval.decision_note is not None
            or approval.metadata.get("framework_binding_id") != binding.binding_id
            or approval.metadata.get("framework_call_id") != call_id
            or approval.metadata.get("framework_native_identity") != native.model_dump(mode="json")
            or job.id != link.tool_job_id or job.id != self._jobs.call_job_id(binding.session_id, binding.turn_id, call_id)
            or job.session_id != binding.session_id or job.turn_id != binding.turn_id
            or job.call_id != call_id or job.tool_key != link.tool_key or job.input_payload != link.arguments
            or job.status.value != "waiting_approval" or job.started_at is not None or job.completed_at is not None
            or job.metadata.get("framework_binding_id") != binding.binding_id
            or job.metadata.get("framework_native_identity") != native.model_dump(mode="json")):
            raise FrameworkApprovalRecoveryRequired("Native business projection has inconsistent identities")
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            assert_runtime_owner(cur, self._settings, owner)
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_approval_table} WHERE id=%s FOR UPDATE",
                (approval.id,))
            existing = cur.fetchone()
            if existing is not None:
                saved = ToolApprovalRequest.model_validate(existing)
                if (saved.session_id != approval.session_id or saved.tool_key != approval.tool_key
                    or any(saved.metadata.get(key) != approval.metadata.get(key) for key in (
                        "turn_id", "call_id", "tool_job_id", "approval_scope_hash", "framework_binding_id",
                        "framework_call_id", "framework_native_identity"))):
                    raise FrameworkApprovalRecoveryRequired("Existing approval has a different native projection")
                approval = saved
            stored_job = self._jobs.create_job_if_absent_in_transaction(cur, job)
            if (stored_job.session_id != job.session_id or stored_job.turn_id != job.turn_id
                or stored_job.call_id != job.call_id or stored_job.tool_key != job.tool_key
                or approval_value_hash(stored_job.input_payload) != approval_value_hash(job.input_payload)
                or stored_job.metadata.get("framework_native_identity") != job.metadata["framework_native_identity"]
                or stored_job.metadata.get("framework_binding_id") != binding.binding_id
                or (existing is None and stored_job.status.value != "waiting_approval")):
                raise FrameworkApprovalRecoveryRequired("Existing ToolJob requires native approval reconciliation")
            if existing is None:
                self._sessions.save_approval_in_transaction(cur, binding.session_id, approval)
            self._journal.link_interrupt_in_transaction(cur, owner, link)
            event = ExecutionEvent(id=f"framework-approval:{approval.id}:created", session_id=binding.session_id,
                type="approval.created", timestamp=approval.created_at,
                payload={"turn_id": binding.turn_id, "approval_id": approval.id, "tool_key": approval.tool_key,
                    "tool_name": approval.tool_name, "reason": approval.reason,
                    "message": "A tool execution approval request has been created."})
            self._sessions.append_event_in_transaction(cur, binding.session_id, event,
                turn_lease_token=owner.owner_token)
        logger.info("framework_approval_projected", extra={"binding_id": binding.binding_id,
            "approval_id": approval.id, "tool_job_id": stored_job.id, "reused": existing is not None})
        return PreparedFrameworkApproval(approval=approval, job=stored_job, link=link)

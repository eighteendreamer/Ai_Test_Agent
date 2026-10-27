"""Prepare one owner-bound continuation without invoking models or tools.

No upstream counterpart (checked: LangGraph 1.2.12 interrupt/pending writes,
LangChain 1.4.6 per-call HITL, integration plan §5): framework delivery does
not prove our ToolJob outcome. Validate both before choosing Command or None.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

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
from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY, parse_tool_projection


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

    async def record_graph_input(self, owner, approval_id, decision_id, raw):
        """Freeze per-task application inputs before invoking Command/None.

        No upstream counterpart (checked: prepared graph input, approval
        journal, LangGraph 1.2.12 persistence): a graph checkpoint does not own
        the assistant/snapshot preparation. Extend the private original stage,
        not public audit payloads or the immutable business decision.
        """
        from src.schemas.framework_graph_input import FrameworkPreparedGraphInputAsset, FrameworkApprovalGraphInput, parse_graph_input_asset
        from src.runtime.framework_message_stages import message_stage_table, validate_message_stage

        asset = FrameworkPreparedGraphInputAsset.model_validate(raw)

        def save():
            with postgres_connect(self._settings) as connection, connection.cursor() as cur:
                configure_runtime_owner_transaction(cur, self._settings)
                plan, _, _ = self._owned_plan_in_transaction(cur, owner, approval_id, decision_id)
                if asset.runtime_preparation.identity.binding != owner.binding:
                    raise FrameworkApprovalRecoveryRequired("Approval graph input differs from its captured binding")
                original = FrameworkApprovalGraphInput(task_id=owner.task_id, attempt_id=owner.attempt_id,
                    owner_epoch=owner.owner_epoch, approval_id=approval_id, decision_id=decision_id,
                    continue_from_checkpoint=plan.continue_from_checkpoint, graph_input_asset=asset)
                table = message_stage_table(self._settings)
                cur.execute(f"SELECT record FROM {table} WHERE binding_id=%s FOR UPDATE", (owner.binding.binding_id,))
                row = cur.fetchone()
                if row is None:
                    raise FrameworkApprovalRecoveryRequired("Approval graph input has no original message stage")
                stage = validate_message_stage(row["record"])
                manifest = dict(stage.source_manifest or {})
                original_input = manifest.get("graph_input_asset")
                initial = parse_graph_input_asset(original_input) if original_input is not None else None
                if initial is None or initial.schema_version != 2:
                    raise FrameworkApprovalRecoveryRequired("Approval graph input has no original prepared input")
                initial_binding = initial.runtime_preparation.identity.binding
                if (stage.binding_id != owner.binding.binding_id or initial_binding != owner.binding
                    or manifest.get("binding_fingerprint") != owner.binding.fingerprint):
                    raise FrameworkApprovalRecoveryRequired("Approval graph input differs from its original prepared binding")
                if stage.phase != "runtime_submitted" or "runtime_result_asset" in manifest:
                    raise FrameworkApprovalRecoveryRequired("Approval graph input cannot change a completed application source")
                inputs = dict(manifest.get("approval_graph_inputs", {}))
                payload = original.model_dump(mode="json")
                if owner.task_id in inputs:
                    if inputs[owner.task_id] != payload:
                        raise FrameworkApprovalRecoveryRequired("Original approval graph input is immutable")
                    return original
                inputs[owner.task_id] = payload
                manifest["approval_graph_inputs"] = inputs
                cur.execute("SELECT clock_timestamp() AS now")
                saved = validate_message_stage(stage.model_copy(update={"schema_version": 16,
                    "source_manifest": manifest, "source_sha256": approval_value_hash(manifest),
                    "updated_at": cur.fetchone()["now"]}).model_dump(mode="json"))
                assert_runtime_owner(cur, self._settings, owner)
                cur.execute(f"UPDATE {table} SET record=%s::jsonb WHERE binding_id=%s",
                    (saved.model_dump_json(), owner.binding.binding_id))
                return original

        try:
            saved = await asyncio.to_thread(save)
        except Exception as exc:
            logger.warning("framework_approval_graph_input_rejected", extra={"task_id": owner.task_id,
                "binding_id": owner.binding.binding_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_approval_graph_input_frozen", extra={"task_id": owner.task_id,
            "binding_id": owner.binding.binding_id, "owner_epoch": owner.owner_epoch,
            "asset_sha256": saved.graph_input_asset.asset_sha256})
        return saved

    async def settle(self, owner, approval_id, decision_id, *, rejection_projector=None):
        """Commit denial material beside applied, using a pure synchronous projector.

        The callback may only adapt already locked facts to application display
        policy. It must not perform I/O, mutate a graph or execute a tool.
        Historical callers without it retain the original receipt contract.
        """
        try:
            plan = await asyncio.to_thread(self._settle, owner, approval_id, decision_id, rejection_projector)
        except Exception as exc:
            logger.warning("framework_continuation_settlement_rejected", extra={
                "binding_id": owner.binding.binding_id, "task_id": owner.task_id,
                "approval_id": approval_id, "decision_id": decision_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_continuation_settled", extra={"binding_id": owner.binding.binding_id,
            "task_id": owner.task_id, "approval_id": approval_id, "decision_id": decision_id,
            "tool_job_id": plan.job.id, "tool_job_status": plan.job.status.value,
            "tool_projection_sha256": ((plan.receipt.proof or {}).get("application", {})
                .get(TOOL_PROJECTION_KEY, {}).get("sha256"))})
        return plan

    def _settle(self, owner, approval_id, decision_id, rejection_projector=None):
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            plan = self._prepare_in_transaction(cur, owner, approval_id, decision_id)
            if not plan.continue_from_checkpoint:
                raise FrameworkApprovalRecoveryRequired("Native tool result has no consumed decision")
            message, evidence = self._journal.require_tool_result_in_transaction(cur, owner.binding, plan.link)
            job = plan.job
            if plan.approval.status.value == "denied":
                if message.status != "error" or not isinstance(message.content, str) or not message.content:
                    raise FrameworkApprovalRecoveryRequired("Native rejection result requires reconciliation")
                if job.status == ToolJobStatus.waiting_approval:
                    job.status, job.summary = ToolJobStatus.denied, message.content
                    job.output_payload = {"status": "denied", "approval_id": approval_id,
                        "native_feedback": message.content, "instruction_authority": "none"}
                    job.updated_at = job.completed_at = datetime.utcnow()
                    job = self._jobs.save_job_in_transaction(cur, job, framework_owner=owner)
            evidence = self._application_evidence(owner.binding, plan, job, message, evidence)
            if plan.approval.status.value == "denied":
                applied = (plan.receipt.proof or {}).get("application", {})
                if TOOL_PROJECTION_KEY in applied or rejection_projector is not None:
                    # The projector is pure application policy. Run it only on
                    # first settlement, under the same owner/Job/receipt locks.
                    # A missing original after applied is not permission to
                    # regenerate it with current code, metadata or clock.
                    if plan.receipt.applied_at is not None and TOOL_PROJECTION_KEY not in applied:
                        raise FrameworkApprovalRecoveryRequired("Applied rejection has no original projection material")
                    if plan.receipt.applied_at is None and TOOL_PROJECTION_KEY in applied:
                        raise FrameworkApprovalRecoveryRequired("Rejection projection has no applied timestamp")
                    material = (applied[TOOL_PROJECTION_KEY] if plan.receipt.applied_at is not None
                        else rejection_projector(job, message))
                    evidence = self._rejection_projection_evidence(owner.binding, plan, job, message, evidence, material)
            receipt = self._journal.record_application_in_transaction(cur, plan.receipt, evidence)
            self._sessions.append_event_in_transaction(cur, owner.binding.session_id, ExecutionEvent(
                id=f"framework-decision:{decision_id}:applied", session_id=owner.binding.session_id,
                type="approval.decision_applied", timestamp=receipt.applied_at, turn_id=owner.binding.turn_id,
                trace_id=job.trace_id, attempt_id=owner.attempt_id, worker_id=owner.worker_id,
                payload={"task_id": owner.task_id, "turn_id": owner.binding.turn_id, "approval_id": approval_id,
                    "decision_id": decision_id, "tool_job_id": job.id, "status": job.status.value, "outcome": evidence["outcome"]}),
                turn_lease_token=owner.owner_token)
            return FrameworkContinuationPlan(approval=plan.approval, job=job, link=plan.link, receipt=receipt)

    @staticmethod
    def _rejection_projection_evidence(binding, plan, job, native, evidence, material):
        try:
            original = parse_tool_projection(material)
            original.require_binding(binding, plan.link)
        except ValueError as exc:
            raise FrameworkApprovalRecoveryRequired("Original rejection projection requires reconciliation") from exc
        record = original.record
        if (job.status != ToolJobStatus.denied or job.started_at is not None or job.completed_at is None
            or job.summary != native.content or job.output_payload != {"status": "denied",
                "approval_id": plan.approval.id, "native_feedback": native.content, "instruction_authority": "none"}
            or record.job_id != job.id or record.approval_id != plan.approval.id or record.status != "denied"
            or record.tool_name != job.tool_name or record.summary != job.summary
            or record.input != job.input_payload or record.output != job.output_payload
            or record.trace_id != (job.trace_id or "") or record.started_at is not None
            or record.completed_at != job.completed_at or original.message.created_at != job.completed_at
            or original.model_content != native.content):
            raise FrameworkApprovalRecoveryRequired("Original rejection projection differs from its denied ToolJob")
        return {**evidence, TOOL_PROJECTION_KEY: original.model_dump(mode="json"),
            "projection_job_sha256": approval_value_hash(job.model_dump(mode="json"))}

    @staticmethod
    def _application_evidence(binding, plan, job, message, evidence):
        if plan.approval.status.value == "denied":
            if message.status != "error" or not isinstance(message.content, str) or not message.content:
                raise FrameworkApprovalRecoveryRequired("Native rejection result requires reconciliation")
            outcome = "human_rejected"
        else:
            content = message.content
            if isinstance(message.artifact, dict) and TOOL_PROJECTION_KEY in message.artifact:
                try:
                    material = FrameworkToolProjection.from_tool_message(message, binding=binding, source=plan.link)
                except ValueError as exc:
                    raise FrameworkApprovalRecoveryRequired("Original approved tool projection requires reconciliation") from exc
                if material.record.job_id != job.id or material.record.approval_id != plan.approval.id:
                    raise FrameworkApprovalRecoveryRequired("Original tool projection differs from its approved ToolJob")
                content = material.model_content
            try:
                payload = json.loads(content) if isinstance(content, str) else None
            except (TypeError, ValueError) as exc:
                raise FrameworkApprovalRecoveryRequired("Native governed result is not a valid tool response") from exc
            expected_status = "denied" if job.status == ToolJobStatus.cancelled else job.status.value
            if (message.status != "success" or not isinstance(payload, dict)
                or payload.get("status") != expected_status or payload.get("instruction_authority") != "none"):
                raise FrameworkApprovalRecoveryRequired("Native governed result differs from its terminal ToolJob")
            outcome = "governance_denied" if job.status == ToolJobStatus.denied and job.started_at is None else "tool_result"
        return {**evidence, "decision_hash": plan.receipt.decision_hash, "tool_job_id": job.id,
            "tool_job_status": job.status.value, "tool_output_hash": approval_value_hash(job.output_payload), "outcome": outcome}

    def _prepare(self, owner, approval_id, decision_id):
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            return self._prepare_in_transaction(cur, owner, approval_id, decision_id)

    def _prepare_in_transaction(self, cur, owner, approval_id, decision_id):
        plan, consumed, task = self._owned_plan_in_transaction(cur, owner, approval_id, decision_id)
        binding = owner.binding
        if consumed:
            receipt = self._journal.reconcile_delivery_in_transaction(cur, owner, approval_id)
            event = ExecutionEvent(id=f"framework-decision:{decision_id}:delivered", session_id=binding.session_id,
                type="approval.decision_delivered", timestamp=receipt.delivered_at,
                turn_id=binding.turn_id, trace_id=task["trace_id"], request_id=task["request_id"],
                attempt_id=owner.attempt_id, worker_id=owner.worker_id,
                payload={"task_id": owner.task_id, "turn_id": binding.turn_id, "approval_id": approval_id,
                    "decision_id": decision_id, "tool_job_id": plan.job.id, "interrupt_id": plan.link.interrupt_id})
            self._sessions.append_event_in_transaction(cur, binding.session_id, event,
                turn_lease_token=owner.owner_token)
        else:
            receipt = self._journal.mark_sending_in_transaction(cur, approval_id)
        return plan.model_copy(update={"receipt": receipt})

    async def read_tool_projection(self, owner, approval_id, decision_id):
        """Read original material, not a fresh ToolJob output transformation.

        This neither settles a decision nor grants recovery/execution rights.
        Retained native task writes survive message summarization and carry
        the exact artifact even if the current transcript no longer does.
        """
        try:
            material = await asyncio.to_thread(self._read_tool_projection, owner, approval_id, decision_id)
        except Exception as exc:
            logger.warning("framework_tool_projection_read_rejected", extra={
                "binding_id": owner.binding.binding_id, "approval_id": approval_id,
                "error_type": type(exc).__name__})
            raise
        logger.info("framework_tool_projection_read", extra={"binding_id": owner.binding.binding_id,
            "approval_id": approval_id, "material_sha256": material.sha256})
        return material

    def _read_tool_projection(self, owner, approval_id, decision_id):
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            plan, consumed, _ = self._owned_plan_in_transaction(cur, owner, approval_id, decision_id)
            return self._applied_tool_projection_in_transaction(cur, owner.binding, plan, consumed)

    def read_applied_tool_projection_in_transaction(self, cur, binding, task_id):
        """Read historical originals; never acquire or reuse an execution owner.

        No upstream counterpart (checked: owned projection reader, approval
        receipt journal, LangGraph 1.2.12 persistence, v4 §9.2/§10.2): an
        expired producer cannot execute again just to read its known result.
        Load the original task from the same database; caller dictionaries or
        hashes are not source authority. Caller still owns access control,
        transaction lifetime and any separate recovery admission.
        """
        try:
            configure_runtime_owner_transaction(cur, self._settings)
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE", (task_id,))
            task = cur.fetchone()
            if (task is None or task["operation"] not in {"approval_resume", "approval_recover"}
                or task["session_id"] != binding.session_id or task["turn_id"] != binding.turn_id
                or task["runtime_binding"] != binding.model_dump(mode="json") or not task["attempt_id"]):
                raise FrameworkApprovalRecoveryRequired("Applied projection has no matching original approval task")
            plan, consumed = self.inspect_decision_in_transaction(cur, binding, task)
            material = self._applied_tool_projection_in_transaction(cur, binding, plan, consumed)
        except Exception as exc:
            logger.warning("framework_applied_tool_projection_read_rejected", extra={"task_id": task_id,
                "binding_id": binding.binding_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_applied_tool_projection_read", extra={"task_id": task_id,
            "binding_id": binding.binding_id, "material_sha256": material.sha256})
        return material

    def _applied_tool_projection_in_transaction(self, cur, binding, plan, consumed):
        if not consumed or plan.receipt.applied_at is None:
            raise FrameworkApprovalRecoveryRequired("Original tool projection requires its applied approval")
        message, evidence = self._journal.require_tool_result_in_transaction(cur, binding, plan.link)
        applied = (plan.receipt.proof or {}).get("application")
        expected = self._application_evidence(binding, plan, plan.job, message, evidence)
        if plan.receipt.decision["type"] == "reject":
            raw = (applied or {}).get(TOOL_PROJECTION_KEY)
            expected = self._rejection_projection_evidence(binding, plan, plan.job, message, expected, raw)
        if applied != expected:
            raise FrameworkApprovalRecoveryRequired("Original tool projection differs from its applied native result")
        try:
            if plan.receipt.decision["type"] == "reject":
                return parse_tool_projection(raw).require_binding(binding, plan.link)
            return FrameworkToolProjection.from_tool_message(message, binding=binding, source=plan.link)
        except ValueError as exc:
            raise FrameworkApprovalRecoveryRequired("Original approved tool projection requires reconciliation") from exc

    def _owned_plan_in_transaction(self, cur, owner, approval_id, decision_id):
        binding = owner.binding
        assert_runtime_owner(cur, self._settings, owner)
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE",
            (owner.task_id,))
        task = cur.fetchone()
        if (task is None or task["operation"] not in {"approval_resume", "approval_recover"} or task["status"] != "running"
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
        if task["operation"] == "approval_recover":
            from src.runtime.framework_approval_recovery import require_successor_proof

            require_successor_proof(cur, self._settings, task)
        plan, consumed = self.inspect_decision_in_transaction(cur, binding, task)
        return plan, consumed, task

    def inspect_decision_in_transaction(self, cur, binding, task):
        """Read frozen decision/native/Job facts under the caller's owner locks.

        Shared by execution preflight and deterministic recovery. This method
        neither sends a decision nor changes receipt delivery/application facts.
        """
        approval_id, decision_id = task["reference_id"], task["input_payload"].get("decision_id")
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
        return FrameworkContinuationPlan(approval=approval, job=job, link=link, receipt=receipt), consumed

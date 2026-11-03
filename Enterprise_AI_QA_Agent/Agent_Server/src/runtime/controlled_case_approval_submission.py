"""Dedicated parked-case decisions on the original task/journal/outbox.

No upstream counterpart (checked: FrameworkApprovalSubmission, controlled
wait originals, v4 §7.3/§12.1): native HITL does not own the formal RunItem.
This internal admission is not installed in HTTP/main. It records one intent,
never claims either lease, invokes the graph or grants native delivery rights.
"""
import asyncio
from copy import deepcopy
import json
import logging
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import TypeAdapter

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_execution import lock_case_delivery
from src.runtime.framework_application_projection import _require_event_intent
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_approval_submission import assert_active_approval
from src.runtime.framework_event_completion import original_checkpoint_source
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, lock_owner_row
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommandV2, AgentTaskRecord
from src.schemas.controlled_case_approval_submission import ControlledCaseApprovalAdmission, case_approval_task_id
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ApprovalDecisionRequest, ExecutionEvent, ToolApprovalStatus


logger = logging.getLogger(__name__)
ADMISSION_KEY = "controlled_case_approval_admission"
ENVELOPE = ("session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id", "root_trace_id")


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def approval_events(proof, command):
    return [ExecutionEvent(id=str(uuid5(NAMESPACE_URL, f"controlled-case:{kind}:{proof.task_id}")),
        session_id=command.session_id, timestamp=proof.admitted_at, type=kind,
        request_id=command.request_id, trace_id=command.trace_id, turn_id=command.turn_id,
        run_id=command.run_id, run_item_id=command.run_item_id,
        payload={"task_id": proof.task_id, "turn_id": command.turn_id, "approval_id": proof.approval_id,
            "decision": proof.decision, "decision_id": proof.decision_id,
            "admission_sha256": proof.admission_sha256, "scope": proof.scope})
        for kind in ("approval.resolved", "approval.continuation_queued")]


class ControlledCaseApprovalSubmission:
    def __init__(self, waits):
        self._waits, self._tasks = waits, waits._tasks
        self._settings = self._tasks._settings
        self._journal = waits._projection._journal

    async def submit(self, delivery, approval_id, payload, *, capabilities):
        scope = {"approval_id": approval_id, "operation": "formal_approval_admission", "automatic_replay": False}
        try:
            command = deepcopy(capabilities.require_command_format(delivery))
            scope.update(task_id=command.task_id, binding_id=command.runtime_binding.binding_id)
            capabilities.require_runtime_artifact(command.runtime_binding)
            payload = ApprovalDecisionRequest.model_validate(payload.model_dump(mode="json"))
            if payload.decision not in {ToolApprovalStatus.approved, ToolApprovalStatus.denied}:
                raise ValueError("Approval decision must be approved or denied")
            if capabilities.controlled_case_approval_admission_max_version < 1:
                raise TaskDeferred("framework_case_approval_admission_format_unavailable")
            result, reused = await asyncio.to_thread(self._submit, command, approval_id, payload, capabilities, scope)
        except (Exception, asyncio.CancelledError) as exc:
            # Cancellation of this waiter does not prove its SQL rolled back.
            logger.warning("controlled_case_approval_submission_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "decision_admission_receipt_unknown"})
            raise
        logger.info("controlled_case_approval_decision_reused" if reused else "controlled_case_approval_decision_admitted",
            extra={**scope, "continuation_task_id": result.id, "decision": payload.decision.value})
        return result

    def _command(self, proof, origin, binding):
        return AgentTaskCommandV2(operation="case_approval", task_id=proof.task_id,
            runtime_binding=binding, parent_task_id=origin["id"], reference_id=proof.approval_id,
            priority=origin["priority"], created_at=proof.admitted_at, **{name: origin[name] for name in ENVELOPE},
            case_approval_reference=proof.model_dump(mode="json", include={"schema_version", "origin_task_id",
                "origin_attempt_id", "run_item_execution_attempt_id", "admission_sha256"}))

    def _existing(self, cur, row, origin, binding, wait, approval, receipt, payload, capabilities, key):
        if approval["status"] != payload.decision.value:
            raise AgentSubmissionConflict("Formal approval already has a different decision")
        raw = row["input_payload"]
        _require(isinstance(raw, dict) and set(raw) == {ADMISSION_KEY}, "Formal decision admission payload changed")
        proof = capabilities.require_case_approval_admission_format(raw[ADMISSION_KEY])
        command = self._command(proof, origin, binding)
        _require(receipt is not None and (proof.origin_task_id, proof.origin_attempt_id, proof.origin_owner_epoch,
                proof.run_item_execution_attempt_id, proof.binding_fingerprint, proof.input_sha256,
                proof.submission_sha256, proof.wait_sha256, proof.approval_id, proof.decision_id,
                proof.decision, proof.decision_sha256)
            == (wait.task_id, wait.attempt_id, wait.owner_epoch, wait.run_item_execution_attempt_id,
                binding.fingerprint, wait.input_sha256, wait.submission_sha256, wait.receipt_sha256,
                approval["id"], receipt.decision_id, approval["status"], receipt.decision_hash)
            and approval["resolved_at"] == proof.admitted_at
            and receipt.decision == ({"type": "approve"} if proof.decision == "approved" else
                {"type": "reject", "message": approval["decision_note"] or "Human denied execution."}),
            "Formal decision admission differs from its original decision/parking receipt")
        _require(row["operation"] == "case_approval" and row["id"] == proof.task_id
            and all(row[name] == getattr(command, name) for name in (*ENVELOPE, "reference_id", "parent_task_id", "priority"))
            and row["runtime_binding"] == binding.model_dump(mode="json") and row["idempotency_key"] == key
            and row["user_message_id"] is None and row["input_hash"] == self._tasks._input_hash("case_approval",
                {"input": raw, "runtime_binding_fingerprint": binding.fingerprint}),
            "Formal decision admission envelope changed")
        cur.execute(f"SELECT stream,payload FROM {self._settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("agent_turn:" + proof.task_id,))
        outbox = cur.fetchone()
        _require(outbox is not None and outbox["stream"] == self._settings.orchestration.agent_task_v2_stream
            and capabilities.require_command_format(outbox["payload"]) == command,
            "Formal decision admission has no matching original outbox")
        for event in approval_events(proof, command):
            cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} WHERE id=%s FOR SHARE", (event.id,))
            _require_event_intent(cur.fetchone(), event)
        return AgentTaskRecord.model_validate(row), True

    def _require_parked(self, cur, command, case, session, owner, wait, origin, capabilities):
        binding = command.runtime_binding
        run = case.store._read_run(cur, command.run_id)
        pending_ids = (session["metadata"].get("pending_turn") or {}).get("pending_approval_ids")
        _require(isinstance(pending_ids, list) and len(pending_ids) == len(wait.batch)
            and all(entry.approval_id in pending_ids for entry in wait.batch),
            "Formal decision requires its complete parked batch")
        assert_active_approval(session, binding, wait.batch[0].approval_id, owner)
        _require(session["project_id"] == command.project_id == run.project_id
            and session["status"] == "waiting_approval" and not session["lease_active"]
            and run.status not in {"completed", "cancelled"}
            and case.item.status == case.attempt.status == "waiting_approval" and case.item.result_id is None
            and case.item.attempt_no == case.attempt.attempt_no and case.attempt.recovered_from_attempt_id is None
            and (case.item.lease_owner, case.item.lease_token) == (case.attempt.worker_id, case.attempt.lease_token)
            and case.item.lease_expires_at is None
            and (owner["task_id"], owner["attempt_id"], owner["owner_epoch"], owner["execution_state"])
                == (wait.task_id, wait.attempt_id, wait.owner_epoch, "completed")
            and (session["metadata"].get("pending_turn") or {}).get("controlled_case_wait_sha256") == wait.receipt_sha256,
            "Formal decision requires its current parked original; dual-owner continuation is separate")
        cur.execute(f"SELECT id FROM {self._tasks._tasks} WHERE session_id=%s AND turn_id=%s "
            "AND interrupt_requested=TRUE LIMIT 1", (binding.session_id, binding.turn_id))
        _require(cur.fetchone() is None, "Formal decision Turn has a pending interrupt request")
        # A historical ACK is valid after later activity. NEW decision admission
        # is not: prove the current parked native batch and unchanged ledger.
        ledger = PostgresFrameworkModelCalls(self._settings)._snapshot(cur, binding)
        _require(approval_value_hash(ledger.model_dump(mode="json")) == wait.model_ledger_sha256,
            "Formal parked decision has later model activity requiring reconciliation")
        natives = self._waits._batch_original(cur, binding, wait, capabilities, project_id=command.project_id)
        from src.application.deep_agents.native_approval_batch import require_pending_tool_interrupts_original

        source = original_checkpoint_source(cur, self._settings, binding, origin, wait.owner_epoch)
        original = capabilities.require_case_input_format(origin["input_payload"]["case_execution_input"])
        require_pending_tool_interrupts_original(cur, self._settings, source, original.graph_input, natives)
        for entry in wait.batch:
            cur.execute(f"SELECT status,started_at,completed_at FROM {self._settings.database.postgres_tool_job_table} "
                "WHERE id=%s FOR SHARE", (entry.tool_job_id,))
            job = cur.fetchone()
            _require(job is not None and job["status"] == "waiting_approval"
                and job["started_at"] is job["completed_at"] is None, "Formal parked ToolJob has already progressed")

    def _submit(self, command, approval_id, payload, capabilities, scope):
        tasks, settings, binding = self._tasks, self._settings, command.runtime_binding
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            session = tasks._lock_session(cur, binding.session_id)
            case, origin = lock_case_delivery(cur, tasks, command, capabilities)
            owner = lock_owner_row(cur, settings, binding)
            wait = self._waits.require_original_in_transaction(cur, command, capabilities, scope)
            _require(approval_id in {entry.approval_id for entry in wait.batch}, "Approval is outside the original formal wait batch")
            cur.execute(f"SELECT * FROM {settings.database.postgres_approval_table} WHERE id=%s FOR UPDATE", (approval_id,))
            approval = cur.fetchone()
            self._journal.require_link_in_transaction(cur, binding, approval_id, approval)
            receipt = self._journal.get_receipt_in_transaction(cur, approval_id)
            if receipt is not None:
                capabilities.require_approval_journal_format("receipt", receipt.model_dump(mode="json"))
            task_id = case_approval_task_id(origin["id"], approval_id)
            key = tasks._idempotency_key("case_approval", task_id)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s OR (session_id=%s AND idempotency_key=%s) FOR UPDATE",
                (task_id, binding.session_id, key))
            rows = cur.fetchall()
            if rows:
                _require(len(rows) == 1, "Formal decision has conflicting successor identities")
                return self._existing(cur, rows[0], origin, binding, wait, approval, receipt, payload, capabilities, key)
            self._require_parked(cur, command, case, session, owner, wait, origin, capabilities)
            if approval["status"] != "pending" or receipt is not None or approval["resolved_at"] is not None:
                raise AgentSubmissionConflict("Formal approval has a prior decision without its dedicated continuation")
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            cur.execute(f"UPDATE {settings.database.postgres_approval_table} SET status=%s,decision_note=%s,resolved_at=%s "
                "WHERE id=%s RETURNING *", (payload.decision.value, payload.reason, now, approval_id))
            receipt = self._journal.queue_decision_in_transaction(cur, binding, approval_id, decision_id=str(uuid4()))
            values = TypeAdapter(dict).dump_python(dict(schema_version=1, scope="controlled_case_approval_admission_only",
                task_id=task_id, origin_task_id=wait.task_id, origin_attempt_id=wait.attempt_id,
                origin_owner_epoch=wait.owner_epoch, run_item_execution_attempt_id=case.attempt.id,
                binding_fingerprint=binding.fingerprint, input_sha256=wait.input_sha256,
                submission_sha256=wait.submission_sha256, wait_sha256=wait.receipt_sha256,
                approval_id=approval_id, decision_id=receipt.decision_id, decision=payload.decision.value,
                decision_sha256=receipt.decision_hash, admitted_at=now), mode="json")
            proof = ControlledCaseApprovalAdmission(**values, admission_sha256=approval_value_hash(values))
            capabilities.require_case_approval_admission_format(proof.model_dump(mode="json"))
            successor = self._command(proof, origin, binding)
            raw = {ADMISSION_KEY: proof.model_dump(mode="json")}
            content_hash = tasks._input_hash("case_approval", {"input": raw, "runtime_binding_fingerprint": binding.fingerprint})
            cur.execute(f"""INSERT INTO {tasks._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 priority,idempotency_key,input_hash,input_payload,reference_id,parent_task_id,root_trace_id,runtime_binding)
                VALUES (%s,%s,'case_approval',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) RETURNING *""",
                (task_id, successor.session_id, successor.turn_id, successor.project_id, successor.request_id,
                 successor.trace_id, successor.run_id, successor.run_item_id, successor.priority, key, content_hash,
                 json.dumps(raw, ensure_ascii=False), approval_id, origin["id"], successor.root_trace_id, binding.model_dump_json()))
            row = cur.fetchone()
            cur.execute(f"INSERT INTO {settings.database.postgres_task_outbox_table} (event_key,stream,payload) VALUES (%s,%s,%s::jsonb)",
                ("agent_turn:" + task_id, settings.orchestration.agent_task_v2_stream, successor.model_dump_json()))
            for event in approval_events(proof, successor):
                _require(tasks._sessions.append_event_in_transaction(cur, binding.session_id, event),
                    "Formal decision audit exists without its atomic admission")
            return AgentTaskRecord.model_validate(row), False

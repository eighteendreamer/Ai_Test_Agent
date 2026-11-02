"""Scoped Task leases and the original formal completion kernel, never graph owners.

No upstream counterpart (checked: approval settlement, Task claim/finish kernels,
RunStore completion, v4 §10.1/§12.1): adopting a graph owner would replace the
lost formal producer. Keep its owner intact; reserve only the existing Session
and successor Task attempt. Store the atomic receipt in that Task's result.
"""
import asyncio
from copy import deepcopy
import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_settlement import _hash
from src.runtime.framework_application_projection import _require, _require_event_intent
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.controlled_case_settlement_recovery import (
    ControlledCaseRecoverySettlementReceipt, ControlledCaseSettlementLease, settlement_recovery_task_id,
)
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)
RECEIPT_KEY = "case_recovery_settlement_receipt"


def settlement_event(receipt, source):
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-recovery-settlement:" + receipt.task_id)),
        session_id=source.owner.binding.session_id, timestamp=receipt.settled_at, type="test_run.case_recovery_settled",
        payload={"task_id": receipt.task_id, "attempt_id": receipt.attempt_id, "turn_id": source.owner.binding.turn_id,
            "origin_task_id": receipt.origin_task_id, "origin_attempt_id": receipt.origin_attempt_id,
            "run_id": source.locked.item.run_id, "run_item_id": source.locked.item.id,
            "result_id": receipt.result_id, "status": source.checkpoint.assessment.completion.status,
            "scope": receipt.scope, "settlement_sha256": receipt.receipt_sha256, "automatic_replay": False})


class ControlledCaseRecoverySettlement:
    def __init__(self, admission):
        self._admission, self._tasks = admission, admission._tasks
        self._settings, self._originals = admission._settings, admission._originals

    async def claim(self, delivery, *, worker_id, capabilities):
        return await self._run("claim", delivery, capabilities, worker_id=worker_id)

    async def complete(self, lease, *, capabilities):
        return await self._run("complete", lease, capabilities)

    async def renew(self, lease, *, lease_seconds, capabilities):
        return await self._run("renew", lease, capabilities, lease_seconds=lease_seconds)

    async def acknowledge(self, delivery, *, capabilities):
        return await self._run("ack", delivery, capabilities)

    async def _run(self, operation, value, capabilities, **options):
        scope = {"operation": operation, "automatic_replay": False}
        try:
            if operation in {"claim", "ack"}:
                value = deepcopy(capabilities.require_command_format(value))
                _require(value.operation == "case_settle", "Controlled settlement requires its successor delivery")
                binding, task_id = value.runtime_binding, value.task_id
            else:
                _require(isinstance(value, ControlledCaseSettlementLease), "Controlled settlement requires its scoped task lease")
                value = deepcopy(value)
                binding, task_id = value.binding, value.task_id
            scope["task_id"] = task_id
            capabilities.require_runtime_artifact(binding)
            if operation != "ack" and (not capabilities.controlled_case_settlement_available
                or capabilities.controlled_case_recovery_receipt_max_version < 1):
                raise TaskDeferred("framework_case_settlement_writer_unavailable")
            result = await asyncio.to_thread(self._transaction, operation, value, capabilities, **options)
        except asyncio.CancelledError:
            logger.warning("controlled_case_recovery_settlement_unknown", extra={**scope,
                "recovery_reason": "settlement_transaction_receipt_unknown"})
            raise
        except Exception as exc:
            logger.warning("controlled_case_recovery_settlement_rejected", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "settlement_scope_unsettled",
                "defer_reason": str(exc) if isinstance(exc, TaskDeferred) else None})
            raise
        logger.info("controlled_case_recovery_settlement_" + operation, extra=scope)
        return result

    def _locked(self, cur, value, capabilities):
        tasks = self._tasks
        if isinstance(value, ControlledCaseSettlementLease):
            cur.execute(f"SELECT payload FROM {self._settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                ("agent_turn:" + value.task_id,))
            outbox = cur.fetchone()
            _require(outbox is not None, "Controlled settlement lease has no original delivery")
            command = capabilities.require_command_format(outbox["payload"])
            _require(command.task_id == value.task_id and command.runtime_binding == value.binding
                and command.case_settlement_reference == value.reference, "Controlled settlement lease lineage changed")
        else:
            command = value
        tasks._lock_session(cur, command.session_id)
        reference = command.case_settlement_reference
        _require(command.operation == "case_settle" and reference is not None, "Controlled settlement delivery changed scope")
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s", (reference.origin_task_id,))
        origin = cur.fetchone()
        _require(origin is not None, "Controlled settlement original producer is missing")
        asset = capabilities.require_case_input_format((origin["input_payload"] or {}).get("case_execution_input"))
        from src.runtime.controlled_case_settlement_recovery import ENVELOPE

        original = AgentTaskCommandV2(task_id=origin["id"], operation="case_execute",
            runtime_binding=capabilities.require_binding_format(origin["runtime_binding"]),
            reference_id=origin["reference_id"], priority=origin["priority"], created_at=origin["created_at"],
            **{name: origin[name] for name in ENVELOPE}, case_execution_reference={
                "run_item_execution_attempt_id": reference.run_item_execution_attempt_id, "input_sha256": asset.asset_sha256})
        source, assessment = self._originals._expired_assessment_source(cur, original, capabilities)
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (command.task_id,))
        row = cur.fetchone()
        _require(row is not None, "Controlled settlement successor is missing")
        proof = self._admission._validate_envelope(cur, row, source, capabilities,
            tasks._idempotency_key("case_settle", command.task_id))
        current_origin, _, _ = self._originals._lock_identity_rows(cur, source.owner)
        _require(command == self._admission._command(proof, current_origin, source.owner.binding),
            "Controlled settlement delivery differs from its admission")
        self._task_attempt(cur, row)
        return source, assessment, row, proof

    def _task_attempt(self, cur, row):
        if row["attempt_id"] is None:
            _require(row["attempt_no"] == 0 and row["status"] == "queued", "Controlled settlement has no captured task attempt")
            return None
        cur.execute(f"SELECT * FROM {self._tasks._attempts} WHERE id=%s FOR UPDATE", (row["attempt_id"],))
        attempt = cur.fetchone()
        _require(attempt is not None and (attempt["task_id"], attempt["attempt_no"], attempt["worker_id"], attempt["lease_token"])
            == (row["id"], row["attempt_no"], row["worker_id"], row["lease_token"]),
            "Controlled settlement task attempt identity changed")
        return attempt

    def _pending(self, cur, source, asset, row, proof):
        current, _ = self._admission._proof(cur, source, asset, proof.admitted_at)
        _require(proof == current and row["result_payload"] is None, "Controlled settlement admitted original facts changed")
        case = source.locked
        session = self._tasks._lock_session(cur, source.owner.binding.session_id)
        run = case.store._read_run(cur, case.item.run_id)
        _require(session["status"] not in {"running", "waiting_approval", "cancelled"}
            and run.status not in {"completed", "cancelled"}
            and case.item.status == case.attempt.status == "running" and case.item.result_id is None
            and not row["interrupt_requested"], "Controlled settlement is cancelled or no longer pending")
        return session

    def _lease_identity(self, row, lease):
        if (row["id"], row["attempt_id"], row["attempt_no"], row["worker_id"], row["lease_token"]) != (
                lease.task_id, lease.attempt_id, lease.attempt_no, lease.worker_id, lease.lease_token):
            raise ContinuationLeaseLostError("Controlled settlement task lease changed")

    def _fence(self, cur, row, lease):
        self._lease_identity(row, lease)
        attempt = self._task_attempt(cur, row)
        session = self._tasks._lock_session(cur, lease.binding.session_id)
        cur.execute("SELECT clock_timestamp() AS now, COALESCE(%s::timestamptz > clock_timestamp(),false) AS active",
            (session["metadata"].get("turn_lease_expires_at"),))
        clock = cur.fetchone()
        metadata = session["metadata"]
        if (row["status"] != "claimed" or attempt["status"] != "claimed" or attempt["finished_at"] is not None
            or not clock["active"] or row["interrupt_requested"]
            or (metadata.get("turn_lease_turn_id"), metadata.get("turn_lease_owner"), metadata.get("turn_lease_token"))
                != (lease.binding.turn_id, lease.worker_id, lease.lease_token)):
            raise ContinuationLeaseLostError("Controlled settlement task lease is no longer active")
        return clock["now"]

    def _transaction(self, operation, value, capabilities, *, worker_id=None, lease_seconds=None):
        tasks, settings = self._tasks, self._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            source, asset, row, proof = self._locked(cur, value, capabilities)
            if operation == "ack":
                return self._ack_source(cur, source, asset, row, proof, capabilities)
            if operation == "complete" and row["status"] == "completed":
                self._lease_identity(row, value)
                return self._ack_source(cur, source, asset, row, proof, capabilities)
            session = self._pending(cur, source, asset, row, proof)
            if operation == "claim":
                _require(isinstance(worker_id, str) and bool(worker_id.strip()), "Controlled settlement worker identity is required")
                if row["status"] not in {"queued", "claimed"}:
                    raise TaskDeferred("framework_case_settlement_task_terminal")
                _require(row["status"] != "queued" or row["attempt_id"] is None,
                    "Controlled settlement queued task has a previous attempt")
                if session["lease_active"]:
                    raise TaskDeferred("framework_case_settlement_session_occupied")
                if row["status"] == "claimed":
                    attempt = self._task_attempt(cur, row)
                    _require(attempt["status"] == "claimed" and attempt["finished_at"] is None,
                        "Controlled settlement previous attempt is already terminal")
                    tasks._end_attempt(cur, row, "interrupted", "settlement_lease_expired_before_commit")
                    tasks._event(cur, row, "agent_task.interrupted", {"status": "interrupted",
                        "reason": "settlement_lease_expired_before_commit", "scope": proof.scope})
                row = tasks._claim_task_attempt(cur, row, worker_id)
                tasks._event(cur, row, "agent_task.claimed", {"scope": proof.scope, "admission_sha256": proof.asset_sha256})
                return ControlledCaseSettlementLease(binding=source.owner.binding, task_id=row["id"],
                    attempt_id=row["attempt_id"], attempt_no=row["attempt_no"], worker_id=row["worker_id"],
                    lease_token=row["lease_token"], reference=value.case_settlement_reference)
            now = self._fence(cur, row, value)
            if operation == "renew":
                _require(type(lease_seconds) is int and lease_seconds > 0, "Controlled settlement renewal requires positive seconds")
                cur.execute(f"UPDATE {tasks._session_table} SET metadata=metadata || jsonb_build_object("
                    "'turn_lease_expires_at',to_jsonb(clock_timestamp()+%s*INTERVAL '1 second')) WHERE id=%s",
                    (lease_seconds, value.binding.session_id))
                return True
            case = source.locked
            tasks._confirm_application_settlement(cur, row,
                source.checkpoint.projection_receipts.application_projection_commit)
            result = case.store._complete_locked_item_in_transaction(cur, case.item, case.attempt, asset.completion, now)
            tasks._update_task_status(cur, row, "completed", "completed", None, execution_returned=True)
            tasks._clear_lease(cur, row)
            # Refresh actual terminal rows through the same original reader.
            source, asset, row, proof = self._locked(cur, value, capabilities)
            hashes = self._terminal_materials(cur, source, asset, row, proof, result.id)
            values = TypeAdapter(Any).dump_python(dict(schema_version=1, scope="controlled_case_recovery_settlement_only",
                task_id=row["id"], attempt_id=row["attempt_id"], origin_task_id=proof.origin_task_id,
                origin_attempt_id=proof.origin_attempt_id, origin_owner_epoch=proof.origin_owner_epoch,
                binding_fingerprint=proof.binding_fingerprint, admission_sha256=proof.asset_sha256,
                assessment_sha256=asset.asset_sha256, result_id=result.id, settled_at=now, **hashes), mode="json")
            receipt = ControlledCaseRecoverySettlementReceipt(**values, receipt_sha256=approval_value_hash(values))
            capabilities.require_case_recovery_receipt_format(receipt.model_dump(mode="json"))
            cur.execute(f"UPDATE {tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s",
                (json.dumps({RECEIPT_KEY: receipt.model_dump(mode="json")}), row["id"]))
            _require(tasks._sessions.append_event_in_transaction(cur, row["session_id"], settlement_event(receipt, source)),
                "Controlled recovery settlement audit exists without its atomic receipt")
            return receipt

    def _terminal_materials(self, cur, source, asset, row, proof, result_id):
        current, _ = self._admission._proof(cur, source, asset, proof.admitted_at)
        # Only formal completion changes the RunItem/RunItem-attempt rows.
        # The immutable admission itself is retained, not re-signed in place.
        exclude = {"run_item_sha256", "run_item_attempt_sha256", "asset_sha256"}
        _require(current.model_dump(exclude=exclude) == proof.model_dump(exclude=exclude),
            "Controlled recovery settlement original producer changed")
        attempt = self._task_attempt(cur, row)
        session = self._tasks._lock_session(cur, row["session_id"])
        _require(row["status"] == attempt["status"] == "completed" and row["result_status"] == "completed"
            and not row["interrupt_requested"] and row["error_code"] is attempt["error_code"] is None
            and attempt["finished_at"] is not None and session["metadata"].get("turn_lease_token") != row["lease_token"]
            and session["metadata"].get("turn_lease_turn_id") != row["turn_id"],
            "Controlled recovery settlement task terminal facts changed")
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} WHERE session_id=%s "
            "AND type='agent_task.completed' AND payload->>'task_id'=%s FOR SHARE", (row["session_id"], row["id"]))
        events = cur.fetchall()
        _require(len(events) == 1 and events[0]["payload"].get("execution_returned") is True
            and events[0]["payload"].get("result_status") == "completed",
            "Controlled recovery settlement has no unique task completion event")
        case_hashes = self._originals._case_result_materials(cur, source.locked, result_id, asset)
        # Normalize just the self-referential receipt slot, never other columns.
        task = {**dict(row), "result_payload": None}
        return {**case_hashes, "origin_task_sha256": current.origin_task_sha256,
            "origin_attempt_sha256": current.origin_attempt_sha256, "origin_owner_sha256": current.origin_owner_sha256,
            "settlement_task_sha256": _hash(task), "settlement_attempt_sha256": _hash(dict(attempt)),
            "task_event_sha256": _hash(dict(events[0]))}

    def _ack_source(self, cur, source, asset, row, proof, capabilities):
        raw = row["result_payload"]
        _require(isinstance(raw, dict) and set(raw) == {RECEIPT_KEY}, "Controlled recovery settlement receipt is missing or changed")
        receipt = capabilities.require_case_recovery_receipt_format(raw[RECEIPT_KEY])
        expected = {"task_id": row["id"], "attempt_id": row["attempt_id"], "origin_task_id": proof.origin_task_id,
            "origin_attempt_id": proof.origin_attempt_id, "origin_owner_epoch": proof.origin_owner_epoch,
            "binding_fingerprint": proof.binding_fingerprint, "admission_sha256": proof.asset_sha256,
            "assessment_sha256": asset.asset_sha256}
        _require(all(getattr(receipt, key) == value for key, value in expected.items()),
            "Controlled recovery settlement receipt identity changed")
        hashes = self._terminal_materials(cur, source, asset, row, proof, receipt.result_id)
        _require(all(getattr(receipt, key) == value for key, value in hashes.items()),
            "Controlled recovery settlement terminal originals changed")
        event = settlement_event(receipt, source)
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} WHERE id=%s FOR SHARE", (event.id,))
        _require_event_intent(cur.fetchone(), event)
        return receipt

    def acknowledge_original_in_transaction(self, cur, original, capabilities):
        source, asset = self._originals._expired_assessment_source(cur, original, capabilities)
        identity = settlement_recovery_task_id(source.owner.task_id, source.owner.attempt_id)
        cur.execute(f"SELECT * FROM {self._tasks._tasks} WHERE id=%s FOR UPDATE", (identity,))
        row = cur.fetchone()
        _require(row is not None, "Controlled recovery settlement successor is missing")
        proof = self._admission._validate_envelope(cur, row, source, capabilities,
            self._tasks._idempotency_key("case_settle", identity))
        return self._ack_source(cur, source, asset, row, proof, capabilities)

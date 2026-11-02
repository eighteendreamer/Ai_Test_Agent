"""Formal settlement lineage on the existing task/outbox transaction.

No upstream counterpart (checked: FrameworkMessageRecovery, approval settlement,
ControlledCaseAdmission/Settlements, v4 §10.1/§12.1): formal RunItems have no
message stage, and the original failed producer must remain unchanged. Issue
only an immutable settlement admission, not a graph or finalizer permission.
"""
import asyncio
from copy import deepcopy
import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_settlement import ControlledCaseSettlements, _hash
from src.runtime.framework_application_projection import _require, _require_event_intent
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommandV2, AgentTaskRecord
from src.schemas.controlled_case_settlement_recovery import ControlledCaseSettlementAdmission, settlement_recovery_task_id
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)
ENVELOPE = ("session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id", "root_trace_id")
ADMISSION_KEY = "case_settlement_admission"


def admission_event(proof, command):
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-settlement-admission:" + proof.task_id)),
        session_id=command.session_id, timestamp=proof.admitted_at, type="test_run.case_settlement_admitted",
        payload={"task_id": proof.task_id, "turn_id": command.turn_id, "origin_task_id": proof.origin_task_id,
            "origin_attempt_id": proof.origin_attempt_id, "origin_owner_epoch": proof.origin_owner_epoch,
            "run_id": command.run_id, "run_item_id": command.run_item_id,
            "run_item_execution_attempt_id": proof.run_item_execution_attempt_id,
            "scope": proof.scope, "admission_sha256": proof.asset_sha256,
            "assessment_sha256": proof.assessment_sha256, "automatic_replay": False})


class ControlledCaseSettlementRecovery:
    def __init__(self, tasks):
        self._tasks, self._settings = tasks, tasks._settings
        self._originals = ControlledCaseSettlements(tasks)

    async def claim(self, delivery, *, worker_id, capabilities):
        from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

        return await ControlledCaseRecoverySettlement(self).claim(delivery, worker_id=worker_id, capabilities=capabilities)

    async def complete(self, lease, *, capabilities):
        from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

        return await ControlledCaseRecoverySettlement(self).complete(lease, capabilities=capabilities)

    async def renew(self, lease, *, lease_seconds, capabilities):
        from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

        return await ControlledCaseRecoverySettlement(self).renew(lease, lease_seconds=lease_seconds, capabilities=capabilities)

    async def defer(self, lease, *, reason, capabilities):
        from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

        return await ControlledCaseRecoverySettlement(self).defer(lease, reason=reason, capabilities=capabilities)

    async def acknowledge(self, delivery, *, capabilities):
        from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

        return await ControlledCaseRecoverySettlement(self).acknowledge(delivery, capabilities=capabilities)

    async def ensure(self, delivery, *, capabilities):
        scope = {"operation": "case_settlement_admission"}
        try:
            command = deepcopy(capabilities.require_command_format(delivery))
            scope["origin_task_id"] = command.task_id
            capabilities.require_runtime_artifact(command.runtime_binding)
            receipt, reused = await asyncio.to_thread(self._ensure, command, capabilities)
        except asyncio.CancelledError:
            logger.warning("controlled_case_settlement_admission_unknown", extra={**scope,
                "recovery_reason": "admission_receipt_unknown", "automatic_replay": False})
            raise
        except Exception as exc:
            logger.warning("controlled_case_settlement_admission_rejected", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "settlement_originals_unsettled",
                "defer_reason": str(exc) if isinstance(exc, TaskDeferred) else None,
                "automatic_replay": False})
            raise
        logger.info("controlled_case_settlement_admission_reused" if reused else "controlled_case_settlement_admitted",
            extra={**scope, "task_id": receipt.id, "run_item_id": receipt.run_item_id,
                "automatic_replay": False})
        return receipt

    def _proof(self, cur, source, asset, admitted_at):
        case = source.locked
        origin, attempt, owner = self._originals._lock_identity_rows(cur, source.owner)
        cur.execute(f"SELECT * FROM {case.store._item_table} WHERE id=%s", (case.item.id,))
        item = dict(cur.fetchone())
        cur.execute(f"SELECT * FROM {case.store._attempt_table} WHERE id=%s", (case.attempt.id,))
        case_attempt = dict(cur.fetchone())
        values = dict(schema_version=1, scope="controlled_case_settlement_admission_only",
            task_id=settlement_recovery_task_id(source.owner.task_id, source.owner.attempt_id),
            origin_task_id=source.owner.task_id, origin_attempt_id=source.owner.attempt_id,
            origin_owner_epoch=source.owner.owner_epoch, binding_fingerprint=source.owner.binding.fingerprint,
            run_item_execution_attempt_id=case.attempt.id, input_sha256=asset.input_sha256,
            application_commit_sha256=asset.application_commit_sha256, assessment_sha256=asset.asset_sha256,
            origin_task_sha256=_hash(dict(origin)), origin_attempt_sha256=_hash(dict(attempt)),
            origin_owner_sha256=_hash(dict(owner)), run_item_sha256=_hash(item), run_item_attempt_sha256=_hash(case_attempt),
            admitted_at=admitted_at)
        values = TypeAdapter(Any).dump_python(values, mode="json")
        return ControlledCaseSettlementAdmission(**values, asset_sha256=approval_value_hash(values)), origin

    def _command(self, proof, origin, binding):
        return AgentTaskCommandV2(task_id=proof.task_id, operation="case_settle", runtime_binding=binding,
            parent_task_id=proof.origin_task_id, reference_id=origin["reference_id"], priority=origin["priority"],
            created_at=proof.admitted_at, **{name: origin[name] for name in ENVELOPE},
            case_settlement_reference={"origin_task_id": proof.origin_task_id, "origin_attempt_id": proof.origin_attempt_id,
                "run_item_execution_attempt_id": proof.run_item_execution_attempt_id, "admission_sha256": proof.asset_sha256})

    def _validate_envelope(self, cur, row, source, capabilities, key):
        raw = row["input_payload"]
        _require(isinstance(raw, dict) and set(raw) == {ADMISSION_KEY}, "Controlled settlement admission payload changed")
        proof = capabilities.require_case_settlement_format(raw[ADMISSION_KEY])
        origin, _, _ = self._originals._lock_identity_rows(cur, source.owner)
        command = self._command(proof, origin, source.owner.binding)
        content_hash = self._tasks._input_hash("case_settle", {"input": raw,
            "runtime_binding_fingerprint": source.owner.binding.fingerprint})
        _require(row["id"] == proof.task_id and row["operation"] == "case_settle"
            and row["runtime_binding"] == source.owner.binding.model_dump(mode="json")
            and all(row[name] == getattr(command, name) for name in (*ENVELOPE, "reference_id", "parent_task_id", "priority"))
            and row["input_hash"] == content_hash and row["idempotency_key"] == key and row["user_message_id"] is None,
            "Controlled settlement admission envelope changed")
        cur.execute(f"SELECT stream,payload FROM {self._settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("agent_turn:" + proof.task_id,))
        outbox = cur.fetchone()
        _require(outbox is not None and outbox["stream"] == self._settings.orchestration.agent_task_v2_stream
            and capabilities.require_command_format(outbox["payload"]) == command,
            "Controlled settlement admission has no matching original outbox")
        event = admission_event(proof, command)
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} WHERE id=%s", (event.id,))
        audit = cur.fetchone()
        _require(audit is not None, "Controlled settlement admission has no atomic audit")
        _require_event_intent(audit, event)
        return proof

    def _existing(self, cur, row, source, asset, capabilities, key):
        proof = self._validate_envelope(cur, row, source, capabilities, key)
        if row["status"] == "completed":
            from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

            ControlledCaseRecoverySettlement(self)._ack_source(cur, source, asset, row, proof, capabilities)
        else:
            current, _ = self._proof(cur, source, asset, proof.admitted_at)
            _require(proof == current, "Controlled settlement admission original facts changed")
        return AgentTaskRecord.model_validate(row), True

    def _ensure(self, command, capabilities):
        tasks, settings = self._tasks, self._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            source, asset = self._originals._expired_assessment_source(cur, command, capabilities)
            case = source.locked
            task_id = settlement_recovery_task_id(source.owner.task_id, source.owner.attempt_id)
            key = tasks._idempotency_key("case_settle", task_id)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s OR (session_id=%s AND idempotency_key=%s) FOR UPDATE",
                (task_id, command.session_id, key))
            existing = cur.fetchall()
            if existing:
                _require(len(existing) == 1, "Controlled settlement admission has conflicting task identities")
                return self._existing(cur, existing[0], source, asset, capabilities, key)
            if not capabilities.controlled_case_settlement_admission_available:
                raise TaskDeferred("framework_case_settlement_admission_unavailable")
            # The complete original/run/task lock set is held. Re-observe the
            # Session lease on the DB clock rather than reuse a pre-wait check.
            session = tasks._lock_session(cur, command.session_id)
            run = case.store._read_run(cur, case.item.run_id)
            _require(not session["lease_active"] and session["status"] not in {"running", "waiting_approval", "cancelled"}
                and run.status not in {"completed", "cancelled"}
                and case.item.status == case.attempt.status == "running" and case.item.result_id is None,
                "Controlled settlement admission has no inactive uncompleted formal source")
            cur.execute("SELECT clock_timestamp() AS now")
            proof, origin = self._proof(cur, source, asset, cur.fetchone()["now"])
            capabilities.require_case_settlement_format(proof.model_dump(mode="json"))
            successor = self._command(proof, origin, source.owner.binding)
            payload = {ADMISSION_KEY: proof.model_dump(mode="json")}
            content_hash = tasks._input_hash("case_settle", {"input": payload,
                "runtime_binding_fingerprint": source.owner.binding.fingerprint})
            cur.execute(f"""INSERT INTO {tasks._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 priority,idempotency_key,input_hash,input_payload,reference_id,parent_task_id,root_trace_id,runtime_binding)
                VALUES (%s,%s,'case_settle',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) RETURNING *""",
                (successor.task_id, successor.session_id, successor.turn_id, successor.project_id,
                 successor.request_id, successor.trace_id, successor.run_id, successor.run_item_id, successor.priority,
                 key, content_hash, json.dumps(payload, ensure_ascii=False), successor.reference_id,
                 successor.parent_task_id, successor.root_trace_id, successor.runtime_binding.model_dump_json()))
            row = cur.fetchone()
            cur.execute(f"INSERT INTO {settings.database.postgres_task_outbox_table} (event_key,stream,payload) VALUES (%s,%s,%s::jsonb)",
                ("agent_turn:" + successor.task_id, settings.orchestration.agent_task_v2_stream, successor.model_dump_json()))
            _require(tasks._sessions.append_event_in_transaction(cur, successor.session_id, admission_event(proof, successor)),
                "Controlled settlement admission audit exists without its atomic original")
            return AgentTaskRecord.model_validate(row), False

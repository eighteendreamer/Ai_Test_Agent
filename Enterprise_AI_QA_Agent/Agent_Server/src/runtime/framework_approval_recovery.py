"""Durable reconciliation and safe successors for failed native approval tasks.

No upstream counterpart (checked: inbox/outbox, runtime owner, native decision
journal, integration plan §5.2/5.3): framework persistence cannot decide whether
an application tool's external effect is known. Never reset a terminal task.
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.postgres_runtime_owner import (
    assert_runtime_owner, configure_runtime_owner_transaction, lock_owner_row,
)
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_approval import approval_value_hash


logger = logging.getLogger(__name__)
APPROVAL_EXECUTIONS = frozenset({"approval_resume", "approval_recover"})
RECOVERY_TASKS = APPROVAL_EXECUTIONS | {"framework_reconcile"}


def _successor_task_id(operation, parent_id, attempt_id):
    return str(uuid5(NAMESPACE_URL, f"framework:{operation}:{parent_id}:{attempt_id}"))


def require_recovery_origin(cur, tasks, session, binding, origin):
    from src.runtime.framework_approval_finalization import prepare_approval_finalization

    if prepare_approval_finalization(cur, tasks._settings, session, binding, origin) is not None:
        return
    pending = (session["metadata"] or {}).get("pending_turn") or {}
    control = (session["metadata"] or {}).get("control") or {}
    if (origin["operation"] not in APPROVAL_EXECUTIONS or origin["status"] not in {"failed", "recovery_required"}
        or not origin["attempt_id"] or origin["interrupt_requested"]
        or origin["runtime_binding"] != binding.model_dump(mode="json")
        or origin["session_id"] != session["id"] or origin["project_id"] != session["project_id"]
        or origin["turn_id"] != binding.turn_id
        or pending.get("turn_id") != binding.turn_id
        or (pending.get("context_bundle") or {}).get("runtime_binding") != binding.model_dump(mode="json")
        or control.get("last_interrupt_binding_id") == binding.binding_id):
        raise FrameworkApprovalRecoveryRequired("Origin no longer belongs to a recoverable framework Turn")
    cur.execute(f"SELECT id FROM {tasks._tasks} WHERE session_id=%s AND turn_id=%s AND interrupt_requested LIMIT 1",
        (binding.session_id, binding.turn_id))
    if cur.fetchone() is not None:
        raise FrameworkApprovalRecoveryRequired("Explicitly interrupted Turn cannot resume automatically")


def is_expired_reconciliation(row):
    """Only a lost running attempt permits another deterministic reconciliation.

    A safety rejection or a returned database error is not a retry instruction.
    The original task and attempt stay terminal even when this returns True.
    """
    return (row["operation"] == "framework_reconcile" and row["status"] == "recovery_required"
        and row["error_code"] == "execution_owner_expired" and bool(row["attempt_id"])
        and not row["interrupt_requested"])


def reconciliation_origin(cur, tasks, session, binding, row):
    cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (row["reference_id"],))
    origin = cur.fetchone()
    if origin is None:
        raise FrameworkApprovalRecoveryRequired("Reconciliation origin is missing")
    require_recovery_origin(cur, tasks, session, binding, origin)
    if (row["input_payload"].get("origin_task_id") != origin["id"]
        or row["input_payload"].get("origin_attempt_id") != origin["attempt_id"]
        or row["session_id"] != origin["session_id"] or row["turn_id"] != origin["turn_id"]
        or row["project_id"] != origin["project_id"] or row["runtime_binding"] != origin["runtime_binding"]):
        raise FrameworkApprovalRecoveryRequired("Reconciliation origin attempt or binding changed")
    predecessor = origin
    if row["parent_task_id"] != origin["id"]:
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (row["parent_task_id"],))
        predecessor = cur.fetchone()
        payload = row["input_payload"]
        if (predecessor is None or not is_expired_reconciliation(predecessor)
            or predecessor["reference_id"] != origin["id"]
            or predecessor["input_payload"].get("origin_task_id") != origin["id"]
            or predecessor["input_payload"].get("origin_attempt_id") != origin["attempt_id"]
            or predecessor["session_id"] != row["session_id"] or predecessor["turn_id"] != row["turn_id"]
            or predecessor["project_id"] != row["project_id"] or predecessor["runtime_binding"] != row["runtime_binding"]
            or payload.get("predecessor_task_id") != predecessor["id"]
            or payload.get("predecessor_attempt_id") != predecessor["attempt_id"]):
            raise FrameworkApprovalRecoveryRequired("Reconciliation predecessor has no matching expired attempt")
    return origin, predecessor


def require_reconciliation_claim(cur, tasks, session, binding, row, owner):
    origin, predecessor = reconciliation_origin(cur, tasks, session, binding, row)
    if (owner["task_id"] not in {predecessor["id"], row["id"]}
        or (owner["task_id"] == predecessor["id"] and owner["attempt_id"] != predecessor["attempt_id"])):
        raise FrameworkApprovalRecoveryRequired("Reconciliation coordinator attempt changed")
    return origin


def require_successor_proof(cur, settings, row):
    """A recover command is valid only as the recorded child of a completed reconciliation."""
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE",
        (row["parent_task_id"],))
    parent = cur.fetchone()
    payload = row["input_payload"]
    result = (parent or {}).get("result_payload") or {}
    proof = payload.get("recovery_proof")
    if (parent is None or parent["operation"] != "framework_reconcile" or parent["status"] != "completed"
        or parent["result_status"] != "reconciled" or parent["runtime_binding"] != row["runtime_binding"]
        or parent["session_id"] != row["session_id"] or parent["turn_id"] != row["turn_id"]
        or payload.get("reconciliation_task_id") != parent["id"] or result.get("successor_task_id") != row["id"]
        or not isinstance(proof, dict) or result.get("proof") != proof
        or proof.get("origin_task_id") != parent["reference_id"]
        or proof.get("origin_task_id") != payload.get("origin_task_id")
        or proof.get("origin_attempt_id") != payload.get("origin_attempt_id")
        or proof.get("decision_id") != payload.get("decision_id")):
        raise FrameworkApprovalRecoveryRequired("Recovery successor has no matching durable reconciliation proof")
    if proof.get("scope") == "application_finalization":
        from src.runtime.framework_approval_finalization import validate_finalization_link

        validate_finalization_link(cur, settings, row)
    else:
        if proof.get("schema_version") != 1 or proof.get("scope") is not None:
            raise FrameworkApprovalRecoveryRequired("Native recovery proof has an unsupported scope or format")
        require_native_recovery_scope(cur, settings, RuntimeBinding.model_validate(row["runtime_binding"]))
    return proof


def require_native_recovery_scope(cur, settings, binding):
    """A native-decision proof cannot authorize application-result recovery.

    No upstream counterpart (checked: message recovery finalizing proof,
    approval reconciliation, v4 §9.4/§10.2): after the result boundary, only
    a separately admitted finalizer-only continuation may consume the asset.
    Recheck both before publication and before claiming an older queued proof.
    """
    from src.runtime.framework_message_stages import message_stage_table

    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (binding.binding_id,))
    row = cur.fetchone()
    manifest = ((row["record"] if row is not None else {}).get("source_manifest") or {})
    if "runtime_result_asset" in manifest or "application_projection_commit" in manifest:
        raise FrameworkApprovalRecoveryRequired(
            "Original application result requires finalization-only proof, not native continuation")


class FrameworkApprovalRecovery:
    def __init__(self, tasks, continuation=None):
        self._tasks, self._settings, self._continuation = tasks, tasks._settings, continuation

    def _publish(self, cur, origin, *, operation, reference_id, parent_id, input_payload):
        tasks = self._tasks
        task_id = _successor_task_id(operation, parent_id, origin["attempt_id"])
        command = AgentTaskCommandV2(task_id=task_id, operation=operation, session_id=origin["session_id"],
            turn_id=origin["turn_id"], project_id=origin["project_id"], request_id=origin["request_id"],
            trace_id=origin["trace_id"], run_id=origin["run_id"], run_item_id=origin["run_item_id"],
            reference_id=reference_id, parent_task_id=parent_id, root_trace_id=origin["root_trace_id"],
            runtime_binding=RuntimeBinding.model_validate(origin["runtime_binding"]))
        cur.execute(f"""INSERT INTO {tasks._tasks}
            (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
             idempotency_key,input_hash,input_payload,reference_id,parent_task_id,root_trace_id,runtime_binding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) RETURNING *""",
            (task_id, command.session_id, operation, command.turn_id, command.project_id, command.request_id,
                command.trace_id, command.run_id, command.run_item_id, tasks._idempotency_key(operation, task_id),
                approval_value_hash(input_payload), json.dumps(input_payload, ensure_ascii=False), reference_id,
                parent_id, command.root_trace_id, command.runtime_binding.model_dump_json()))
        row = cur.fetchone()
        cur.execute(f"INSERT INTO {self._settings.database.postgres_task_outbox_table} (event_key,stream,payload) "
            "VALUES (%s,%s,%s::jsonb)", (f"agent_turn:{task_id}", self._settings.orchestration.agent_task_v2_stream,
                command.model_dump_json()))
        tasks._event(cur, row, f"framework.{operation}_queued", {"origin_task_id": origin["id"],
            "origin_attempt_id": origin["attempt_id"], "binding_id": command.runtime_binding.binding_id})
        return tasks._record(row)

    def ensure(self, task_id):
        tasks = self._tasks
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            cur.execute(f"SELECT session_id FROM {tasks._tasks} WHERE id=%s", (task_id,))
            identity = cur.fetchone()
            if identity is None:
                raise KeyError(task_id)
            session = tasks._lock_session(cur, identity["session_id"])
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s", (task_id,))
            predecessor = cur.fetchone()
            if (predecessor["runtime_binding"] is None or predecessor["operation"] not in RECOVERY_TASKS
                or predecessor["status"] not in {"failed", "recovery_required"}
                or (predecessor["operation"] == "framework_reconcile" and not is_expired_reconciliation(predecessor))):
                return None
            binding = RuntimeBinding.model_validate(predecessor["runtime_binding"])
            owner = lock_owner_row(cur, self._settings, binding)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE operation='framework_reconcile' AND parent_task_id=%s",
                (task_id,))
            existing = cur.fetchone()
            if existing is not None:
                return tasks._record(existing)
            try:
                if predecessor["operation"] == "framework_reconcile":
                    origin, _ = reconciliation_origin(cur, tasks, session, binding, predecessor)
                else:
                    origin = predecessor
                    require_recovery_origin(cur, tasks, session, binding, origin)
            except FrameworkApprovalRecoveryRequired as exc:
                logger.info("framework_recovery_not_admitted", extra={"origin_task_id": task_id,
                    "binding_id": binding.binding_id, "reason": str(exc)})
                return None
            if (session["lease_active"] or owner["task_id"] != task_id
                or owner["attempt_id"] != predecessor["attempt_id"]):
                logger.info("framework_recovery_owner_unavailable", extra={"task_id": task_id,
                    "binding_id": binding.binding_id, "lease_active": session["lease_active"]})
                return None
            payload = {"origin_task_id": origin["id"], "origin_attempt_id": origin["attempt_id"]}
            if predecessor["operation"] == "framework_reconcile":
                payload.update(predecessor_task_id=task_id, predecessor_attempt_id=predecessor["attempt_id"])
            created = self._publish(cur, origin, operation="framework_reconcile", reference_id=origin["id"],
                parent_id=task_id, input_payload=payload)
        logger.info("framework_reconciliation_admitted", extra={"origin_task_id": origin["id"], "task_id": created.id,
            "predecessor_task_id": task_id, "binding_id": binding.binding_id})
        return created

    async def apply(self, task):
        try:
            result = await asyncio.to_thread(self._apply, task)
        except Exception as exc:
            logger.warning("framework_reconciliation_rejected", extra={"task_id": task.id,
                "origin_task_id": task.reference_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_reconciliation_succeeded", extra={"task_id": task.id, "successor_task_id": result.id})
        return result

    def _apply(self, task):
        if self._continuation is None or task.runtime_owner is None:
            raise FrameworkApprovalRecoveryRequired("Reconciliation requires its owned native continuation service")
        tasks, owner = self._tasks, task.runtime_owner
        with postgres_connect(self._settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            assert_runtime_owner(cur, self._settings, owner)
            session = tasks._lock_session(cur, task.session_id)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (task.id,))
            row = cur.fetchone()
            if (row is None or row["operation"] != "framework_reconcile" or row["status"] != "running"
                or row["attempt_id"] != owner.attempt_id or row["lease_token"] != owner.owner_token
                or row["runtime_binding"] != owner.binding.model_dump(mode="json")):
                raise FrameworkApprovalRecoveryRequired("Reconciliation differs from its owned task")
            origin = require_reconciliation_claim(cur, tasks, session, owner.binding, row,
                {"task_id": task.id, "attempt_id": owner.attempt_id})
            from src.runtime.framework_approval_finalization import prepare_approval_finalization, _save
            from src.schemas.framework_message_stage import FrameworkApprovalFinalizationLink, FrameworkApprovalFinalizationProof

            prepared = prepare_approval_finalization(cur, self._settings, session, owner.binding, origin)
            if prepared is None:
                require_native_recovery_scope(cur, self._settings, owner.binding)
                plan, consumed = self._continuation.inspect_decision_in_transaction(cur, owner.binding, origin)
                cur.execute(f"SELECT * FROM {self._settings.database.postgres_tool_job_table} "
                    "WHERE session_id=%s AND turn_id=%s ORDER BY id FOR UPDATE", (task.session_id, task.turn_id))
                jobs = cur.fetchall()
                from src.runtime.framework_approval_continuation import TERMINAL_JOBS

                for job in jobs:
                    if (job["metadata"].get("framework_binding_id") != owner.binding.binding_id
                        or (job["status"] not in TERMINAL_JOBS
                            and not (job["status"] == "waiting_approval" and job["started_at"] is None))):
                        raise FrameworkApprovalRecoveryRequired("Turn has unknown tool effects; reconciliation cannot dispatch")
                proof = {"schema_version": 1, "origin_task_id": origin["id"], "origin_attempt_id": origin["attempt_id"],
                    "decision_id": plan.receipt.decision_id, "decision_hash": plan.receipt.decision_hash,
                    "native_consumed": consumed, "tool_job_id": plan.job.id, "tool_job_status": plan.job.status.value,
                    "tool_output_hash": approval_value_hash(plan.job.output_payload)}
            else:
                stage, asset, budget, predecessor_epoch = prepared
                cur.execute("SELECT clock_timestamp() AS now")
                now = cur.fetchone()["now"]
                successor_id = _successor_task_id("approval_recover", task.id, origin["attempt_id"])
                link = FrameworkApprovalFinalizationLink(task_id=successor_id, predecessor_task_id=origin["id"],
                    predecessor_attempt_id=origin["attempt_id"], predecessor_owner_epoch=predecessor_epoch,
                    budget_sha256=approval_value_hash(budget.model_dump(mode="json")), admitted_at=now,
                    source_sha256=asset.source_sha256, execution_sha256=asset.execution_sha256,
                    model_calls_sha256=asset.model_calls_sha256, result_reference=asset.runtime_result.snapshot.id,
                    result_sha256=asset.asset_sha256)
                proof = FrameworkApprovalFinalizationProof(origin_task_id=origin["id"], origin_attempt_id=origin["attempt_id"],
                    decision_id=origin["input_payload"]["decision_id"],
                    finalization=link.model_dump(mode="json", exclude={"owner_attempt_id", "owner_epoch"})).model_dump(mode="json")
                _save(cur, self._settings, stage.model_copy(update={"schema_version": max(18, stage.schema_version),
                    "approval_finalizations": [*stage.approval_finalizations, link], "updated_at": now}))
            successor = self._publish(cur, origin, operation="approval_recover", reference_id=origin["reference_id"],
                parent_id=task.id, input_payload={**origin["input_payload"], "origin_task_id": origin["id"],
                    "origin_attempt_id": origin["attempt_id"], "reconciliation_task_id": task.id, "recovery_proof": proof})
            cur.execute(f"UPDATE {tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s",
                (json.dumps({"successor_task_id": successor.id, "proof": proof}), task.id))
            tasks._update_status(cur, row, "completed", "reconciled", None)
            tasks._clear_lease(cur, row)
            metadata = dict(session["metadata"])
            for key in ("turn_lease_turn_id", "turn_lease_owner", "turn_lease_token", "turn_lease_expires_at"):
                metadata.pop(key, None)
            metadata["control"] = {**(metadata.get("control") or {}), "control_state": "waiting_approval",
                "is_interrupted": False, "is_resumable": False}
            cur.execute(f"UPDATE {tasks._session_table} SET status='waiting_approval',metadata=%s::jsonb, "
                "state_revision=state_revision+1,updated_at=clock_timestamp() WHERE id=%s",
                (json.dumps(metadata, ensure_ascii=False), task.session_id))
            tasks._event(cur, row, "framework.recovery_succeeded", {"successor_task_id": successor.id,
                "origin_task_id": origin["id"], "binding_id": owner.binding.binding_id})
            return successor

"""Compose the original formal wait kernels; no new scheduler or ACK path.

No upstream counterpart (checked: RunStore approval wait, formal settlement,
native batch projection, Worker activity, v4 §7/§12.1): native interrupts do
not park business owners. Retain a versioned, non-secret original in the
producer Task, not a completed result or a mutable Session/ToolJob row hash.
"""
import asyncio
from copy import deepcopy
import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import current_framework_owner, current_graph_event_sink
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_tool_activity import read_tool_activity
from src.runtime.framework_worker_activity import read_worker_activity
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction, lock_owner_row
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.controlled_case_approval_wait import ControlledCaseApprovalWaitReceipt
from src.schemas.framework_approval import NativeToolInterrupt, approval_value_hash
from src.schemas.session import ExecutionEvent
from src.schemas.tool_job import ToolOutputObservation


logger = logging.getLogger(__name__)
WAIT_KEY = "controlled_case_approval_wait"


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def _event_hash(row):
    return approval_value_hash(ExecutionEvent.model_validate(row).model_dump(mode="json"))


class ControlledCaseApprovalWaits:
    def __init__(self, tasks, *, projection):
        if projection._settings != tasks._settings or projection._sessions is not tasks._sessions:
            raise ValueError("Controlled wait requires the original shared projection transaction")
        self._tasks, self._projection = tasks, projection

    async def park(self, owner, *, proposals):
        scope = {"task_id": getattr(owner, "task_id", None), "operation": "approval_wait",
            "binding_id": getattr(getattr(owner, "binding", None), "binding_id", None)}
        try:
            _require(owner is not None and owner.controlled_case_lease is not None
                and owner == current_framework_owner() and current_graph_event_sink() is None,
                "Controlled wait requires its captured dual owner after graph event drain")
            owner, proposals = deepcopy((owner, proposals))
            _require(bool(proposals), "Controlled wait requires a nonempty complete native batch")
            receipt = await asyncio.to_thread(self._park, owner, proposals)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_approval_wait_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "approval_wait_write_receipt_unknown",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_approval_wait_committed", extra={**scope,
            "wait_sha256": receipt.receipt_sha256, "approval_count": len(receipt.batch), "automatic_replay": False})
        return receipt

    def _task_original(self, cur, owner):
        tasks = self._tasks
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
        task = cur.fetchone()
        cur.execute(f"SELECT * FROM {tasks._attempts} WHERE id=%s FOR UPDATE", (owner.attempt_id,))
        attempt = cur.fetchone()
        _require(task is not None and attempt is not None and task["operation"] == "case_execute"
            and task["status"] == attempt["status"] == "running" and not task["interrupt_requested"]
            and task["result_payload"] is None and task["result_status"] is None
            and (task["attempt_id"], task["worker_id"], task["lease_token"])
                == (owner.attempt_id, owner.worker_id, owner.owner_token)
            and (attempt["task_id"], attempt["worker_id"], attempt["lease_token"], attempt["attempt_no"])
                == (owner.task_id, owner.worker_id, owner.owner_token, task["attempt_no"])
            and attempt["finished_at"] is None,
            "Controlled wait requires its original running task/attempt without a prior result")
        return task, attempt

    def _settled_materials(self, cur, owner, case, task, projected):
        tasks, settings, binding = self._tasks, self._tasks._settings, owner.binding
        ledger = PostgresFrameworkModelCalls(settings)
        cur.execute(f"SELECT binding_id FROM {ledger.budgets_table} WHERE binding_id=%s FOR SHARE", (binding.binding_id,))
        cur.execute(f"SELECT attempt_id FROM {ledger.calls_table} WHERE binding_id=%s ORDER BY attempt_id FOR SHARE",
            (binding.binding_id,))
        snapshot = ledger._snapshot(cur, binding)
        expected = tasks._input_hash("case_execute", {"input": task["input_payload"],
            "runtime_binding_fingerprint": binding.fingerprint, "model_budget": snapshot.policy.model_dump(mode="json")})
        _require(expected == task["input_hash"] == case.checkpoint.submission_sha256
            and snapshot.calls and all(call.phase == "completed" and call.settled_at is not None for call in snapshot.calls),
            "Controlled wait has changed input/budget or unsettled model calls")
        # Lock every original Turn job, not only the candidates: a native wait
        # cannot turn an unrelated still-running/unknown effect into idle time.
        cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE session_id=%s AND turn_id=%s "
            "ORDER BY id FOR UPDATE", (binding.session_id, binding.turn_id))
        batch_ids = {item.job.id for item in projected}
        for job in cur.fetchall():
            _require(job["metadata"].get("framework_binding_id") == binding.binding_id,
                "Controlled wait has a foreign business ToolJob")
            journal = read_tool_activity(job)
            if job["id"] in batch_ids:
                _require(job["status"] == "waiting_approval" and job["started_at"] is None
                    and job["completed_at"] is None and journal is not None and journal.origin_observed,
                    "Controlled wait candidate ToolJob has already started or lost its original wait")
            else:
                returned = job["metadata"].get("tool_output_observation")
                if returned is not None:
                    # Native return hashes describe the raw observation. The
                    # original settlement may enrich the final output (e.g.
                    # recover_observed_output adds output_delivery). Do not
                    # compare that final projection to the raw response hash.
                    ToolOutputObservation.model_validate(returned)
                _require(job["status"] in {"completed", "partial", "failed", "denied"}
                    and (job["started_at"] is None or job["status"] in {"completed", "partial"}
                        or (journal is not None and journal.events[-1].output_observed)),
                    "Controlled wait has unsettled business ToolJobs")
            if journal is not None:
                # No as_of is supplied: an unclosed external tail must reject.
                journal.activity_intervals()
        for item in projected:
            _require(item.approval.status.value == "pending" and item.approval.resolved_at is None
                and self._projection._journal.get_receipt_in_transaction(cur, item.approval.id) is None,
                "Controlled wait cannot overwrite a prior human decision or delivery")
        return snapshot

    def _worker_fact(self, cur, owner, task, attempt, event_type):
        cur.execute(f"SELECT * FROM {self._tasks._settings.database.postgres_event_table} "
            "WHERE session_id=%s AND payload->>'task_id'=%s "
            "AND type IN ('agent_task.claimed','agent_task.running','agent_task.completed') ORDER BY event_sequence FOR SHARE",
            (owner.binding.session_id, owner.task_id))
        events = cur.fetchall()
        journal = read_worker_activity(task, [attempt], events)[owner.attempt_id]
        matches = [e for e in events if e["type"] == event_type]
        _require(journal is not None and journal.origin_observed and len(matches) == 1,
            "Controlled wait has no unique original Worker boundary")
        if event_type == "agent_task.claimed":
            _require(matches[0]["payload"].get("owner_epoch") == owner.owner_epoch,
                "Controlled wait original Worker claim epoch differs")
        else:
            _require(journal.events[-1].execution_returned
                and journal.events[-1].result_status == "waiting_approval",
                "Controlled wait has no confirmed Worker return")
            journal.activity_intervals()
        return _event_hash(matches[0])

    def _park(self, owner, proposals):
        tasks, settings, binding = self._tasks, self._tasks._settings, owner.binding
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            session = tasks._lock_session(cur, binding.session_id)
            case = assert_runtime_owner(cur, settings, owner)
            _require(case is not None and session["status"] == "running"
                and not session["metadata"].get("pending_turn")
                and (session["metadata"].get("control") or {}).get("control_state") not in {
                    "interrupt_requested", "interrupted", "cancelled"},
                "Controlled wait conflicts with the active Session/control state")
            task, attempt = self._task_original(cur, owner)
            case.store._lock_run(cur, case.item.run_id)
            claim_hash = self._worker_fact(cur, owner, task, attempt, "agent_task.claimed")
            projected = self._projection.project_many_in_transaction(cur, owner, proposals)
            snapshot = self._settled_materials(cur, owner, case, task, projected)
            # All pending originals/Job/event/ledger locks precede a fresh dual
            # fence. Held row locks forbid another owner changing these facts.
            case = assert_runtime_owner(cur, settings, owner)
            saved_owner = lock_owner_row(cur, settings, binding)
            deadlines = (session["metadata"]["turn_lease_expires_at"], saved_owner["expires_at"], case.item.lease_expires_at)
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            first = projected[0]  # Existing display fields only; receipt retains ALL siblings.
            case.store._mark_waiting_approval_in_transaction(cur, case.item.id, case.lease.lease_token,
                first.approval.id, first.job.id, now)
            tasks._update_status(cur, task, "completed", "waiting_approval", None, execution_returned=True)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s", (owner.task_id,))
            returned_task = cur.fetchone()
            cur.execute(f"SELECT * FROM {tasks._attempts} WHERE id=%s", (owner.attempt_id,))
            return_hash = self._worker_fact(cur, owner, returned_task, cur.fetchone(), "agent_task.completed")
            values = dict(schema_version=1, scope="controlled_case_atomic_approval_wait_only",
                binding_fingerprint=binding.fingerprint, task_id=owner.task_id, attempt_id=owner.attempt_id,
                owner_epoch=owner.owner_epoch, run_id=case.item.run_id, run_item_id=case.item.id,
                run_item_execution_attempt_id=case.attempt.id, input_sha256=case.checkpoint.input_sha256,
                submission_sha256=case.checkpoint.submission_sha256,
                admission_sha256=approval_value_hash(case.checkpoint.model_dump(mode="json")),
                model_ledger_sha256=approval_value_hash(snapshot.model_dump(mode="json")),
                task_claim_sha256=claim_hash, task_return_sha256=return_hash,
                parked_at=now, batch=[dict(approval_id=item.approval.id, tool_job_id=item.job.id,
                    interrupt_id=item.link.interrupt_id, link_sha256=approval_value_hash(item.link.model_dump(mode="json")),
                    native_sha256=approval_value_hash(item.link.model_dump(mode="json", include=set(NativeToolInterrupt.model_fields))))
                    for item in sorted(projected, key=lambda item: item.approval.id)])
            values = TypeAdapter(Any).dump_python(values, mode="json")
            receipt = ControlledCaseApprovalWaitReceipt(**values, receipt_sha256=approval_value_hash(values))
            cur.execute(f"UPDATE {tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s AND result_payload IS NULL",
                (json.dumps({WAIT_KEY: receipt.model_dump(mode="json")}), owner.task_id))
            _require(cur.rowcount == 1, "Controlled wait original task receipt already exists")
            pending = dict(turn_id=binding.turn_id, stage="waiting_approval", source="test_run_agent_execution",
                context_bundle={"runtime_binding": binding.model_dump(mode="json")},
                pending_approval_ids=[item.approval_id for item in receipt.batch],
                controlled_case_wait_sha256=receipt.receipt_sha256)
            control = {**(session["metadata"].get("control") or {}), "control_state": "waiting_approval"}
            cur.execute(f"UPDATE {tasks._session_table} SET status='waiting_approval', "
                "metadata=metadata || %s::jsonb, state_revision=state_revision+1, updated_at=clock_timestamp() WHERE id=%s",
                (json.dumps({"pending_turn": pending, "control": control}), binding.session_id))
            tasks._clear_lease(cur, task)
            event = ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-approval-wait:" + owner.task_id)),
                session_id=binding.session_id, type="test_run.case_approval_waiting", timestamp=now,
                payload={"turn_id": binding.turn_id, "task_id": owner.task_id, "attempt_id": owner.attempt_id,
                    "owner_epoch": owner.owner_epoch, "run_id": case.item.run_id, "run_item_id": case.item.id,
                    "run_item_execution_attempt_id": case.attempt.id, "wait_sha256": receipt.receipt_sha256,
                    "approval_ids": [item.approval_id for item in receipt.batch]})
            _require(tasks._sessions.append_event_in_transaction(cur, binding.session_id, event),
                "Controlled wait audit exists without its original task receipt")
            # Authority rows have deliberately transitioned/cleared, so the
            # normal running-owner assertion no longer applies. Check the same
            # locked pre-transition deadlines on a NEW DB clock after ALL writes.
            cur.execute("SELECT %s::timestamptz > clock_timestamp() AND %s::timestamptz > clock_timestamp() "
                "AND %s::timestamptz > clock_timestamp() AS active", deadlines)
            if cur.fetchone()["active"] is not True:
                raise ContinuationLeaseLostError("Controlled wait crossed its original locked lease deadline")
            return receipt

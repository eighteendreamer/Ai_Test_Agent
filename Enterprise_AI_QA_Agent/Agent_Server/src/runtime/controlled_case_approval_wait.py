"""Original atomic wait and its read-only historical delivery ACK.

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
from src.runtime.framework_application_projection import _require_event_intent
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction, lock_owner_row
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.controlled_case_approval_wait import ControlledCaseApprovalWaitReceipt, ControlledCaseApprovalStateWaitReceipt
from src.schemas.framework_approval import NativeToolInterrupt, approval_value_hash
from src.schemas.framework_model_call import FrameworkModelBudgetSnapshot
from src.schemas.session import ExecutionEvent
from src.schemas.tool_job import ToolOutputObservation


logger = logging.getLogger(__name__)
WAIT_KEY = "controlled_case_approval_wait"


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def _event_hash(row):
    return approval_value_hash(ExecutionEvent.model_validate(row).model_dump(mode="json"))


def approval_wait_event(receipt, binding):
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-approval-wait:" + receipt.task_id)),
        session_id=binding.session_id, type="test_run.case_approval_waiting", timestamp=receipt.parked_at,
        payload={"turn_id": binding.turn_id, "task_id": receipt.task_id, "attempt_id": receipt.attempt_id,
            "owner_epoch": receipt.owner_epoch, "run_id": receipt.run_id, "run_item_id": receipt.run_item_id,
            "run_item_execution_attempt_id": receipt.run_item_execution_attempt_id,
            "wait_sha256": receipt.receipt_sha256, "approval_ids": [item.approval_id for item in receipt.batch]})


class ControlledCaseApprovalWaits:
    def __init__(self, tasks, *, projection):
        if projection._settings != tasks._settings or projection._sessions is not tasks._sessions:
            raise ValueError("Controlled wait requires the original shared projection transaction")
        self._tasks, self._projection = tasks, projection

    async def acknowledge_delivery(self, delivery, *, capabilities):
        scope = {"operation": "historical_approval_wait_ack"}
        try:
            command = deepcopy(capabilities.require_command_format(delivery))
            scope["task_id"] = command.task_id
            capabilities.require_runtime_artifact(command.runtime_binding)
            receipt = await asyncio.to_thread(self._delivery_original, command, capabilities)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_approval_wait_ack_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "approval_wait_originals_unsettled",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_approval_wait_acknowledged", extra={**scope,
            "wait_sha256": receipt.receipt_sha256, "approval_count": len(receipt.batch), "automatic_replay": False})
        return receipt

    def _delivery_original(self, command, capabilities):
        scope = {"task_id": command.task_id, "binding_id": command.runtime_binding.binding_id,
            "original_stage": "delivery_admission", "automatic_replay": False}
        try:
            return self._read_delivery_original(command, capabilities, scope)
        except Exception as exc:
            logger.warning("controlled_case_approval_wait_original_rejected", extra={**scope,
                "error_type": type(exc).__name__})
            raise

    async def read_state(self, delivery, *, capabilities):
        """Private historical material only, never a new execution owner."""
        command = deepcopy(capabilities.require_command_format(delivery))
        capabilities.require_runtime_artifact(command.runtime_binding)
        scope = {"task_id": command.task_id, "operation": "historical_approval_wait_state_read"}
        try:
            asset = await asyncio.to_thread(self._read_delivery_original, command, capabilities, scope, state_only=True)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_approval_wait_state_rejected", extra={**scope,
                "error_type": type(exc).__name__, "automatic_replay": False})
            raise
        logger.info("controlled_case_approval_wait_state_read", extra={**scope,
            "state_asset_sha256": asset.asset_sha256, "automatic_replay": False})
        return asset

    async def read_state_for_task(self, task_id, *, capabilities):
        """Load the exact original outbox, then use the same historical reader."""
        def wire():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT payload FROM {self._tasks._settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                    ("agent_turn:" + task_id,))
                row = cur.fetchone()
                _require(row is not None, "Controlled wait state has no original outbox")
                return row["payload"]

        return await self.read_state(await asyncio.to_thread(wire), capabilities=capabilities)

    def _read_delivery_original(self, command, capabilities, scope, *, state_only=False):
        with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._tasks._settings)
            return self.require_original_in_transaction(cur, command, capabilities, scope, state_only=state_only)

    def require_original_in_transaction(self, cur, command, capabilities, scope, *, state_only=False):
        """Historical facts only, reusable by a caller's separate admission.

        The caller must still establish current formal eligibility. Keeping the
        native/ledger/original reader on this connection avoids a check-then-
        write window; this reader alone never grants a decision or live owner.
        """
        from src.application.deep_agents.native_approval_batch import require_historical_tool_interrupts_original
        from src.runtime.controlled_case_execution import lock_case_delivery
        from src.runtime.framework_event_completion import original_checkpoint_source
        from src.schemas.controlled_case_task_input import parse_controlled_case_task_input

        tasks, settings, binding = self._tasks, self._tasks._settings, command.runtime_binding
        tasks._lock_session(cur, binding.session_id)  # Lock order only, NOT current Session authority.
        case, _ = lock_case_delivery(cur, tasks, command, capabilities)
        scope["original_stage"] = "producer_receipt"
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR SHARE", (command.task_id,))
        task = cur.fetchone()
        payload = task["result_payload"]
        _require(task["status"] == "completed" and task["result_status"] == "waiting_approval"
            and task["error_code"] is None and isinstance(payload, dict) and set(payload) == {WAIT_KEY},
            "Controlled wait original completed producer/receipt is missing")
        receipt = capabilities.require_case_approval_wait_format(payload[WAIT_KEY])
        scope["original_stage"] = "producer_admission_identity"
        source = original_checkpoint_source(cur, settings, binding, task, receipt.owner_epoch)
        cur.execute(f"SELECT * FROM {tasks._attempts} WHERE id=%s FOR SHARE", (source.attempt_id,))
        attempt = cur.fetchone()
        _require(attempt is not None and attempt["status"] == "completed" and attempt["error_code"] is None
            and attempt["finished_at"] is not None
            and (attempt["task_id"], attempt["worker_id"], attempt["lease_token"], attempt["attempt_no"])
                == (task["id"], task["worker_id"], task["lease_token"], task["attempt_no"])
            and (receipt.binding_fingerprint, receipt.task_id, receipt.attempt_id, receipt.run_id,
                receipt.run_item_id, receipt.run_item_execution_attempt_id, receipt.input_sha256, receipt.submission_sha256)
                == (binding.fingerprint, task["id"], source.attempt_id, case.item.run_id,
                    case.item.id, case.attempt.id, case.checkpoint.input_sha256, case.checkpoint.submission_sha256)
            and receipt.admission_sha256 == approval_value_hash(case.checkpoint.model_dump(mode="json")),
            "Controlled wait original producer/admission identity changed")
        scope["original_stage"] = "worker_claim_return"
        _require(receipt.task_claim_sha256 == self._worker_fact(cur, source, task, attempt, "agent_task.claimed")
            and receipt.task_return_sha256 == self._worker_fact(cur, source, task, attempt, "agent_task.completed"),
            "Controlled wait original Worker claim/return changed")
        scope["original_stage"] = "atomic_wait_audit"
        event = approval_wait_event(receipt, binding)
        cur.execute(f"SELECT * FROM {settings.database.postgres_event_table} WHERE id=%s FOR SHARE", (event.id,))
        _require_event_intent(cur.fetchone(), event)
        scope["original_stage"] = "historical_model_ledger"
        self._historical_ledger(cur, binding, task, receipt, capabilities)
        scope["original_stage"] = "complete_batch_links"
        natives = self._batch_original(cur, binding, receipt, capabilities, project_id=command.project_id)
        scope["original_stage"] = "historical_native_interrupts"
        original = parse_controlled_case_task_input(task["input_payload"]["case_execution_input"])
        snapshots = require_historical_tool_interrupts_original(cur, settings, source, original.graph_input, natives)
        asset = None
        if receipt.schema_version == 2 or state_only:
            from src.runtime.controlled_case_approval_state import require_wait_state_in_transaction

            scope["original_stage"] = "retained_application_wait_state"
            asset = require_wait_state_in_transaction(cur, settings, case, task, receipt, original, snapshots, natives)
        _require(receipt.schema_version == 2 or case.attempt.checkpoint_version not in {7, 8},
            "Controlled wait state checkpoint has no matching original receipt")
        if state_only:
            return asset
        return receipt

    def _historical_ledger(self, cur, binding, task, receipt, capabilities):
        """Freeze at the DB parking boundary; later calls are not old ACK facts."""
        ledger = PostgresFrameworkModelCalls(self._tasks._settings)
        cur.execute(f"SELECT policy FROM {ledger.budgets_table} WHERE binding_id=%s FOR SHARE", (binding.binding_id,))
        budget = cur.fetchone()
        _require(budget is not None, "Controlled wait original model budget is missing")
        capabilities.require_model_ledger_format("budget", budget["policy"])
        cur.execute(f"SELECT attempt_id,record FROM {ledger.calls_table} WHERE binding_id=%s "
            "AND (record->>'created_at')::timestamptz <= %s ORDER BY record->>'created_at',attempt_id FOR SHARE",
            (binding.binding_id, receipt.parked_at))
        calls = []
        for row in cur.fetchall():
            capabilities.require_model_ledger_format("call", row["record"])
            calls.append(ledger._decode_call(row["record"], binding.binding_id, row["attempt_id"]))
        ledger._validate_call_owner_lineage(cur, binding, calls)
        snapshot = FrameworkModelBudgetSnapshot(policy=budget["policy"], calls=calls)
        expected = self._tasks._input_hash("case_execute", {"input": task["input_payload"],
            "runtime_binding_fingerprint": binding.fingerprint, "model_budget": snapshot.policy.model_dump(mode="json")})
        _require(expected == receipt.submission_sha256 and calls
            and all(call.phase == "completed" and call.settled_at is not None and call.settled_at <= receipt.parked_at for call in calls)
            and receipt.model_ledger_sha256 == approval_value_hash(snapshot.model_dump(mode="json")),
            "Controlled wait original settled model ledger changed")

    def _batch_original(self, cur, binding, receipt, capabilities, *, project_id):
        from src.schemas.framework_approval import framework_approval_id, framework_tool_call_id

        journal, settings = self._projection._journal, self._tasks._settings
        formal = {"project_id": project_id, "run_id": receipt.run_id,
            "run_item_id": receipt.run_item_id, "attempt_id": receipt.run_item_execution_attempt_id}
        natives = []
        for entry in receipt.batch:
            cur.execute(f"SELECT * FROM {journal._links} WHERE approval_id=%s FOR SHARE", (entry.approval_id,))
            row = cur.fetchone()
            _require(row is not None, "Controlled wait original interrupt link is missing")
            capabilities.require_approval_journal_format("link", row["link"])
            cur.execute(f"SELECT * FROM {settings.database.postgres_approval_table} WHERE id=%s FOR SHARE", (entry.approval_id,))
            approval = cur.fetchone()
            link = journal.require_link_in_transaction(cur, binding, entry.approval_id, approval)
            native = NativeToolInterrupt.model_validate(link.model_dump(mode="json", include=set(NativeToolInterrupt.model_fields)))
            call_id = framework_tool_call_id(binding, native)
            cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE id=%s FOR SHARE", (entry.tool_job_id,))
            job = cur.fetchone()
            _require((entry.tool_job_id, entry.interrupt_id, entry.native_sha256, entry.link_sha256)
                    == (link.tool_job_id, link.interrupt_id, approval_value_hash(native.model_dump(mode="json")),
                        approval_value_hash(link.model_dump(mode="json")))
                and link.binding_id == binding.binding_id and link.tool_schema_version == binding.tool_schema_version
                and entry.approval_id == framework_approval_id(binding, native) and link.business_call_id == call_id
                and all(row[key] == value for key, value in {"binding_id": binding.binding_id,
                    "checkpoint_ns": journal._namespace(binding, link.checkpoint_namespace), "checkpoint_id": link.checkpoint_id,
                    "graph_task_id": link.graph_task_id, "resume_index": link.resume_index,
                    "source_message_id": link.source_message_id, "tool_call_id": link.tool_call_id}.items())
                and approval["metadata"].get("source") == "test_run_agent_execution"
                and all(approval["metadata"].get(k) == v for k, v in formal.items())
                and job is not None and job["session_id"] == binding.session_id and job["turn_id"] == binding.turn_id
                and job["call_id"] == call_id and job["tool_key"] == link.tool_key
                and job["id"] == self._projection._jobs.call_job_id(binding.session_id, binding.turn_id, call_id)
                and approval_value_hash(job["input_payload"]) == approval_value_hash(native.arguments)
                and job["metadata"].get("framework_binding_id") == binding.binding_id
                and job["metadata"].get("framework_native_identity") == native.model_dump(mode="json")
                and all(job["metadata"].get(k) == v for k, v in formal.items()),
                "Controlled wait original complete batch identity changed")
            natives.append(native)
        return natives

    async def park(self, owner, *, proposals, event_log=None, capabilities=None, hygiene_metadata=None):
        scope = {"task_id": getattr(owner, "task_id", None), "operation": "approval_wait",
            "binding_id": getattr(getattr(owner, "binding", None), "binding_id", None)}
        try:
            _require(owner is not None and owner.controlled_case_lease is not None
                and owner == current_framework_owner() and current_graph_event_sink() is None,
                "Controlled wait requires its captured dual owner after graph event drain")
            owner, proposals, event_log, hygiene_metadata = deepcopy((owner, proposals, event_log, hygiene_metadata))
            _require(bool(proposals), "Controlled wait requires a nonempty complete native batch")
            write_state = capabilities is not None and capabilities.controlled_case_approval_state_available
            if write_state or event_log is not None:
                from src.runtime.task_deferred import TaskDeferred
                from src.application.context.transcript_hygiene_service import TranscriptHygieneService

                if not write_state:
                    raise TaskDeferred("framework_case_approval_state_writer_unavailable")
                capabilities.require_runtime_artifact(owner.binding)
                if capabilities.controlled_case_checkpoint_max_version < 7:
                    raise TaskDeferred("framework_case_checkpoint_format_unavailable")
                if capabilities.controlled_case_approval_wait_max_version < 2:
                    raise TaskDeferred("framework_case_approval_wait_format_unavailable")
                _require(isinstance(event_log, list) and bool(event_log), "Controlled wait state requires its acknowledged event log")
                if hygiene_metadata is None:
                    hygiene_metadata = TranscriptHygieneService().tool_message_metadata()
                receipt = await asyncio.to_thread(self._park, owner, proposals,
                    event_log=event_log, hygiene_metadata=hygiene_metadata)
            else:
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

    def _park(self, owner, proposals, *, event_log=None, hygiene_metadata=None):
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
            asset = None
            if event_log is not None:
                from src.runtime.controlled_case_approval_state import build_wait_state_in_transaction

                asset = build_wait_state_in_transaction(cur, settings, owner, case, task, projected,
                    snapshot, event_log, now, hygiene_metadata)
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
            receipt_type = ControlledCaseApprovalWaitReceipt
            if asset is not None:
                values.update(schema_version=2, state_asset_sha256=asset.asset_sha256)
                receipt_type = ControlledCaseApprovalStateWaitReceipt
            receipt = receipt_type(**values, receipt_sha256=approval_value_hash(values))
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
            event = approval_wait_event(receipt, binding)
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

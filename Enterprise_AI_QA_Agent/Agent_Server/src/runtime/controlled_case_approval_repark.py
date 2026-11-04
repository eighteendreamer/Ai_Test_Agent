"""Atomically close a returned formal successor; never grant graph execution.

No upstream counterpart (checked: initial atomic wait, formal continuation,
immutable progress material, v4 §7.3/§9.2/§12.1): preserve the root receipt and
queued sibling admissions, retaining a separate successor parking proof.
"""
import asyncio
from copy import deepcopy
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.application.deep_agents.native_approval_batch import read_pending_tool_snapshot_original
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_approval_progress import ControlledCaseApprovalProgress
from src.runtime.controlled_case_approval_submission import ENVELOPE
from src.runtime.execution_context import current_framework_owner, current_graph_event_sink
from src.runtime.framework_application_projection import _require_event_intent
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction, lock_owner_row
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.controlled_case_approval_repark import ControlledCaseApprovalReparkReceipt
from src.schemas.framework_approval import approval_value_hash, framework_approval_id
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)
REPARK_KEY = "controlled_case_approval_repark"


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def repark_event(receipt, binding):
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-approval-repark:" + receipt.task_id)),
        session_id=binding.session_id, type="test_run.case_approval_reparked", timestamp=receipt.parked_at,
        payload={"turn_id": binding.turn_id, "task_id": receipt.task_id, "attempt_id": receipt.attempt_id,
            "owner_epoch": receipt.owner_epoch, "origin_task_id": receipt.origin_task_id,
            "parent_wait_sha256": receipt.parent_wait_sha256, "state_asset_sha256": receipt.state_asset_sha256,
            "wait_sha256": receipt.receipt_sha256, "approval_ids": [entry.approval_id for entry in receipt.batch]})


class ControlledCaseApprovalReparks:
    def __init__(self, continuations):
        self._continuations = continuations
        self._tasks, self._settings = continuations._tasks, continuations._settings
        self._progress = ControlledCaseApprovalProgress(continuations)
        self._waits = continuations._submission._waits

    async def repark(self, task, *, capabilities):
        task = self._continuations._capture_delivery(task, capabilities)
        if not capabilities.controlled_case_approval_repark_available:
            raise TaskDeferred("framework_case_approval_repark_writer_unavailable")
        if capabilities.controlled_case_approval_repark_max_version < 1:
            raise TaskDeferred("framework_case_approval_repark_format_unavailable")
        _require(current_framework_owner() == task.runtime_owner and current_graph_event_sink() is None,
            "Controlled re-park requires its captured owner after event drain")
        return await self._run("repark", self._repark, task.id, task, capabilities)

    async def acknowledge_delivery(self, delivery, *, capabilities):
        command = deepcopy(capabilities.require_command_format(delivery))
        capabilities.require_runtime_artifact(command.runtime_binding)
        _require(command.operation == "case_approval", "Controlled re-park ACK requires its original approval delivery")
        return await self._run("historical_ack", self._acknowledge, command.task_id, command, capabilities)

    async def _run(self, operation, function, task_id, *args):
        scope = {"operation": operation, "task_id": task_id, "automatic_replay": False}
        try:
            result = await asyncio.to_thread(function, *args)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_approval_repark_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "repark_receipt_unknown"})
            raise
        logger.info("controlled_case_approval_repark_observed", extra={**scope,
            "wait_sha256": result.receipt_sha256, "approval_count": len(result.batch)})
        return result

    def _asset(self, cur, saved, capabilities):
        _, assets = self._progress._read_chain(cur, saved, capabilities)
        selected = [asset for asset in assets if asset.task_id == saved.task['id']]
        _require(len(selected) == 1, "Controlled re-park requires its original retained progress")
        return selected[0]

    def _batch(self, saved, asset):
        pending = {framework_approval_id(asset.binding, native) for native in asset.natives}
        batch = tuple(entry for entry in saved.wait.batch if entry.approval_id in pending)
        _require(len(batch) == len(pending) and batch and saved.task['reference_id'] not in pending,
            "Controlled re-park requires its exact remaining original siblings")
        return batch

    def _read_receipt(self, cur, saved, asset, capabilities):
        row, root, binding = saved.task, saved.wait, saved.origin_command.runtime_binding
        payload = row['result_payload']
        _require(row['status'] == 'completed' and row['result_status'] == 'waiting_approval'
            and row['error_code'] is None and isinstance(payload, dict) and set(payload) == {REPARK_KEY},
            "Controlled re-park original completed successor/receipt is missing")
        receipt = capabilities.require_case_approval_repark_format(payload[REPARK_KEY])
        source = self._progress._source(cur, saved, receipt.owner_epoch)
        cur.execute(f"SELECT * FROM {self._tasks._attempts} WHERE id=%s FOR SHARE", (source.attempt_id,))
        attempt = cur.fetchone()
        _require(attempt['status'] == 'completed' and attempt['finished_at'] is not None and attempt['error_code'] is None
            and (receipt.binding_fingerprint, receipt.task_id, receipt.attempt_id, receipt.owner_epoch,
                receipt.origin_task_id, receipt.parent_wait_sha256, receipt.state_asset_sha256)
                == (binding.fingerprint, row['id'], asset.attempt_id, asset.owner_epoch,
                    saved.origin['id'], root.receipt_sha256, asset.asset_sha256)
            and (receipt.run_id, receipt.run_item_id, receipt.run_item_execution_attempt_id,
                receipt.input_sha256, receipt.submission_sha256, receipt.admission_sha256, receipt.model_ledger_sha256)
                == (root.run_id, root.run_item_id, root.run_item_execution_attempt_id,
                    root.input_sha256, root.submission_sha256, root.admission_sha256, root.model_ledger_sha256)
            and receipt.batch == self._batch(saved, asset) and receipt.parked_at >= asset.retained_at
            and receipt.task_claim_sha256 == self._waits._worker_fact(cur, source, row, attempt, 'agent_task.claimed')
            and receipt.task_return_sha256 == self._waits._worker_fact(cur, source, row, attempt, 'agent_task.completed'),
            "Controlled re-park differs from its original Worker/material lineage")
        self._waits._historical_ledger(cur, binding, saved.origin, receipt, capabilities)
        event = repark_event(receipt, binding)
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} WHERE id=%s FOR SHARE", (event.id,))
        _require_event_intent(cur.fetchone(), event)
        return receipt

    def _acknowledge(self, command, capabilities):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._continuations._locked(cur, command, capabilities)
            return self._read_receipt(cur, saved, self._asset(cur, saved, capabilities), capabilities)

    def _repark(self, task, capabilities):
        tasks, binding = self._tasks, task.runtime_binding
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._continuations._locked(cur, self._continuations._wire(cur, task.id, capabilities), capabilities)
            _require((task.attempt_id, task.worker_id, task.lease_token)
                == (saved.task['attempt_id'], saved.task['worker_id'], saved.task['lease_token'])
                and all(saved.task[name] == getattr(task, name)
                    for name in (*ENVELOPE, 'parent_task_id', 'reference_id', 'priority'))
                and task.runtime_binding.model_dump(mode='json') == saved.task['runtime_binding']
                and task.input == saved.task['input_payload'],
                "Controlled re-park changed its captured physical task")
            asset = self._asset(cur, saved, capabilities)
            _require(asset.owner_epoch == task.runtime_owner.owner_epoch,
                "Controlled re-park changed its captured epoch")
            if saved.task['result_payload'] is not None:
                return self._read_receipt(cur, saved, asset, capabilities)
            _require(saved.case.attempt.checkpoint_payload['progress_assets'][-1]['asset_sha256'] == asset.asset_sha256,
                "Controlled re-park requires its latest original retained progress")
            saved = self._continuations._captured_originals(cur, task, capabilities)
            session, root = saved.session, saved.wait
            _require(not saved.task['interrupt_requested'] and saved.task['result_status'] is None
                and (session['metadata'].get('control') or {}).get('control_state') not in {
                    'interrupt_requested', 'interrupted', 'cancelled'},
                "Controlled re-park conflicts with current Session/control state")
            batch = self._batch(saved, asset)
            current = self._continuations._submission.current_wait_in_transaction(cur, saved.origin_command, saved.case,
                session, saved.owner, root, saved.origin, capabilities)
            self._continuations._submission._pending_scope(cur, saved.origin_command, session,
                saved.owner, current.receipt, capabilities)
            source = self._progress._source(cur, saved, asset.owner_epoch)
            original = capabilities.require_case_input_format(saved.origin['input_payload']['case_execution_input'])
            read_pending_tool_snapshot_original(cur, self._settings, source, original.graph_input, asset.natives)
            self._waits._require_settled_tool_activity(cur, binding, {entry.tool_job_id for entry in batch})
            ledger = PostgresFrameworkModelCalls(self._settings)._snapshot(cur, binding)
            _require(approval_value_hash(ledger.model_dump(mode='json')) == root.model_ledger_sha256,
                "Controlled re-park has later model activity")
            case = assert_runtime_owner(cur, self._settings, task.runtime_owner)
            owner = lock_owner_row(cur, self._settings, binding)
            session = tasks._lock_session(cur, binding.session_id)
            deadlines = (session['metadata']['turn_lease_expires_at'], owner['expires_at'], case.item.lease_expires_at)
            cur.execute('SELECT clock_timestamp() AS now')
            now = cur.fetchone()['now']
            first = batch[0]
            case.store._mark_waiting_approval_in_transaction(cur, case.item.id, case.lease.lease_token,
                first.approval_id, first.tool_job_id, now)
            tasks._update_status(cur, saved.task, 'completed', 'waiting_approval', None, execution_returned=True)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s", (task.id,))
            returned = cur.fetchone()
            cur.execute(f"SELECT * FROM {tasks._attempts} WHERE id=%s", (task.attempt_id,))
            attempt = cur.fetchone()
            values = root.model_dump(mode='json', exclude={'state_asset_sha256', 'receipt_sha256'})
            values.update(schema_version=1, scope='controlled_case_atomic_approval_repark_only',
                task_id=task.id, attempt_id=task.attempt_id, owner_epoch=task.runtime_owner.owner_epoch,
                origin_task_id=saved.origin['id'], parent_wait_sha256=root.receipt_sha256,
                state_asset_sha256=asset.asset_sha256, parked_at=now, batch=batch,
                task_claim_sha256=self._waits._worker_fact(cur, task.runtime_owner, returned, attempt, 'agent_task.claimed'),
                task_return_sha256=self._waits._worker_fact(cur, task.runtime_owner, returned, attempt, 'agent_task.completed'))
            values = TypeAdapter(dict).dump_python(values, mode='json')
            receipt = ControlledCaseApprovalReparkReceipt(**values, receipt_sha256=approval_value_hash(values))
            cur.execute(f"UPDATE {tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s AND result_payload IS NULL",
                (json.dumps({REPARK_KEY: receipt.model_dump(mode='json')}), task.id))
            _require(cur.rowcount == 1, "Controlled re-park already has a different receipt")
            self._waits._publish_parked_session(cur, saved.task, session, receipt, binding)
            _require(tasks._sessions.append_event_in_transaction(cur, binding.session_id, repark_event(receipt, binding)),
                "Controlled re-park audit exists without its original receipt")
            self._read_receipt(cur, self._continuations._locked(cur,
                self._continuations._wire(cur, task.id, capabilities), capabilities), asset, capabilities)
            cur.execute('SELECT %s::timestamptz > clock_timestamp() AND %s::timestamptz > clock_timestamp() '
                'AND %s::timestamptz > clock_timestamp() AS active', deadlines)
            if cur.fetchone()['active'] is not True:
                raise ContinuationLeaseLostError("Controlled re-park crossed its original locked lease deadline")
            return receipt

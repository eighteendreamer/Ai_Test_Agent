"""Retain/read original successor material, never park or advance the graph.

No upstream counterpart (checked: initial waiting material, dedicated approval
application, v4 §7.3/§9.2): retain the root plus an immutable hash-linked suffix
on the original formal Attempt. Native/model/Job facts remain authoritative.
"""
import asyncio
from copy import deepcopy
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.deep_agents.native_approval_batch import (
    read_pending_tool_snapshot_original, require_historical_tool_interrupts_original,
)
from src.application.runtime.controlled_case_wait_projection import project_controlled_approval_progress, wait_native_messages
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_approval_state import _events
from src.runtime.execution_context import current_framework_owner, current_graph_event_sink
from src.runtime.framework_application_projection import _require_event_intent
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_event_completion import original_checkpoint_source
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.task_deferred import TaskDeferred
from src.schemas.controlled_case_admission import ControlledCaseApprovalProgressCheckpoint, read_approval_history_checkpoint
from src.schemas.controlled_case_approval_progress import ControlledCaseApprovalProgressAsset
from src.schemas.framework_approval import approval_value_hash, framework_approval_id
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def progress_event(asset):
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-approval-progress:" + asset.task_id)),
        session_id=asset.binding.session_id, type="test_run.case_approval_progress_retained", timestamp=asset.retained_at,
        payload={"turn_id": asset.binding.turn_id, "task_id": asset.task_id, "attempt_id": asset.attempt_id,
            "owner_epoch": asset.owner_epoch, "origin_task_id": asset.origin_task_id,
            "parent_state_sha256": asset.parent_state_sha256, "state_asset_sha256": asset.asset_sha256,
            "scope": asset.scope})


class ControlledCaseApprovalProgress:
    def __init__(self, continuations):
        self._continuations = continuations
        self._tasks, self._settings = continuations._tasks, continuations._settings

    async def retain(self, task, *, event_log, capabilities):
        task = self._continuations._capture_delivery(task, capabilities)
        if not capabilities.controlled_case_approval_progress_available:
            raise TaskDeferred("framework_case_approval_progress_writer_unavailable")
        if capabilities.controlled_case_checkpoint_max_version < 8:
            raise TaskDeferred("framework_case_checkpoint_format_unavailable")
        _require(current_framework_owner() == task.runtime_owner and current_graph_event_sink() is None,
            "Controlled progress retention requires its captured owner after event drain")
        return await self._run("retain", self._retain, task.id, task, deepcopy(event_log), capabilities)

    async def read(self, delivery, *, capabilities):
        command = deepcopy(capabilities.require_command_format(delivery))
        capabilities.require_runtime_artifact(command.runtime_binding)
        _require(command.operation == "case_approval", "Controlled progress read requires its original approval delivery")
        return await self._run("read", self._read, command.task_id, command, capabilities)

    async def _run(self, operation, function, task_id, *args):
        scope = {"operation": operation, "task_id": task_id, "automatic_replay": False}
        try:
            result = await asyncio.to_thread(function, *args)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_approval_progress_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "progress_material_receipt_unknown"})
            raise
        logger.info("controlled_case_approval_progress_observed", extra={**scope,
            "state_asset_sha256": result.asset_sha256, "scope": result.scope})
        return result

    def _chain(self, saved):
        checkpoint = read_approval_history_checkpoint(saved.case.attempt)
        if isinstance(checkpoint, ControlledCaseApprovalProgressCheckpoint):
            return checkpoint.initial_wait, list(checkpoint.progress_assets)
        return checkpoint, []

    def _selected(self, cur, saved):
        shared = self._continuations._decision_reader()
        plan, consumed = shared.inspect_decision_originals_in_transaction(cur, saved.origin_command.runtime_binding,
            approval_id=saved.admission.approval_id, decision_id=saved.admission.decision_id,
            decision_sha256=saved.admission.decision_sha256)
        material = shared._applied_tool_projection_in_transaction(cur, saved.origin_command.runtime_binding, plan, consumed)
        _require(consumed and plan.receipt.applied_at is not None, "Controlled progress requires original applied tool material")
        return plan, material

    def _source(self, cur, saved, epoch):
        source = original_checkpoint_source(cur, self._settings, saved.origin_command.runtime_binding, saved.task, epoch)
        cur.execute(f"SELECT * FROM {self._tasks._attempts} WHERE id=%s FOR SHARE", (source.attempt_id,))
        attempt = cur.fetchone()
        _require(attempt is not None and attempt['started_at'] is not None
            and (attempt['task_id'], attempt['worker_id'], attempt['lease_token'], attempt['attempt_no'])
                == (saved.task['id'], saved.task['worker_id'], saved.task['lease_token'], saved.task['attempt_no']),
            "Controlled progress changed its physical Worker attempt")
        self._continuations._submission._waits._worker_fact(cur, source, saved.task, attempt, 'agent_task.claimed')
        return source

    def _validate(self, cur, saved, parent, asset, capabilities):
        plan, material = self._selected(cur, saved)
        _require(asset.admission == saved.admission and asset.decision_receipt == plan.receipt
            and asset.attempt_id == saved.task['attempt_id'] and asset.tool_materials == (*parent.tool_materials, material),
            "Controlled progress changed its original decision/application material")
        source = self._source(cur, saved, asset.owner_epoch)
        original = capabilities.require_case_input_format(saved.origin['input_payload']['case_execution_input'])
        snapshots = require_historical_tool_interrupts_original(cur, self._settings, source, original.graph_input, asset.natives)
        _require(len(snapshots) == 1, "Controlled progress requires one original direct native root")
        native = next(iter(snapshots.values()))
        _require(serialize_messages(native.values.get('messages', [])) == asset.native_messages,
            "Controlled progress changed its original native messages")
        allowed = {(m.source.source_message_id, m.source.tool_call_id) for m in asset.tool_materials}
        expected = project_controlled_approval_progress(parent, material, wait_native_messages(native, retained_sources=allowed),
            asset.natives, asset.state['event_log'], state=parent.state, records=parent.state['tool_results'],
            materials=asset.tool_materials, root_model_budget=parent.root_model_budget)
        event = progress_event(asset)
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} WHERE id=%s FOR SHARE", (event.id,))
        row = cur.fetchone()
        _require_event_intent(row, event)
        references = _events(cur, self._settings, asset.binding, asset.state['event_log'], before_sequence=row['event_sequence'])
        _require(TypeAdapter(Any).dump_python(expected, mode='json') == asset.state and references == asset.event_references,
            "Controlled progress differs from its deterministic original projection")
        return asset

    def _read_chain(self, cur, saved, capabilities):
        initial, assets = self._chain(saved)
        parent = initial.state_asset
        for asset in assets:
            original = self._continuations._locked(cur, self._continuations._wire(cur, asset.task_id, capabilities), capabilities)
            self._validate(cur, original, parent, asset, capabilities)
            parent = asset
        return initial, assets

    def _read(self, command, capabilities):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._continuations._locked(cur, command, capabilities)
            _, assets = self._read_chain(cur, saved, capabilities)
            found = [asset for asset in assets if asset.task_id == command.task_id]
            _require(len(found) == 1, "Controlled progress has no unique original retained material")
            return found[0]

    def _retain(self, task, event_log, capabilities):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._continuations._captured_originals(cur, task, capabilities)
            initial, assets = self._read_chain(cur, saved, capabilities)
            found = [asset for asset in assets if asset.task_id == task.id]
            if found:
                _require(len(found) == 1 and found[0].state['event_log'] == event_log,
                    "Controlled progress retry changed its original event material")
                assert_runtime_owner(cur, self._settings, task.runtime_owner)
                return found[0]
            parent = assets[-1] if assets else initial.state_asset
            plan, material = self._selected(cur, saved)
            binding = task.runtime_binding
            natives = [n for n in parent.natives if framework_approval_id(binding, n) != task.reference_id]
            _require(len(natives) == len(parent.natives) - 1 and bool(natives),
                "Controlled progress requires original remaining siblings; terminal/new batches need their own material")
            source = self._source(cur, saved, task.runtime_owner.owner_epoch)
            original = capabilities.require_case_input_format(saved.origin['input_payload']['case_execution_input'])
            _, native = read_pending_tool_snapshot_original(cur, self._settings, source, original.graph_input, natives)
            ledger = PostgresFrameworkModelCalls(self._settings)._snapshot(cur, binding)
            _require(approval_value_hash(ledger.model_dump(mode='json')) == saved.wait.model_ledger_sha256,
                "Controlled progress changed its root model ledger before sibling closure")
            references = _events(cur, self._settings, binding, event_log)
            state = project_controlled_approval_progress(parent, material, wait_native_messages(native), natives, event_log,
                state=parent.state, records=parent.state['tool_results'], materials=(*parent.tool_materials, material),
                root_model_budget=parent.root_model_budget)
            cur.execute('SELECT clock_timestamp() AS now')
            values = TypeAdapter(Any).dump_python(dict(schema_version=1, scope='controlled_case_approval_progress_material_only',
                binding=binding, origin_task_id=saved.origin['id'], task_id=task.id, attempt_id=task.attempt_id,
                owner_epoch=task.runtime_owner.owner_epoch, parent_state_sha256=parent.asset_sha256,
                admission=saved.admission, decision_receipt=plan.receipt, natives=natives,
                native_messages=serialize_messages(native.values.get('messages', [])),
                tool_materials=(*parent.tool_materials, material), root_model_budget=parent.root_model_budget,
                hygiene_metadata=parent.hygiene_metadata, event_references=references, state=state,
                retained_at=cur.fetchone()['now']), mode='json')
            asset = ControlledCaseApprovalProgressAsset(**values, asset_sha256=approval_value_hash(values))
            checkpoint = ControlledCaseApprovalProgressCheckpoint(schema_version=8, initial_wait=initial,
                progress_assets=(*assets, asset))
            saved.case.store._write_attempt(cur, saved.case.attempt.model_copy(update={
                'checkpoint_version': 8, 'checkpoint_payload': checkpoint.model_dump(mode='json')}))
            _require(self._tasks._sessions.append_event_in_transaction(cur, binding.session_id, progress_event(asset)),
                "Controlled progress audit exists without its original retained material")
            self._validate(cur, saved, parent, asset, capabilities)
            assert_runtime_owner(cur, self._settings, task.runtime_owner)
            return asset

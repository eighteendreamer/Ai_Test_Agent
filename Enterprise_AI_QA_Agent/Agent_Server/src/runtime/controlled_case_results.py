"""Original case result retention in its RunItem attempt, not a second finalizer.

No upstream counterpart (checked: FrameworkRuntimeResults, RunItem checkpoint,
v4 §9/§12.1): formal input has no message-stage. Reuse the original attempt,
shared asset and original-material checks, without new tables or execution rights.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from uuid import NAMESPACE_URL, uuid5

from src.application.testing.verification_service import VerificationService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_result_reader import ControlledCaseResultReader, _require
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.framework_application_projection import _model_hash, _tool_hash
from src.schemas.controlled_case_admission import ControlledCaseResultCheckpoint, read_result_checkpoint
from src.schemas.controlled_case_task_input import ControlledCaseFinalizationTaskInputAsset, parse_controlled_case_task_input
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_runtime_result import ControlledCaseRuntimeResultAsset, FrameworkRuntimeResult
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)


def asset_event_id(binding):
    return str(uuid5(NAMESPACE_URL, "controlled-case-result-asset:" + binding.fingerprint))


def _retention_event(checkpoint):
    asset = checkpoint.runtime_result_asset
    return ExecutionEvent(id=asset_event_id(asset.binding), session_id=asset.binding.session_id,
        timestamp=checkpoint.retained_at, type="test_run.case_result_prepared",
        payload={"turn_id": asset.binding.turn_id, "task_id": asset.task_id, "attempt_id": asset.attempt_id,
            "owner_epoch": asset.owner_epoch, "run_id": asset.run_id, "run_item_id": asset.run_item_id,
            "run_item_execution_attempt_id": asset.run_item_execution_attempt_id,
            "runtime_result_asset_sha256": asset.asset_sha256, "application_finalized": False})


class ControlledCaseResults:
    def __init__(self, tasks):
        self._tasks = tasks

    async def freeze(self, owner, *, runtime_result, native):
        # Preserve worker-only authority; capture inputs before the DB thread.
        owner, native = deepcopy((owner, native))
        result = FrameworkRuntimeResult.from_result(runtime_result).model_dump(mode="json")
        return await self._run(owner, native, result)

    async def load(self, owner, *, native):
        owner, native = deepcopy((owner, native))
        return await self._run(owner, native, None)

    async def _run(self, owner, native, supplied):
        scope = {"task_id": getattr(owner, "task_id", None),
            "binding_id": getattr(getattr(owner, "binding", None), "binding_id", None)}
        try:
            asset = await asyncio.to_thread(self._material, owner, native, supplied)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_result_asset_rejected", extra={**scope,
                "error_type": type(exc).__name__, "operation": "load" if supplied is None else "freeze",
                "recovery_reason": "asset_read_unsettled" if supplied is None else "asset_write_receipt_unknown",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_result_asset_retained", extra={**scope,
            "asset_sha256": asset.asset_sha256, "operation": "load" if supplied is None else "freeze",
            "application_finalized": False})
        return asset

    def _material(self, owner, native, supplied):
        _require(owner is not None and owner.controlled_case_lease is not None,
            "Controlled result asset requires its original live dual owner")
        tasks, binding = self._tasks, owner.binding
        settings = tasks._settings
        with postgres_connect(settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            locked = assert_runtime_owner(cur, settings, owner)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
            task = cur.fetchone()
            _require(task is not None, "Controlled result asset original task is missing")
            original = parse_controlled_case_task_input((task["input_payload"] or {}).get("case_execution_input"))
            _require(isinstance(original, ControlledCaseFinalizationTaskInputAsset),
                "Controlled result asset has no original retention/Verification preparation")
            result = ControlledCaseResultReader(tasks).read_in_transaction(cur, owner, original, native)
            material = FrameworkRuntimeResult.from_result(result).model_dump(mode="json")
            _require(supplied is None or supplied == material,
                "Controlled result asset differs from its deterministic original projection")
            previous = read_result_checkpoint(locked.attempt)
            if previous is not None:
                # Retained verifications are reused, never rebuilt on reentry.
                asset = require_retained_case_asset(cur, settings, owner, original, previous, material)
                assert_runtime_owner(cur, settings, owner)
                return asset
            _require(supplied is not None, "Controlled original result asset is missing")
            verifications = VerificationService().build_results(session_id=binding.session_id,
                turn_id=binding.turn_id, trace_id=original.graph_input.trace_id,
                tool_results=result.state["tool_results"], context_bundle=result.state["context_bundle"])
            admission = locked.checkpoint
            values = dict(schema_version=2, scope="completed_controlled_case_finalization_input",
                binding=binding.model_dump(mode="json"), task_id=owner.task_id, attempt_id=owner.attempt_id,
                owner_epoch=owner.owner_epoch, input_sha256=original.asset_sha256,
                source_sha256=approval_value_hash(admission.model_dump(mode="json")),
                execution_sha256=admission.submission_sha256,
                model_calls_sha256=_model_hash(cur, settings, binding), tool_jobs_sha256=_tool_hash(cur, settings, binding),
                runtime_result=material, assistant_message_id=original.runtime_preparation.identity.assistant_message_id,
                user_message=original.context.objective,
                verification_results=[item.model_dump(mode="json") for item in verifications],
                projection_at=result.events[-1].model_dump(mode="json")["timestamp"],
                run_id=original.context.run_id, run_item_id=original.context.run_item_id,
                run_item_execution_attempt_id=original.run_item_execution_attempt_id)
            asset = ControlledCaseRuntimeResultAsset(**values, asset_sha256=approval_value_hash(values))
            cur.execute("SELECT clock_timestamp() AS now")
            checkpoint = ControlledCaseResultCheckpoint(schema_version=2, admission=admission,
                runtime_result_asset=asset, retained_at=cur.fetchone()["now"])
            # Fresh DB clock after builders/waiting reads; locks hold to commit.
            assert_runtime_owner(cur, settings, owner)
            locked.store._write_attempt(cur, locked.attempt.model_copy(update={"checkpoint_version": 2,
                "checkpoint_payload": checkpoint.model_dump(mode="json")}))
            tasks._sessions.append_event_in_transaction(cur, binding.session_id,
                _retention_event(checkpoint), runtime_owner=owner)
            return asset


def require_retained_case_asset(cur, settings, owner, original, checkpoint, material):
    """Shared original retention checks, AFTER the complete result reader."""
    asset, binding = checkpoint.runtime_result_asset, owner.binding
    _require(asset.runtime_result.model_dump(mode="json") == material
        and asset.assistant_message_id == original.runtime_preparation.identity.assistant_message_id
        and asset.user_message == original.context.objective
        and asset.projection_at == asset.runtime_result.events[-1].timestamp
        and (asset.task_id, asset.attempt_id, asset.owner_epoch)
            == (owner.task_id, owner.attempt_id, owner.owner_epoch)
        and asset.model_calls_sha256 == _model_hash(cur, settings, binding)
        and asset.tool_jobs_sha256 == _tool_hash(cur, settings, binding),
        "Controlled result asset original execution facts changed")
    from src.runtime.framework_application_projection import _require_event_intent

    event = _retention_event(checkpoint)
    cur.execute(f"SELECT * FROM {settings.database.postgres_event_table} WHERE session_id=%s AND id=%s",
        (binding.session_id, event.id))
    row = cur.fetchone()
    _require(row is not None, "Controlled result asset has no atomic retention receipt")
    _require_event_intent(row, event)
    return asset

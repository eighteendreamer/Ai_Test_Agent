"""Storage source for the SAME application part writers, not another finalizer.

No upstream counterpart (checked: FrameworkRuntimeResults, original RunItem
checkpoint and Session part writers, v4 §9.4): formal inputs have no message
stage. Keep the existing algorithms while writing receipts to the real source.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.schemas.controlled_case_admission import (
    ControlledCaseProjectionCheckpoint, ControlledCaseProjectionReceipts, read_result_checkpoint,
)
from src.schemas.framework_approval import approval_value_hash


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlledCaseProjectionSource:
    """Explicit RunItem storage, never a synthetic FrameworkMessageStage."""
    locked: object
    owner: object
    checkpoint: object

    @property
    def source_manifest(self):
        parts = (self.checkpoint.projection_receipts.model_dump(mode="json")
            if isinstance(self.checkpoint, ControlledCaseProjectionCheckpoint) else {})
        return {"runtime_result_asset": self.checkpoint.runtime_result_asset.model_dump(mode="json"),
            **{key: value for key, value in parts.items() if value is not None}}


def lock_projection_source(cur, settings, write):
    from src.runtime.framework_runtime_results import lock_runtime_result_stage, _require

    owner = write.owner
    if owner.binding.runtime_kind != "langgraph":
        return lock_runtime_result_stage(cur, settings, owner)
    from src.runtime.controlled_case_result_reader import ControlledCaseResultReader
    from src.runtime.controlled_case_results import require_retained_case_asset
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.schemas.controlled_case_task_input import ControlledCaseFinalizationTaskInputAsset, parse_controlled_case_task_input
    from src.schemas.framework_runtime_result import FrameworkRuntimeResult

    _require(write.controlled_case_native is not None, "Formal projection requires its exact native original")
    configure_runtime_owner_transaction(cur, settings)
    locked = assert_runtime_owner(cur, settings, owner)
    _require(locked is not None, "Formal projection has no original dual owner")
    tasks = PostgresAgentTaskStore(settings, None)
    cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
    task = cur.fetchone()
    _require(task is not None, "Formal projection original task is missing")
    original = parse_controlled_case_task_input((task["input_payload"] or {}).get("case_execution_input"))
    _require(isinstance(original, ControlledCaseFinalizationTaskInputAsset), "Formal projection has no original result preparation")
    checkpoint = read_result_checkpoint(locked.attempt)
    _require(checkpoint is not None, "Formal projection has no retained original result")
    result = ControlledCaseResultReader(tasks).read_in_transaction(cur, owner, original, write.controlled_case_native)
    require_retained_case_asset(cur, settings, owner, original, checkpoint,
        FrameworkRuntimeResult.from_result(result).model_dump(mode="json"))
    return ControlledCaseProjectionSource(locked, owner, checkpoint), task


def validate_projection_result(cur, settings, owner, source, task, raw):
    from src.runtime.framework_runtime_results import validate_runtime_result, _require

    if not isinstance(source, ControlledCaseProjectionSource):
        return validate_runtime_result(cur, settings, owner, source, task, raw)
    asset = source.checkpoint.runtime_result_asset
    _require(raw == asset.model_dump(mode="json"), "Formal projection result differs from its original source")
    return asset


def save_projection_manifest(cur, settings, source, manifest, *, message_version):
    from src.runtime.framework_runtime_results import _require

    if isinstance(source, ControlledCaseProjectionSource):
        values = dict(manifest)
        _require(values.pop("runtime_result_asset", None) == source.checkpoint.runtime_result_asset.model_dump(mode="json"),
            "Formal projection cannot replace its retained result")
        parts = ControlledCaseProjectionReceipts.model_validate(values)
        checkpoint = ControlledCaseProjectionCheckpoint.model_validate({
            **source.checkpoint.model_dump(mode="json"), "schema_version": 3,
            "projection_receipts": parts.model_dump(mode="json")})
        # A fresh DB clock AFTER the builder/SDK reads; keep the dual locks to commit.
        assert_runtime_owner(cur, settings, source.owner)
        source.locked.store._write_attempt(cur, source.locked.attempt.model_copy(update={
            "checkpoint_version": 3, "checkpoint_payload": checkpoint.model_dump(mode="json")}))
        logger.info("controlled_case_projection_parts_recorded", extra={"task_id": source.owner.task_id,
            "run_item_id": checkpoint.admission.run_item_id, "checkpoint_version": 3, "application_finalized": False})
        return
    from src.runtime.framework_message_stages import message_stage_table, validate_message_stage

    updated = validate_message_stage(source.model_copy(update={"schema_version": max(message_version, source.schema_version),
        "source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}).model_dump())
    cur.execute(f"UPDATE {message_stage_table(settings)} SET record=%s::jsonb WHERE task_id=%s",
        (updated.model_dump_json(), source.task_id))

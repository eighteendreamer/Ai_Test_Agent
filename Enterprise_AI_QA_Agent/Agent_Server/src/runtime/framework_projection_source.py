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
    ControlledCaseApplicationCheckpoint, ControlledCaseApplicationReceipts,
    ControlledCaseAssessmentCheckpoint, ControlledCaseSettlementCheckpoint,
    ControlledCaseApprovalResultCheckpoint, ControlledCaseApprovalProjectionCheckpoint, ControlledCaseApprovalApplicationCheckpoint,
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
    return lock_result_source(cur, settings, write.owner, controlled_case_native=write.controlled_case_native,
        controlled_case_reader=write.controlled_case_reader)


def lock_result_source(cur, settings, owner, *, controlled_case_native=None, controlled_case_reader=None):
    from src.runtime.framework_runtime_results import lock_runtime_result_stage, _require

    if owner.binding.runtime_kind != "langgraph":
        return lock_runtime_result_stage(cur, settings, owner)
    from src.runtime.controlled_case_result_reader import ControlledCaseResultReader
    from src.runtime.controlled_case_results import require_retained_case_asset
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.schemas.controlled_case_task_input import ControlledCaseFinalizationTaskInputAsset
    from src.schemas.framework_runtime_result import FrameworkRuntimeResult

    _require(controlled_case_native is not None, "Formal projection requires its exact native original")
    configure_runtime_owner_transaction(cur, settings)
    locked = assert_runtime_owner(cur, settings, owner)
    _require(locked is not None, "Formal projection has no original dual owner")
    tasks = PostgresAgentTaskStore(settings, None)
    cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
    task = cur.fetchone()
    _require(task is not None, "Formal projection original task is missing")
    reader = controlled_case_reader if controlled_case_reader is not None else ControlledCaseResultReader(tasks)
    _require(isinstance(reader, ControlledCaseResultReader) and reader._tasks._settings == settings,
        "Formal projection requires its explicit original storage reader")
    if task['operation'] == 'case_approval':
        reader.require_approval_finalization_available()
    original, _, _, _, _ = reader.original_input_in_transaction(cur, owner, locked, task)
    _require(isinstance(original, ControlledCaseFinalizationTaskInputAsset), "Formal projection has no original result preparation")
    checkpoint = read_result_checkpoint(locked.attempt)
    _require(checkpoint is not None, "Formal projection has no retained original result")
    result = reader.read_in_transaction(cur, owner, original, controlled_case_native)
    require_retained_case_asset(cur, settings, owner, original, checkpoint,
        FrameworkRuntimeResult.from_result(result).model_dump(mode="json"))
    return ControlledCaseProjectionSource(locked, owner, checkpoint), task


def source_input_message_id(source):
    return None if isinstance(source, ControlledCaseProjectionSource) else source.user_message_id


def source_execution_sha256(source):
    return (source.checkpoint.admission.submission_sha256 if isinstance(source, ControlledCaseProjectionSource)
        else source.execution_sha256)


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
        _require(not isinstance(source.checkpoint, ControlledCaseSettlementCheckpoint),
            "Settled controlled projection is read-only")
        values = dict(manifest)
        _require(values.pop("runtime_result_asset", None) == source.checkpoint.runtime_result_asset.model_dump(mode="json"),
            "Formal projection cannot replace its retained result")
        completed = "application_projection_commit" in values
        parts = (ControlledCaseApplicationReceipts if completed else ControlledCaseProjectionReceipts).model_validate(values)
        if isinstance(source.checkpoint, ControlledCaseApprovalResultCheckpoint):
            reader, version = ((ControlledCaseApprovalApplicationCheckpoint, 11) if completed
                else (ControlledCaseApprovalProjectionCheckpoint, 10))
        else:
            reader = (ControlledCaseAssessmentCheckpoint if isinstance(source.checkpoint, ControlledCaseAssessmentCheckpoint)
                else ControlledCaseApplicationCheckpoint if completed else ControlledCaseProjectionCheckpoint)
            version = 5 if reader is ControlledCaseAssessmentCheckpoint else 4 if completed else 3
        checkpoint = reader.model_validate({
            **source.checkpoint.model_dump(mode="json"), "schema_version": version,
            "projection_receipts": parts.model_dump(mode="json")})
        # A fresh DB clock AFTER the builder/SDK reads; keep the dual locks to commit.
        assert_runtime_owner(cur, settings, source.owner)
        source.locked.store._write_attempt(cur, source.locked.attempt.model_copy(update={
            "checkpoint_version": version, "checkpoint_payload": checkpoint.model_dump(mode="json")}))
        logger.info("controlled_case_projection_parts_recorded", extra={"task_id": source.owner.task_id,
            "run_item_id": checkpoint.admission.run_item_id, "checkpoint_version": version,
            "application_commit_in_transaction": completed, "run_item_completed": False})
        return ControlledCaseProjectionSource(source.locked, source.owner, checkpoint)
    from src.runtime.framework_message_stages import message_stage_table, validate_message_stage

    updated = validate_message_stage(source.model_copy(update={"schema_version": max(message_version, source.schema_version),
        "source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}).model_dump())
    cur.execute(f"UPDATE {message_stage_table(settings)} SET record=%s::jsonb WHERE task_id=%s",
        (updated.model_dump_json(), source.task_id))
    return updated

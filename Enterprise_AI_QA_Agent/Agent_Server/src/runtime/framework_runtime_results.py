"""Freeze finalization input under its original execution owner, before writes.

No upstream counterpart (checked: framework message stages/application commits,
LangGraph persistence, integration plan v3 §12.1 A2-1): native graph checkpoints
do not retain our RuntimeTurnResult or Verification identities. This asset alone
does not admit a successor: message recovery must validate its lineage and all
existing parts. It never proves an application commit or permits graph replay.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_application_projection import COMMIT_KEY, _model_hash, _tool_hash
from src.runtime.framework_message_stages import (
    FrameworkMessageStageRecoveryRequired, message_stage_table, validate_message_stage,
)
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_runtime_result import FrameworkRuntimeResult, FrameworkRuntimeResultAsset


logger = logging.getLogger(__name__)
RESULT_KEY = "runtime_result_asset"
APPROVAL_TASK_OPERATIONS = frozenset({"approval_resume", "approval_recover"})


def _require(condition, message):
    if not condition:
        raise FrameworkMessageStageRecoveryRequired(message)


def _approval_preparation(stage, task):
    from src.schemas.framework_graph_input import FrameworkApprovalGraphInput

    inputs = (stage.source_manifest or {}).get("approval_graph_inputs") or {}
    raw = inputs.get(task["id"])
    if raw is None:
        return None
    preparation = FrameworkApprovalGraphInput.model_validate(raw)
    _require(preparation.task_id == task["id"] and preparation.attempt_id == task["attempt_id"]
        and preparation.approval_id == task["reference_id"]
        and preparation.decision_id == (task["input_payload"] or {}).get("decision_id")
        and preparation.graph_input_asset.runtime_preparation.identity.binding.model_dump(mode="json") == task["runtime_binding"],
        "Runtime result preparation differs from its original approval task")
    return preparation


def runtime_result_lineage(stage, task):
    """An approval's original preparation never authorizes a sibling or message."""
    if task["operation"] in APPROVAL_TASK_OPERATIONS:
        preparation = _approval_preparation(stage, task)
        _require(preparation is not None, "Runtime result has no original approval preparation")
        return {(preparation.task_id, preparation.attempt_id, preparation.owner_epoch)}
    return {(stage.current_task_id, stage.owner_attempt_id, stage.owner_epoch),
        *((link.predecessor_task_id, link.predecessor_attempt_id, link.predecessor_owner_epoch)
            for link in stage.recoveries)}


def _task_user_message(stage, task):
    if task["operation"] in APPROVAL_TASK_OPERATIONS:
        preparation = _approval_preparation(stage, task)
        _require(preparation is not None, "Runtime result has no original approval preparation")
        return str((preparation.graph_input_asset.runtime_preparation.initial_state or {}).get("user_message") or "")
    return task["input_payload"].get("content", "")


def lock_runtime_result_stage(cur, settings, owner):
    """Share the same owner/stage admission with transactional part writers."""
    configure_runtime_owner_transaction(cur, settings)
    assert_runtime_owner(cur, settings, owner)
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE", (owner.task_id,))
    task = cur.fetchone()
    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (owner.binding.binding_id,))
    row = cur.fetchone()
    _require(row is not None, "Runtime result has no message stage")
    stage = validate_message_stage(row["record"])
    _require(task is not None and task["status"] == "running"
        and task["attempt_id"] == owner.attempt_id
        and task["runtime_binding"] == owner.binding.model_dump(mode="json")
        and task["session_id"] == owner.binding.session_id and task["turn_id"] == owner.binding.turn_id
        and stage.phase == "runtime_submitted", "Runtime result has no matching active execution owner")
    message_owner = (task["operation"] in {"message", "message_recover"}
        and stage.current_task_id == owner.task_id
        and stage.owner_attempt_id == owner.attempt_id and stage.owner_epoch == owner.owner_epoch)
    preparation = _approval_preparation(stage, task) if task["operation"] in APPROVAL_TASK_OPERATIONS else None
    approval_owner = (preparation is not None and preparation.owner_epoch == owner.owner_epoch)
    _require(message_owner or approval_owner, "Runtime result has no matching active message or approval owner")
    return stage, task


def runtime_result_source(stage):
    source = dict(stage.source_manifest or {})
    source.pop(RESULT_KEY, None)
    source.pop(COMMIT_KEY, None)
    source.pop("snapshot_projection_commit", None)
    source.pop("event_projection_commits", None)
    source.pop("observation_projection_asset", None)
    source.pop("observation_projection_commits", None)
    return source


def validate_runtime_result(cur, settings, owner, stage, task, raw):
    return validate_runtime_result_binding(cur, settings, owner.binding, stage, task, raw)


def _require_prepared_identity(stage, task, asset):
    """Compare original application intent, not database-assigned row headers."""
    from src.schemas.framework_graph_input import parse_graph_input_asset

    raw = (stage.source_manifest or {}).get("graph_input_asset")
    if task["operation"] in APPROVAL_TASK_OPERATIONS:
        preparation = _approval_preparation(stage, task)
        raw = preparation.graph_input_asset.model_dump(mode="json") if preparation is not None else None
    if raw is None:
        return
    prepared = parse_graph_input_asset(raw)
    if prepared.schema_version == 1:
        return
    identity = prepared.runtime_preparation.identity
    snapshot = asset.runtime_result.snapshot
    _require(identity.binding == asset.binding and identity.assistant_message_id == asset.assistant_message_id
        and identity.snapshot_id == snapshot.id and identity.snapshot_version == snapshot.version
        and identity.snapshot_created_at == snapshot.created_at,
        "Runtime result differs from its original preparation identity")


def validate_runtime_result_binding(cur, settings, binding, stage, task, raw):
    """Read original facts before claim too; this grants no execution ownership."""
    asset = FrameworkRuntimeResultAsset.model_validate(raw)
    lineage = runtime_result_lineage(stage, task)
    _require(asset.binding == binding and asset.input_sha256 == stage.input_sha256
        and asset.execution_sha256 == stage.execution_sha256
        and asset.source_sha256 == approval_value_hash(runtime_result_source(stage))
        and asset.user_message == _task_user_message(stage, task)
        and asset.assistant_message_id != stage.user_message_id
        and (asset.task_id, asset.attempt_id, asset.owner_epoch) in lineage,
        "Runtime result identity or frozen input changed")
    _require(asset.model_calls_sha256 == _model_hash(cur, settings, binding)
        and asset.tool_jobs_sha256 == _tool_hash(cur, settings, binding),
        "Runtime result execution facts changed")
    _require_prepared_identity(stage, task, asset)
    return asset


class FrameworkRuntimeResults:
    def __init__(self, tasks):
        self._tasks, self._settings = tasks, tasks._settings

    def _locked_stage(self, cur, owner):
        return lock_runtime_result_stage(cur, self._settings, owner)

    def _validate(self, cur, owner, stage, task, raw):
        return validate_runtime_result(cur, self._settings, owner, stage, task, raw)

    async def freeze(self, owner, *, runtime_result, assistant_message_id, user_message, verification_results):
        # Snapshot all mutable caller values before entering the synchronous DB
        # transaction. Pydantic rejects arbitrary process objects; the digest
        # also rejects non-finite JSON numbers rather than stringifying them.
        result = FrameworkRuntimeResult.from_result(runtime_result).model_dump(mode="json")
        verifications = [item.model_dump(mode="json") for item in verification_results]

        def write():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                stage, task = self._locked_stage(cur, owner)
                manifest = dict(stage.source_manifest or {})
                _require(COMMIT_KEY not in manifest, "Runtime result cannot be replaced after application commit")
                _require(user_message == _task_user_message(stage, task)
                    and assistant_message_id != stage.user_message_id,
                    "Runtime result differs from its admitted user input")
                if RESULT_KEY in manifest:
                    asset = self._validate(cur, owner, stage, task, manifest[RESULT_KEY])
                    _require(asset.runtime_result.model_dump(mode="json") == result
                        and asset.assistant_message_id == assistant_message_id and asset.user_message == user_message,
                        "Runtime result asset is immutable")
                    # Preparation can allocate random IDs. Only the original
                    # saved preparation wins; a retry never replaces it.
                    return asset
                values = dict(binding=owner.binding.model_dump(mode="json"), task_id=owner.task_id,
                    attempt_id=owner.attempt_id, owner_epoch=owner.owner_epoch, input_sha256=stage.input_sha256,
                    source_sha256=approval_value_hash(runtime_result_source(stage)), execution_sha256=stage.execution_sha256,
                    model_calls_sha256=_model_hash(cur, self._settings, owner.binding),
                    tool_jobs_sha256=_tool_hash(cur, self._settings, owner.binding), runtime_result=result,
                    assistant_message_id=assistant_message_id, user_message=user_message,
                    verification_results=verifications, projection_at=datetime.utcnow().isoformat(),
                    schema_version=1, scope="completed_message_finalization_input")
                asset = FrameworkRuntimeResultAsset(**values, asset_sha256=approval_value_hash(values))
                _require_prepared_identity(stage, task, asset)
                manifest[RESULT_KEY] = asset.model_dump(mode="json")
                result_version = 17 if task["operation"] in APPROVAL_TASK_OPERATIONS else 7
                updated = validate_message_stage(stage.model_copy(update={"schema_version": max(result_version, stage.schema_version),
                    "source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}).model_dump())
                cur.execute(f"UPDATE {message_stage_table(self._settings)} SET record=%s::jsonb WHERE task_id=%s",
                    (updated.model_dump_json(), stage.task_id))
                return asset

        try:
            asset = await asyncio.to_thread(write)
        except Exception as exc:
            logger.warning("framework_runtime_result_freeze_rejected", extra={"task_id": owner.task_id,
                "binding_id": owner.binding.binding_id, "owner_epoch": owner.owner_epoch,
                "error_type": type(exc).__name__})
            raise
        logger.info("framework_runtime_result_frozen", extra={"task_id": owner.task_id,
            "binding_id": owner.binding.binding_id, "asset_sha256": asset.asset_sha256})
        return asset

    async def load(self, owner):
        """Read only: no successor admission, projection, model or tool call."""
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                stage, task = self._locked_stage(cur, owner)
                raw = (stage.source_manifest or {}).get(RESULT_KEY)
                _require(raw is not None, "Original runtime result asset is missing")
                asset = self._validate(cur, owner, stage, task, raw)
                if stage.recoveries and stage.recoveries[-1].resume_phase == "finalizing":
                    from src.runtime.framework_finalization_projection import require_partial_finalization

                    cur.execute(f"SELECT metadata FROM {self._settings.database.postgres_session_table} WHERE id=%s",
                        (owner.binding.session_id,))
                    require_partial_finalization(cur, self._settings, cur.fetchone()["metadata"] or {}, stage, asset)
                return asset

        try:
            return await asyncio.to_thread(read)
        except Exception as exc:
            logger.warning("framework_runtime_result_read_rejected", extra={"task_id": owner.task_id,
                "binding_id": owner.binding.binding_id, "error_type": type(exc).__name__})
            raise

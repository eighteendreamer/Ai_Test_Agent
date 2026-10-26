"""Application projection commits share the actual Session/message/event transaction.

No upstream counterpart (checked: PostgresSessionStore transactional events,
LangGraph persistence/interrupts, integration plan v3 §12.1): a checkpoint cannot
prove that our application projection committed. This receipt excludes Memory,
resource cleanup and arbitrary external effects. It never permits graph replay.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import make_json_safe
from src.runtime.framework_message_stages import (
    FrameworkMessageStageRecoveryRequired, message_stage_table, validate_message_stage,
)
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_projection_commit import FrameworkProjectionCommit
from src.schemas.runtime_binding import RuntimeExecutionOwner


COMMIT_KEY = "application_projection_commit"
SNAPSHOT_COMMIT_KEY = "snapshot_projection_commit"
CONTROL_KEYS = frozenset({"control_state", "is_interrupted", "is_resumable", "preserve_resources",
    "replay_available", "last_snapshot_stage", "last_turn_id"})


@dataclass(frozen=True)
class FrameworkProjectionWrite:
    owner: RuntimeExecutionOwner
    snapshot_id: str
    snapshot_sha256: str
    output_message_id: str | None
    runtime_result_asset_sha256: str | None = None


def _require(condition, message):
    if not condition:
        raise FrameworkMessageStageRecoveryRequired(message)


def _hash(value):
    return approval_value_hash(make_json_safe(value))


def _rows(cur, table, columns, session_id, turn_id):
    cur.execute(f"SELECT {columns} FROM {table} WHERE session_id=%s "
        "AND metadata->>'turn_id'=%s ORDER BY id", (session_id, turn_id))
    rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        # Transport changes on terminal tasks do not rewrite the user's input.
        row["metadata"] = dict(row["metadata"] or {})
        row["metadata"].pop("delivery_status", None)
    return rows


def _message_hashes(cur, settings, binding):
    rows = _rows(cur, settings.database.postgres_message_table,
        "id,role,content,created_at,metadata", binding.session_id, binding.turn_id)
    return {row["id"]: _hash(row) for row in rows}


def _event_hashes(cur, settings, session_id, identities):
    cur.execute(f"SELECT id,type,timestamp,payload FROM {settings.database.postgres_event_table} "
        "WHERE session_id=%s AND id=ANY(%s) ORDER BY id", (session_id, list(identities)))
    return {row["id"]: _hash(dict(row)) for row in cur.fetchall()}


def _model_hash(cur, settings, binding):
    snapshot = PostgresFrameworkModelCalls(settings)._snapshot(cur, binding.binding_id)
    # This receipt acknowledges already-committed application output; it never
    # imports a model response or advances a graph. Legacy completed ledger facts
    # may lack response assets. Hash them as-is, without inventing those assets.
    # Model/graph result import retains its stronger unique-response contract.
    _require(all(call.phase == "completed" for call in snapshot.calls),
        "Application projection has unsettled model facts")
    return approval_value_hash([call.model_dump(mode="json") for call in snapshot.calls])


def _tool_hash(cur, settings, binding):
    table = settings.database.postgres_tool_job_table
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,))
    if not cur.fetchone()["present"]:
        return _hash([])
    cur.execute(f"SELECT id,call_id,tool_key,status,attempt,input_payload,output_payload,"
        f"summary,error_message,artifact_count,metadata,started_at,completed_at FROM {table} "
        "WHERE session_id=%s AND turn_id=%s ORDER BY id", (binding.session_id, binding.turn_id))
    jobs = [dict(row) for row in cur.fetchall()]
    _require(all(job["status"] in {"completed", "partial", "failed", "denied"} for job in jobs),
        "Application projection has unsettled tool jobs")
    return _hash(jobs)


def _verification_hash(metadata, turn_id):
    return _hash([item for item in metadata.get("verification_results", [])
        if isinstance(item, dict) and item.get("turn_id") == turn_id])


def _snapshot(cur, settings, binding, snapshot_id):
    cur.execute(f"SELECT id,session_id,version,created_at,graph_state,stage FROM {settings.database.postgres_snapshot_table} "
        "WHERE id=%s AND session_id=%s", (snapshot_id, binding.session_id))
    row = cur.fetchone()
    _require(row is not None and row["stage"] == "completed", "Application projection snapshot is missing or incomplete")
    state = row["graph_state"] or {}
    _require(state.get("turn_id") == binding.turn_id
        and (state.get("context_bundle") or {}).get("runtime_binding") == binding.model_dump(mode="json"),
        "Application projection snapshot binding differs from admission")
    return dict(row)


def require_snapshot_projection(cur, settings, stage, binding, asset_sha256, snapshot):
    """Validate the exact original saved row, including allocated row headers."""
    from src.schemas.framework_projection_commit import FrameworkSnapshotProjectionCommit

    raw = (stage.source_manifest or {}).get(SNAPSHOT_COMMIT_KEY)
    _require(raw is not None, "Runtime result snapshot has no atomic projection receipt")
    receipt = FrameworkSnapshotProjectionCommit.model_validate(raw)
    _require(receipt.binding_fingerprint == binding.fingerprint
        and receipt.runtime_result_asset_sha256 == asset_sha256
        and receipt.snapshot_id == snapshot["id"] and receipt.snapshot_sha256 == _hash(snapshot),
        "Runtime result snapshot differs from its original projection receipt")
    return receipt


def prepare_snapshot_projection(cur, settings, write, snapshot):
    """Fence and validate BEFORE the snapshot UPSERT, in its own transaction.

    No upstream counterpart (checked: SessionStore version allocation,
    LangGraph persistence, integration plan v3 §12.1 A2-1): a native checkpoint
    is not a receipt for the platform's separately allocated snapshot row.
    """
    from src.runtime.framework_runtime_results import lock_runtime_result_stage, validate_runtime_result, RESULT_KEY

    owner, binding = write.owner, write.owner.binding
    stage, task = lock_runtime_result_stage(cur, settings, owner)
    manifest = stage.source_manifest or {}
    _require(COMMIT_KEY not in manifest, "Committed application snapshot must use ACK recovery")
    _require(manifest.get(RESULT_KEY) is not None, "Runtime result snapshot has no original asset")
    asset = validate_runtime_result(cur, settings, owner, stage, task, manifest[RESULT_KEY])
    _require(write.runtime_result_asset_sha256 == asset.asset_sha256
        and write.snapshot_id == snapshot.id
        and snapshot.model_dump(mode="json") == asset.runtime_result.snapshot.model_dump(mode="json")
        and write.snapshot_sha256 == _hash(snapshot.graph_state),
        "Runtime result snapshot differs from its original write intent")
    cur.execute(f"SELECT id,session_id,version,created_at,graph_state,stage FROM {settings.database.postgres_snapshot_table} "
        "WHERE id=%s FOR UPDATE", (snapshot.id,))
    existing = cur.fetchone()
    if SNAPSHOT_COMMIT_KEY in manifest:
        _require(existing is not None, "Original runtime result snapshot is missing")
        require_snapshot_projection(cur, settings, stage, binding, asset.asset_sha256, dict(existing))
    else:
        _require(existing is None, "Existing runtime result snapshot has no atomic projection receipt")
    return stage, dict(existing) if existing is not None else None


def record_snapshot_projection(cur, settings, write, stage):
    """Called after INSERT, before commit; receipt and actual row commit together."""
    from src.schemas.framework_projection_commit import FrameworkSnapshotProjectionCommit

    snapshot = _snapshot(cur, settings, write.owner.binding, write.snapshot_id)
    receipt = FrameworkSnapshotProjectionCommit(binding_fingerprint=write.owner.binding.fingerprint,
        runtime_result_asset_sha256=write.runtime_result_asset_sha256,
        snapshot_id=write.snapshot_id, snapshot_sha256=_hash(snapshot))
    manifest = dict(stage.source_manifest or {})
    manifest[SNAPSHOT_COMMIT_KEY] = receipt.model_dump(mode="json")
    updated = validate_message_stage(stage.model_copy(update={"schema_version": max(8, stage.schema_version),
        "source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}).model_dump())
    cur.execute(f"UPDATE {message_stage_table(settings)} SET record=%s::jsonb WHERE task_id=%s",
        (updated.model_dump_json(), stage.task_id))
    return snapshot


def record_application_commit(cur, settings, session, events, write):
    """Called only inside save_session_with_events, after its actual row writes."""
    owner, binding = write.owner, write.owner.binding
    assert_runtime_owner(cur, settings, owner)
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE", (owner.task_id,))
    task = cur.fetchone()
    _require(task is not None and task["operation"] in {"message", "message_recover"}
        and task["status"] == "running" and task["attempt_id"] == owner.attempt_id
        and task["runtime_binding"] == binding.model_dump(mode="json"), "Application commit has no active message task")
    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE", (binding.binding_id,))
    row = cur.fetchone()
    _require(row is not None, "Application commit has no message stage")
    stage = validate_message_stage(row["record"])
    _require(stage.phase == "runtime_submitted" and stage.current_task_id == owner.task_id
        and stage.owner_epoch == owner.owner_epoch and stage.owner_attempt_id == owner.attempt_id,
        "Application commit differs from its runtime submission")
    raw_result = (stage.source_manifest or {}).get("runtime_result_asset")
    _require((raw_result is None) == (write.runtime_result_asset_sha256 is None),
        "Application commit result asset disappeared or was not captured")
    if raw_result is not None:
        from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

        result_asset = FrameworkRuntimeResultAsset.model_validate(raw_result)
        _require(write.runtime_result_asset_sha256 == result_asset.asset_sha256,
            "Application commit result asset changed before finalization")
        _require(result_asset.model_calls_sha256 == _model_hash(cur, settings, binding)
            and result_asset.tool_jobs_sha256 == _tool_hash(cur, settings, binding),
            "Application commit result execution facts changed")
    _require(session.id == binding.session_id and session.status.value == "completed"
        and not session.metadata.get("pending_turn")
        and (session.metadata.get("control") or {}).get("control_state") == "completed"
        and session.metadata["control"].get("last_turn_id") == binding.turn_id
        and not session.metadata["control"].get("is_resumable")
        and not session.metadata["control"].get("is_interrupted"),
        "Application commit requires a completed, non-pending Session")
    snapshot = _snapshot(cur, settings, binding, write.snapshot_id)
    _require(_hash(snapshot["graph_state"]) == write.snapshot_sha256, "Application commit snapshot changed before finalization")
    if write.runtime_result_asset_sha256 is not None:
        require_snapshot_projection(cur, settings, stage, binding, write.runtime_result_asset_sha256, snapshot)
    completed = [event for event in events if event.type == "turn.completed" and event.payload.get("turn_id") == binding.turn_id]
    _require(len(completed) == 1 and completed[0].payload.get("session_status") == "completed"
        and all(event.payload.get("turn_id") == binding.turn_id for event in events),
        "Application commit requires one matching completion event")
    messages = _message_hashes(cur, settings, binding)
    _require(stage.user_message_id in messages and (write.output_message_id is None or write.output_message_id in messages),
        "Application commit transcript is incomplete")
    if write.output_message_id is not None:
        cur.execute(f"SELECT role FROM {settings.database.postgres_message_table} WHERE id=%s AND session_id=%s",
            (write.output_message_id, binding.session_id))
        _require(cur.fetchone()["role"] == "assistant", "Application commit output is not an assistant message")
        _require(any(event.type == "assistant.response_generated" and event.payload.get("message_id") == write.output_message_id
            for event in events), "Application commit output has no matching completion event")
    manifest = dict(stage.source_manifest or {})
    manifest.pop(COMMIT_KEY, None)
    cur.execute("SELECT clock_timestamp() AS now")
    committed_at = cur.fetchone()["now"]
    proof = FrameworkProjectionCommit(binding_fingerprint=binding.fingerprint, task_id=owner.task_id,
        attempt_id=owner.attempt_id, owner_epoch=owner.owner_epoch, source_sha256=approval_value_hash(manifest),
        execution_sha256=stage.execution_sha256, snapshot_id=write.snapshot_id, snapshot_sha256=_hash(snapshot),
        output_message_id=write.output_message_id, message_hashes=messages,
        event_hashes=_event_hashes(cur, settings, binding.session_id, [event.id for event in events]),
        model_calls_sha256=_model_hash(cur, settings, binding), tool_jobs_sha256=_tool_hash(cur, settings, binding),
        verification_sha256=_verification_hash(session.metadata, binding.turn_id),
        control={key: value for key, value in session.metadata["control"].items() if key in CONTROL_KEYS},
        committed_at=committed_at)
    _require(len(proof.event_hashes) == len(events), "Application commit events are incomplete")
    previous = (stage.source_manifest or {}).get(COMMIT_KEY)
    _require(previous is None or FrameworkProjectionCommit.model_validate(previous).model_dump(exclude={"committed_at"})
        == proof.model_dump(exclude={"committed_at"}), "Application commit cannot replace a different result")
    manifest[COMMIT_KEY] = previous or proof.model_dump(mode="json")
    updated = validate_message_stage(stage.model_copy(update={"schema_version": max(6, stage.schema_version),
        "source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}).model_dump())
    cur.execute(f"UPDATE {message_stage_table(settings)} SET record=%s::jsonb WHERE task_id=%s",
        (updated.model_dump_json(), stage.task_id))


def require_application_commit(cur, settings, session, binding, stage):
    """Recheck the original committed rows; never promote a bare snapshot."""
    manifest = dict(stage.source_manifest or {})
    raw = manifest.pop(COMMIT_KEY, None)
    _require(stage.schema_version >= 6 and raw is not None, "Runtime submission has no application commit proof")
    proof = FrameworkProjectionCommit.model_validate(raw)
    owners = {(stage.current_task_id, stage.owner_attempt_id, stage.owner_epoch),
        *((link.predecessor_task_id, link.predecessor_attempt_id, link.predecessor_owner_epoch) for link in stage.recoveries)}
    _require(proof.binding_fingerprint == binding.fingerprint and proof.execution_sha256 == stage.execution_sha256
        and proof.source_sha256 == approval_value_hash(manifest)
        and (proof.task_id, proof.attempt_id, proof.owner_epoch) in owners,
        "Application commit identity or input assets changed")
    _require(_hash(_snapshot(cur, settings, binding, proof.snapshot_id)) == proof.snapshot_sha256,
        "Committed application snapshot changed")
    _require(_message_hashes(cur, settings, binding) == proof.message_hashes,
        "Committed application transcript changed")
    _require(_event_hashes(cur, settings, binding.session_id, proof.event_hashes) == proof.event_hashes,
        "Committed application events changed")
    _require(_model_hash(cur, settings, binding) == proof.model_calls_sha256
        and _tool_hash(cur, settings, binding) == proof.tool_jobs_sha256,
        "Committed application execution results changed")
    _require(_verification_hash(session["metadata"] or {}, binding.turn_id) == proof.verification_sha256,
        "Committed application verification changed")
    _require(proof.control.get("control_state") == "completed" and not proof.control.get("is_interrupted"),
        "Committed application control state is not terminal")
    return proof


async def load_committed_projection(settings, owner):
    """Read the exact proven output, never the latest snapshot or assistant text."""
    def load():
        from src.runtime.postgres_session_store import _message_from_row, _snapshot_from_row

        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            assert_runtime_owner(cur, settings, owner)
            cur.execute(f"SELECT * FROM {settings.database.postgres_session_table} WHERE id=%s",
                (owner.binding.session_id,))
            session = cur.fetchone()
            cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE",
                (owner.binding.binding_id,))
            row = cur.fetchone()
            _require(row is not None, "Application commit stage is unavailable")
            stage = validate_message_stage(row["record"])
            _require(stage.current_task_id == owner.task_id and stage.phase == "runtime_submitted",
                "Application commit read has no matching continuation")
            proof = require_application_commit(cur, settings, session, owner.binding, stage)
            cur.execute(f"SELECT * FROM {settings.database.postgres_snapshot_table} WHERE id=%s AND session_id=%s",
                (proof.snapshot_id, owner.binding.session_id))
            snapshot = _snapshot_from_row(cur.fetchone())
            assistant = None
            if proof.output_message_id is not None:
                cur.execute(f"SELECT * FROM {settings.database.postgres_message_table} WHERE id=%s AND session_id=%s",
                    (proof.output_message_id, owner.binding.session_id))
                assistant = _message_from_row(cur.fetchone())
            return proof, snapshot, assistant
    return await asyncio.to_thread(load)

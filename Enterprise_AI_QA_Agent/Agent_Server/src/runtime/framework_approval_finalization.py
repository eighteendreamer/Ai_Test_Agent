"""Narrow approval-result recovery using the existing task and finalizer.

No upstream counterpart (checked: message finalizing recovery/part receipts,
approval reconciliation, v4 §9.4/§10.2): a native decision proof cannot grant
application writes. Keep this lineage outside the immutable result source.
"""
from __future__ import annotations

import json
import logging

from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import (
    FrameworkMessageStageRecoveryRequired, message_stage_table, validate_message_stage,
)
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_message_stage import FrameworkApprovalFinalizationProof
from src.schemas.runtime_binding import RuntimeBinding


logger = logging.getLogger(__name__)
SCOPE = "application_finalization"
OWNER_FIELDS = {"owner_attempt_id", "owner_epoch"}


def is_finalization_task(row):
    proof = (row["input_payload"] or {}).get("recovery_proof") or {}
    return isinstance(proof, dict) and proof.get("scope") == SCOPE


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def _stage(cur, settings, binding):
    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (binding.binding_id,))
    row = cur.fetchone()
    try:
        return validate_message_stage(row["record"]) if row is not None else None
    except FrameworkMessageStageRecoveryRequired as exc:
        raise FrameworkApprovalRecoveryRequired("Approval finalization stage requires reconciliation") from exc


def _material(cur, settings, binding, stage):
    from src.runtime.framework_runtime_results import validate_runtime_result_binding

    _require(stage is not None and stage.phase in {"runtime_submitted", "projected"},
        "Approval finalization has no original runtime submission")
    raw = (stage.source_manifest or {}).get("runtime_result_asset")
    _require(isinstance(raw, dict), "Approval finalization has no original result")
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s", (raw.get("task_id"),))
    producer = cur.fetchone()
    _require(producer is not None and producer["operation"] in {"approval_resume", "approval_recover"}
        and not is_finalization_task(producer) and producer["runtime_binding"] == binding.model_dump(mode="json")
        and producer["session_id"] == binding.session_id and producer["turn_id"] == binding.turn_id,
        "Approval finalization has no matching original producer")
    try:
        asset = validate_runtime_result_binding(cur, settings, binding, stage, producer, raw)
    except FrameworkMessageStageRecoveryRequired as exc:
        raise FrameworkApprovalRecoveryRequired("Approval finalization original result requires reconciliation") from exc
    return asset, producer


def _context(cur, settings, session, binding, stage, asset, origin, *, current=True):
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    metadata = session["metadata"] or {}
    pending = metadata.get("pending_turn") or {}
    _require(origin["operation"] in {"approval_resume", "approval_recover"}
        and origin["status"] in {"failed", "recovery_required"} and origin["attempt_id"]
        and not origin["interrupt_requested"] and origin["project_id"] == session["project_id"]
        and origin["runtime_binding"] == binding.model_dump(mode="json")
        and origin["session_id"] == binding.session_id and origin["turn_id"] == binding.turn_id,
        "Approval finalization origin no longer belongs to its recoverable Turn")
    if current:
        _require((not pending or (pending.get("turn_id") == binding.turn_id
            and (pending.get("context_bundle") or {}).get("runtime_binding") == binding.model_dump(mode="json")))
            and (metadata.get("control") or {}).get("last_interrupt_binding_id") != binding.binding_id,
            "Approval finalization no longer belongs to the active recoverable Turn")
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s", (stage.task_id,))
    initial = cur.fetchone()
    _require(initial is not None and initial["operation"] == "message"
        and initial["runtime_binding"] == binding.model_dump(mode="json")
        and initial["project_id"] == origin["project_id"], "Approval finalization lost its admitted message")
    if current:
        cur.execute(f"SELECT id FROM {settings.database.postgres_agent_task_table} WHERE session_id=%s AND "
            "((turn_id=%s AND interrupt_requested) OR (turn_id<>%s AND sequence>%s AND attempt_id IS NOT NULL)) LIMIT 1",
            (binding.session_id, binding.turn_id, binding.turn_id, initial["sequence"]))
        _require(cur.fetchone() is None, "Approval finalization was interrupted or overtaken by another Turn")
    budget = PostgresFrameworkModelCalls(settings)._policy(cur, binding.binding_id)
    _require(PostgresAgentTaskStore._v2_input_hash(initial["input_payload"], binding, budget) == stage.input_sha256
        and initial["input_hash"] == stage.input_sha256 and initial["user_message_id"] == stage.user_message_id,
        "Approval finalization admission or root budget changed")
    cur.execute(f"SELECT role,content,metadata FROM {settings.database.postgres_message_table} WHERE id=%s AND session_id=%s",
        (stage.user_message_id, binding.session_id))
    user = cur.fetchone()
    _require(user is not None and user["role"] == "user" and user["content"] == asset.user_message.strip()
        and (user["metadata"] or {}).get("turn_id") == binding.turn_id,
        "Approval finalization admitted message changed")
    return budget


def _parts(cur, settings, session, binding, stage, asset):
    from src.runtime.framework_application_projection import COMMIT_KEY, require_application_commit
    from src.runtime.framework_finalization_projection import require_partial_finalization

    if COMMIT_KEY in (stage.source_manifest or {}):
        require_application_commit(cur, settings, session, binding, stage)
    else:
        require_partial_finalization(cur, settings, session["metadata"] or {}, stage, asset)


def prepare_approval_finalization(cur, settings, session, binding, origin):
    stage = _stage(cur, settings, binding)
    if stage is None or "runtime_result_asset" not in (stage.source_manifest or {}):
        return None
    asset, producer = _material(cur, settings, binding, stage)
    previous = stage.approval_finalizations[-1] if stage.approval_finalizations else None
    expected = (previous.task_id, previous.owner_attempt_id, previous.owner_epoch) if previous else (
        producer["id"], asset.attempt_id, asset.owner_epoch)
    _require((origin["id"], origin["attempt_id"]) == expected[:2] and expected[2] is not None,
        "Approval finalization origin is not the latest projection predecessor")
    _require(stage.phase == "runtime_submitted", "Projected approval result requires settlement-only recovery")
    budget = _context(cur, settings, session, binding, stage, asset, origin)
    _parts(cur, settings, session, binding, stage, asset)
    return stage, asset, budget, expected[2]


def validate_finalization_link(cur, settings, row, *, current=False, adopting=False):
    binding = RuntimeBinding.model_validate(row["runtime_binding"])
    stage = _stage(cur, settings, binding)
    asset, producer = _material(cur, settings, binding, stage)
    proof = (row["input_payload"] or {}).get("recovery_proof") or {}
    FrameworkApprovalFinalizationProof.model_validate(proof)
    links = [link for link in stage.approval_finalizations if link.task_id == row["id"]]
    _require(proof.get("schema_version") == 2 and proof.get("scope") == SCOPE and len(links) == 1,
        "Approval finalization successor has no scoped admission")
    envelope = ("session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id", "root_trace_id")
    _require(all(row[field] == producer[field] for field in envelope)
        and row["input_hash"] == approval_value_hash(row["input_payload"])
        and row["input_payload"].get("approval") == producer["input_payload"].get("approval"),
        "Approval finalization successor changed its original task envelope")
    link = links[0]
    _require(adopting or link.owner_attempt_id is None or row["attempt_id"] == link.owner_attempt_id,
        "Approval finalization captured task attempt changed")
    _require(proof.get("finalization") == link.model_dump(mode="json", exclude=OWNER_FIELDS)
        and proof.get("origin_task_id") == link.predecessor_task_id
        and proof.get("origin_attempt_id") == link.predecessor_attempt_id
        and row["reference_id"] == producer["reference_id"]
        and row["input_payload"].get("decision_id") == producer["input_payload"].get("decision_id"),
        "Approval finalization successor differs from its frozen lineage")
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s", (link.predecessor_task_id,))
    origin = cur.fetchone()
    _require(origin is not None and origin["attempt_id"] == link.predecessor_attempt_id,
        "Approval finalization predecessor attempt changed")
    cur.execute(f"SELECT * FROM {settings.database.postgres_session_table} WHERE id=%s", (binding.session_id,))
    session = cur.fetchone()
    budget = _context(cur, settings, session, binding, stage, asset, origin, current=current)
    _require(link.budget_sha256 == approval_value_hash(budget.model_dump(mode="json")),
        "Approval finalization root budget changed after admission")
    return stage, asset, producer, link, session


def require_finalization_claim(cur, settings, row, owner):
    stage, asset, producer, link, session = validate_finalization_link(cur, settings, row, current=True)
    _require(stage.phase == "runtime_submitted" and stage.approval_finalizations[-1].task_id == row["id"]
        and owner["task_id"] in {row["parent_task_id"], row["id"]},
        "Approval finalization is not the current projection successor")
    if link.owner_attempt_id is not None:
        _require(row["status"] in {"claimed", "waiting_resource"} and link.owner_attempt_id == row["attempt_id"]
            and owner["task_id"] == row["id"] and owner["attempt_id"] == row["attempt_id"]
            and owner["owner_epoch"] == link.owner_epoch, "Approval finalization captured claim changed")
    _parts(cur, settings, session, asset.binding, stage, asset)


def _save(cur, settings, stage):
    stage = validate_message_stage(stage.model_dump(mode="json"))
    cur.execute(f"UPDATE {message_stage_table(settings)} SET record=%s::jsonb WHERE task_id=%s",
        (stage.model_dump_json(), stage.task_id))
    return stage


def adopt_finalization_owner(cur, settings, row, owner):
    from src.runtime.postgres_runtime_owner import assert_runtime_owner

    assert_runtime_owner(cur, settings, owner, states=("claimed",))
    stage, asset, producer, link, session = validate_finalization_link(cur, settings, row, current=True, adopting=True)
    _require(stage.approval_finalizations[-1].task_id == row["id"],
        "Approval finalization is no longer the current claim")
    adopted = link.model_copy(update={"owner_attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch})
    return _save(cur, settings, stage.model_copy(update={
        "approval_finalizations": [*stage.approval_finalizations[:-1], adopted]}))


def acknowledge_finalization(settings, owner, reference):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.framework_application_projection import require_application_commit
    from src.runtime.framework_runtime_results import lock_runtime_result_stage

    with postgres_connect(settings) as connection, connection.cursor() as cur:
        stage, task = lock_runtime_result_stage(cur, settings, owner)
        cur.execute(f"SELECT * FROM {settings.database.postgres_session_table} WHERE id=%s", (owner.binding.session_id,))
        session = cur.fetchone()
        proof = require_application_commit(cur, settings, session, owner.binding, stage)
        _require(reference == (proof.output_message_id or proof.snapshot_id), "Approval finalization ACK reference changed")
        saved = _save(cur, settings, stage.model_copy(update={"phase": "projected", "result_reference": reference}))
        cur.execute(f"UPDATE {settings.database.postgres_session_table} SET status='completed', "
            "metadata=jsonb_set(metadata,'{control}',COALESCE(metadata->'control','{}'::jsonb) || %s::jsonb), "
            "state_revision=state_revision+1,updated_at=clock_timestamp() WHERE id=%s",
            (json.dumps(proof.control), owner.binding.session_id))
    logger.info("framework_approval_finalization_projected", extra={"task_id": owner.task_id,
        "binding_id": owner.binding.binding_id, "result_reference": reference})
    return saved

"""Durable per-request Memory execution state and physical-row receipts.

The application commit freezes an immutable plan.  This module records only
derived execution facts in the same PostgreSQL transaction as each Memory row.
It deliberately does not create a task, lease, retry permission, or outbox.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_application_projection import (
    COMMIT_KEY,
    _hash,
    _require,
)
from src.runtime.framework_message_stages import (
    message_stage_table,
    validate_message_stage,
)
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_projection_commit import (
    FrameworkTurnMemoryExecution,
    FrameworkTurnMemoryPlan,
    FrameworkTurnMemoryReconciliation,
    FrameworkTurnMemoryRequest,
    FrameworkTurnMemoryRowReceipt,
    TURN_MEMORY_EXECUTION_KEY,
    TURN_MEMORY_PLAN_KEY,
)

if TYPE_CHECKING:
    from src.schemas.memory import MemoryWriteRequest
    from src.schemas.runtime_binding import RuntimeExecutionOwner


MEMORY_COLUMNS = (
    "id,scope,kind,content,summary,tags,session_id,turn_id,trace_id,source,stale,"
    "mode_key,metadata,embedding::text AS embedding,created_at,updated_at"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkTurnMemoryExecutionWrite:
    owner: "RuntimeExecutionOwner"
    request_id: str
    controlled_case_native: object | None = field(default=None, repr=False, compare=False)


def _save_stage(cur, settings, stage, values):
    from src.runtime.framework_projection_source import save_projection_manifest

    manifest = {**(stage.source_manifest or {}), **values}
    return save_projection_manifest(cur, settings, stage, manifest, message_version=27)


def _execution(stage, plan: FrameworkTurnMemoryPlan) -> FrameworkTurnMemoryExecution | None:
    raw = (stage.source_manifest or {}).get(TURN_MEMORY_EXECUTION_KEY)
    if raw is None:
        return None
    value = FrameworkTurnMemoryExecution.model_validate_json(json.dumps(raw, ensure_ascii=False))
    _require(
        value.effect_id == plan.effect_id
        and value.session_id == plan.session_id
        and value.turn_id == plan.turn_id
        and value.binding_fingerprint == plan.binding_fingerprint
        and value.runtime_result_asset_sha256 == plan.runtime_result_asset_sha256
        and value.source_snapshot_id == plan.source_snapshot_id
        and value.plan_asset_sha256 == plan.asset_sha256,
        "Memory execution receipt differs from its frozen plan",
    )
    by_id = {item.request_id: item for item in plan.requests}
    for receipt in value.row_receipts:
        item = by_id.get(receipt.request_id)
        _require(item is not None, "Memory execution receipt has an unknown request identity")
        _require(item.ordinal == receipt.ordinal, "Memory execution receipt ordinal differs from its plan")
    _require(
        value.state != "completed" or {item.request_id for item in value.row_receipts}
        == set(by_id),
        "Completed Memory execution is missing a row receipt",
    )
    return value


def _new_execution(plan: FrameworkTurnMemoryPlan, *, state: str = "running") -> FrameworkTurnMemoryExecution:
    values = {
        "schema_version": 1,
        "scope": "derived_turn_memory_execution_receipt_only",
        "effect_id": plan.effect_id,
        "session_id": plan.session_id,
        "turn_id": plan.turn_id,
        "binding_fingerprint": plan.binding_fingerprint,
        "runtime_result_asset_sha256": plan.runtime_result_asset_sha256,
        "source_snapshot_id": plan.source_snapshot_id,
        "plan_asset_sha256": plan.asset_sha256,
        "state": state,
        "row_receipts": [],
    }
    return FrameworkTurnMemoryExecution(**values, asset_sha256=approval_value_hash(values))


def _plan_from_stage(stage, owner) -> FrameworkTurnMemoryPlan:
    manifest = stage.source_manifest or {}
    _require(COMMIT_KEY in manifest, "Memory execution has no application commit")
    raw = manifest.get(TURN_MEMORY_PLAN_KEY)
    _require(raw is not None, "Memory execution has no frozen plan")
    plan = FrameworkTurnMemoryPlan.model_validate(raw)
    _require(
        plan.session_id == owner.binding.session_id
        and plan.turn_id == owner.binding.turn_id
        and plan.binding_fingerprint == owner.binding.fingerprint,
        "Memory execution plan binding differs from its owner",
    )
    return plan


def _lock_stage_and_plan(cur, settings, owner, *, controlled_case_native=None):
    if owner.binding.runtime_kind == "langgraph":
        from src.runtime.framework_projection_source import lock_result_source

        source, _ = lock_result_source(cur, settings, owner, controlled_case_native=controlled_case_native)
        return source, _plan_from_stage(source, owner)
    # Post-commit Memory may run before or after the stage ACK.  The generic
    # runtime-result lock intentionally accepts only runtime_submitted; this
    # derived effect instead needs the narrower owner/stage fence for both
    # runtime_submitted and projected without reopening graph execution.
    configure_runtime_owner_transaction(cur, settings)
    assert_runtime_owner(cur, settings, owner)
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE",
        (owner.task_id,))
    task = cur.fetchone()
    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (owner.binding.binding_id,))
    row = cur.fetchone()
    _require(row is not None, "Memory execution has no message stage")
    stage = validate_message_stage(row["record"])
    _require(task is not None and task["status"] == "running"
        and task["attempt_id"] == owner.attempt_id
        and task["runtime_binding"] == owner.binding.model_dump(mode="json")
        and stage.current_task_id == owner.task_id
        and stage.phase in {"runtime_submitted", "projected"},
        "Memory execution has no matching active execution owner")
    return stage, _plan_from_stage(stage, owner)


def _row_hash(row: dict) -> str:
    return _hash(dict(row))


def _request_by_id(plan: FrameworkTurnMemoryPlan, request_id: str) -> FrameworkTurnMemoryRequest:
    matches = [item for item in plan.requests if item.request_id == request_id]
    _require(len(matches) == 1, "Memory execution request is not part of the frozen plan")
    return matches[0]


def reconcile_turn_memory_execution(
    plan: FrameworkTurnMemoryPlan,
    execution: FrameworkTurnMemoryExecution | None,
    row_hashes: dict[str, str],
    *,
    effect_failure_observed: bool = False,
) -> FrameworkTurnMemoryReconciliation:
    """Classify durable Memory evidence without granting execution authority.

    ``row_hashes`` must contain the physical Memory rows keyed by their stable
    request-derived IDs.  A row without a matching receipt, a receipt without
    a matching row, or a changed row is drift rather than a missing request.
    Missing requests become ``unknown`` when a post-commit failure was
    observed (or when no execution receipt exists); they are ``partial`` only
    when the durable execution receipt proves a prefix of the plan.
    """
    expected = {item.request_id: item.ordinal for item in plan.requests}
    receipts = {item.request_id: item for item in (execution.row_receipts if execution else [])}
    drifted = set(row_hashes) - set(expected)
    drifted.update(set(receipts) - set(expected))
    committed: list[str] = []
    for request_id, receipt in receipts.items():
        if expected.get(request_id) != receipt.ordinal or receipt.memory_id != request_id:
            drifted.add(request_id)
            continue
        if row_hashes.get(receipt.memory_id) != receipt.row_sha256:
            drifted.add(request_id)
            continue
        committed.append(request_id)
    for row_id in set(row_hashes).intersection(expected) - set(receipts):
        drifted.add(row_id)

    committed.sort(key=lambda request_id: expected[request_id])
    missing = [request_id for request_id in expected if request_id not in receipts]
    if drifted:
        return FrameworkTurnMemoryReconciliation(
            effect_id=plan.effect_id,
            session_id=plan.session_id,
            turn_id=plan.turn_id,
            binding_fingerprint=plan.binding_fingerprint,
            runtime_result_asset_sha256=plan.runtime_result_asset_sha256,
            source_snapshot_id=plan.source_snapshot_id,
            plan_asset_sha256=plan.asset_sha256,
            status="drifted",
            request_ids=committed,
            missing_request_ids=missing,
            drifted_request_ids=sorted(drifted),
            failure_observed=effect_failure_observed,
            reason_code="row_evidence_drifted",
        )
    if not missing:
        if execution is None or execution.state != "completed":
            return FrameworkTurnMemoryReconciliation(
                effect_id=plan.effect_id,
                session_id=plan.session_id,
                turn_id=plan.turn_id,
                binding_fingerprint=plan.binding_fingerprint,
                runtime_result_asset_sha256=plan.runtime_result_asset_sha256,
                source_snapshot_id=plan.source_snapshot_id,
                plan_asset_sha256=plan.asset_sha256,
                status="drifted",
                request_ids=committed,
                failure_observed=effect_failure_observed,
                reason_code="execution_state_inconsistent",
                drifted_request_ids=committed,
            )
        return FrameworkTurnMemoryReconciliation(
            effect_id=plan.effect_id,
            session_id=plan.session_id,
            turn_id=plan.turn_id,
            binding_fingerprint=plan.binding_fingerprint,
            runtime_result_asset_sha256=plan.runtime_result_asset_sha256,
            source_snapshot_id=plan.source_snapshot_id,
            plan_asset_sha256=plan.asset_sha256,
            status="completed",
            request_ids=committed,
            failure_observed=effect_failure_observed,
            reason_code="all_rows_verified",
        )
    unknown = missing if effect_failure_observed or execution is None else []
    return FrameworkTurnMemoryReconciliation(
        effect_id=plan.effect_id,
        session_id=plan.session_id,
        turn_id=plan.turn_id,
        binding_fingerprint=plan.binding_fingerprint,
        runtime_result_asset_sha256=plan.runtime_result_asset_sha256,
        source_snapshot_id=plan.source_snapshot_id,
        plan_asset_sha256=plan.asset_sha256,
        status="unknown" if unknown else "partial",
        request_ids=committed,
        missing_request_ids=missing,
        unknown_request_ids=unknown,
        failure_observed=effect_failure_observed,
        reason_code=("effect_failure_observed" if effect_failure_observed
                      else "missing_execution_receipt" if execution is None
                      else "partial_rows_verified"),
    )


def read_turn_memory_reconciliation(owner, settings) -> FrameworkTurnMemoryReconciliation:
    """Read Memory effect evidence after the execution owner may be gone.

    This path verifies the immutable binding and attempt identity but does not
    require a live lease, running task, or worker token.  It is intentionally
    read-only: the returned status cannot claim a retry or compensation right.
    """
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id,status,attempt_id,runtime_binding FROM {settings.database.postgres_agent_task_table} "
            "WHERE id=%s",
            (owner.task_id,),
        )
        task = cur.fetchone()
        _require(task is not None, "Memory reconciliation task is missing")
        _require(
            task["attempt_id"] == owner.attempt_id
            and task["runtime_binding"] == owner.binding.model_dump(mode="json"),
            "Memory reconciliation task identity differs from its binding",
        )
        cur.execute(
            f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s",
            (owner.binding.binding_id,),
        )
        saved = cur.fetchone()
        _require(saved is not None, "Memory reconciliation stage is missing")
        stage = validate_message_stage(saved["record"])
        _require(stage.phase in {"runtime_submitted", "projected"},
                 "Memory reconciliation has no post-commit stage")
        plan = _plan_from_stage(stage, owner)
        execution = _execution(stage, plan)
        request_ids = [item.request_id for item in plan.requests]
        if request_ids:
            cur.execute(
                f"SELECT {MEMORY_COLUMNS} FROM {settings.database.postgres_memory_table} "
                "WHERE id=ANY(%s)",
                (request_ids,),
            )
            rows = {str(row["id"]): _row_hash(dict(row)) for row in cur.fetchall()}
        else:
            rows = {}
        cur.execute(
            f"SELECT 1 FROM {settings.database.postgres_event_table} "
            "WHERE session_id=%s AND type='session.post_commit_effect_failed' "
            "AND payload->>'effect_id'=%s AND payload->>'effect_kind'='turn_memory' "
            "AND payload->>'status'='unknown' LIMIT 1",
            (plan.session_id, plan.effect_id),
        )
        failure_observed = cur.fetchone() is not None
        result = reconcile_turn_memory_execution(
            plan, execution, rows, effect_failure_observed=failure_observed,
        )
    LOGGER.log(
        logging.WARNING if result.status in {"unknown", "drifted"} else logging.INFO,
        "framework_turn_memory_reconciliation_read",
        extra={
            "session_id": result.session_id,
            "turn_id": result.turn_id,
            "effect_id": result.effect_id,
            "status": result.status,
            "reason_code": result.reason_code,
            "request_count": len(result.request_ids),
            "missing_count": len(result.missing_request_ids),
        },
    )
    return result


async def read_turn_memory_reconciliation_async(owner, settings) -> FrameworkTurnMemoryReconciliation:
    return await asyncio.to_thread(read_turn_memory_reconciliation, owner, settings)


def prepare_memory_row_projection(cur, settings, execution: FrameworkTurnMemoryExecutionWrite,
    request: "MemoryWriteRequest", memory_id: str):
    """Validate the immutable request and lock its physical row before writing."""
    stage, plan = _lock_stage_and_plan(cur, settings, execution.owner, controlled_case_native=execution.controlled_case_native)
    item = _request_by_id(plan, execution.request_id)
    normalized = request.model_copy(update={
        "turn_lease_token": None,
        "compaction_key": None,
        "compaction_owner_id": None,
    })
    _require(normalized == item.request, "Memory execution request differs from its frozen plan")
    _require(memory_id == execution.request_id, "Memory row identity is not derived from its request")
    receipt = _execution(stage, plan)
    if receipt is None:
        receipt = _new_execution(plan, state="running")
        stage = _save_stage(cur, settings, stage, {TURN_MEMORY_EXECUTION_KEY: receipt.model_dump(mode="json")})
    cur.execute(
        f"SELECT {MEMORY_COLUMNS} FROM {settings.database.postgres_memory_table} WHERE id=%s FOR UPDATE",
        (memory_id,),
    )
    row = cur.fetchone()
    if receipt is not None:
        prior = next((value for value in receipt.row_receipts
            if value.request_id == execution.request_id), None)
        if prior is not None:
            _require(row is not None and _row_hash(dict(row)) == prior.row_sha256,
                "Memory row differs from its original execution receipt")
        else:
            _require(row is None, "Existing Memory row has no atomic execution receipt")
    return stage, plan, receipt, (dict(row) if row is not None else None)


def record_memory_row_projection(cur, settings, execution: FrameworkTurnMemoryExecutionWrite,
    stage, plan: FrameworkTurnMemoryPlan, receipt: FrameworkTurnMemoryExecution,
    row: dict, *, memory_id: str, recorded_at: datetime | None = None):
    """Record the actual row and receipt in the same SQL transaction."""
    _require(row.get("id") == memory_id, "Memory row identity changed during write")
    existing = {item.request_id: item for item in receipt.row_receipts}
    digest = _row_hash(row)
    if execution.request_id in existing:
        _require(existing[execution.request_id].memory_id == memory_id
            and existing[execution.request_id].row_sha256 == digest,
            "Memory row differs from its original execution receipt")
        return receipt
    item = _request_by_id(plan, execution.request_id)
    recorded = recorded_at or datetime.now(timezone.utc)
    row_receipt = FrameworkTurnMemoryRowReceipt(
        request_id=execution.request_id,
        ordinal=item.ordinal,
        memory_id=memory_id,
        row_sha256=digest,
        recorded_at=recorded,
    )
    rows = [*receipt.row_receipts, row_receipt]
    rows.sort(key=lambda value: value.ordinal)
    state = "completed" if {value.request_id for value in rows} == {value.request_id for value in plan.requests} else "running"
    values = receipt.model_dump(mode="python", exclude={"asset_sha256"})
    values.update(state=state, row_receipts=[value.model_dump(mode="python") for value in rows])
    json_values = receipt.model_dump(mode="json", exclude={"asset_sha256"})
    json_values.update(state=state, row_receipts=[value.model_dump(mode="json") for value in rows])
    updated = FrameworkTurnMemoryExecution(**values, asset_sha256=approval_value_hash(json_values))
    _save_stage(cur, settings, stage, {TURN_MEMORY_EXECUTION_KEY: updated.model_dump(mode="json")})
    return updated


def read_turn_memory_execution(owner, settings, *, controlled_case_native=None) -> FrameworkTurnMemoryExecution | None:
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        stage, plan = _lock_stage_and_plan(cur, settings, owner, controlled_case_native=controlled_case_native)
        receipt = _execution(stage, plan)
        if receipt is not None and receipt.row_receipts:
            ids = [item.memory_id for item in receipt.row_receipts]
            cur.execute(f"SELECT {MEMORY_COLUMNS} FROM {settings.database.postgres_memory_table} "
                "WHERE id=ANY(%s) FOR UPDATE", (ids,))
            rows = {row["id"]: _row_hash(dict(row)) for row in cur.fetchall()}
            _require(all(rows.get(item.memory_id) == item.row_sha256 for item in receipt.row_receipts),
                "Memory execution rows changed or disappeared")
        return receipt


async def read_turn_memory_execution_async(owner, settings, *, controlled_case_native=None):
    return await asyncio.to_thread(read_turn_memory_execution, owner, settings, controlled_case_native=controlled_case_native)


def ensure_empty_turn_memory_execution(owner, settings, *, controlled_case_native=None):
    """Persist a completed receipt for a valid empty requested plan."""
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        stage, plan = _lock_stage_and_plan(cur, settings, owner, controlled_case_native=controlled_case_native)
        _require(not plan.requests, "Only an empty Memory plan can be completed without rows")
        current = _execution(stage, plan)
        if current is not None:
            _require(current.state == "completed", "Empty Memory execution is not complete")
            return current
        receipt = _new_execution(plan, state="completed")
        _save_stage(cur, settings, stage, {TURN_MEMORY_EXECUTION_KEY: receipt.model_dump(mode="json")})
        return receipt

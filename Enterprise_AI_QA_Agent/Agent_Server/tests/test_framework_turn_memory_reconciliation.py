from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.runtime.framework_turn_memory_projection import (
    _row_hash,
    read_turn_memory_reconciliation,
    reconcile_turn_memory_execution,
)
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_projection_commit import (
    FrameworkTurnMemoryExecution,
    FrameworkTurnMemoryPlan,
    FrameworkTurnMemoryRequest,
    FrameworkTurnMemoryRowReceipt,
    post_commit_effect_id,
    turn_memory_request_id,
)
from src.schemas.memory import MemoryWriteRequest
from src.schemas.runtime_binding import RuntimeBinding, RuntimeExecutionOwner


def _plan(count: int = 2) -> FrameworkTurnMemoryPlan:
    session_id = "session-memory-reconcile"
    turn_id = "turn-memory-reconcile"
    source_snapshot_id = "snapshot-memory-reconcile"
    effect_id = post_commit_effect_id(session_id, turn_id, source_snapshot_id, "turn_memory")
    requests = []
    for ordinal in range(count):
        request = MemoryWriteRequest(
            scope="session",
            kind="episodic",
            content=f"memory-{ordinal}",
            summary=f"summary-{ordinal}",
            tags=["fixture"],
            session_id=session_id,
            turn_id=turn_id,
            trace_id="trace-memory-reconcile",
        )
        requests.append(FrameworkTurnMemoryRequest(
            request_id=turn_memory_request_id(effect_id, ordinal),
            ordinal=ordinal,
            request=request,
        ))
    values = {
        "schema_version": 1,
        "scope": "derived_turn_memory_plan_only",
        "derivation_contract_version": 1,
        "effect_id": effect_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "binding_fingerprint": "a" * 64,
        "runtime_result_asset_sha256": "b" * 64,
        "source_snapshot_id": source_snapshot_id,
        "requested": bool(requests),
        "requests": [item.model_dump(mode="json") for item in requests],
    }
    values["asset_sha256"] = approval_value_hash(values)
    return FrameworkTurnMemoryPlan.model_validate(values)


def _execution(plan: FrameworkTurnMemoryPlan, request_ids: list[str], *, state: str = "running"):
    recorded_at = datetime(2026, 10, 29, tzinfo=timezone.utc)
    receipts = [
        FrameworkTurnMemoryRowReceipt(
            request_id=request_id,
            ordinal=ordinal,
            memory_id=request_id,
            row_sha256=chr(ord("c") + ordinal) * 64,
            recorded_at=recorded_at,
        )
        for ordinal, request_id in enumerate(request_ids)
    ]
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
        "row_receipts": [item.model_dump(mode="json") for item in receipts],
    }
    values["asset_sha256"] = approval_value_hash(values)
    return FrameworkTurnMemoryExecution.model_validate(values)


def _rows(request_ids: list[str]) -> dict[str, str]:
    return {
        request_id: chr(ord("c") + ordinal) * 64
        for ordinal, request_id in enumerate(request_ids)
    }


def test_reconciliation_reports_known_completion_only_for_all_verified_rows():
    plan = _plan()
    ids = [item.request_id for item in plan.requests]
    result = reconcile_turn_memory_execution(plan, _execution(plan, ids, state="completed"), _rows(ids))

    assert result.status == "completed"
    assert result.request_ids == ids
    assert result.missing_request_ids == []
    assert result.unknown_request_ids == []
    assert result.drifted_request_ids == []


def test_reconciliation_reports_known_partial_rows_without_granting_completion():
    plan = _plan()
    first = plan.requests[0].request_id
    result = reconcile_turn_memory_execution(plan, _execution(plan, [first]), _rows([first]))

    assert result.status == "partial"
    assert result.request_ids == [first]
    assert result.missing_request_ids == [plan.requests[1].request_id]
    assert result.unknown_request_ids == []


def test_reconciliation_reports_unknown_effect_when_failure_observation_has_missing_rows():
    plan = _plan()
    first = plan.requests[0].request_id
    result = reconcile_turn_memory_execution(
        plan,
        _execution(plan, [first]),
        _rows([first]),
        effect_failure_observed=True,
    )

    assert result.status == "unknown"
    assert result.unknown_request_ids == [plan.requests[1].request_id]
    assert result.missing_request_ids == [plan.requests[1].request_id]


@pytest.mark.parametrize("rows", [
    {"unexpected": "d" * 64},
    {},
])
def test_reconciliation_reports_drift_instead_of_inventing_a_missing_or_completed_row(rows):
    plan = _plan()
    first = plan.requests[0].request_id
    result = reconcile_turn_memory_execution(plan, _execution(plan, [first]), rows)

    assert result.status == "drifted"
    assert result.drifted_request_ids


def _owner():
    binding = RuntimeBinding(
        binding_id="binding-memory-reconcile",
        session_id="session-memory-reconcile",
        turn_id="turn-memory-reconcile",
        runtime_kind="deepagents",
        runtime_version="fixture",
        state_schema_version=1,
        thread_id="turn-memory-reconcile",
        checkpoint_namespace="namespace-memory-reconcile",
        prompt_version="prompt",
        skill_version="skills",
        tool_schema_version="tools",
        model_config_fingerprint="model",
        dependency_lock_sha256="d" * 64,
    )
    return RuntimeExecutionOwner(
        binding=binding,
        owner_epoch=1,
        owner_token="expired-token",
        task_id="task-memory-reconcile",
        attempt_id="attempt-memory-reconcile",
        worker_id="worker-memory-reconcile",
    )


class _Cursor:
    def __init__(self, task, stage, rows, failure):
        self.task = task
        self.stage = stage
        self.rows = rows
        self.failure = failure
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=()):
        if "_message_stage" in statement:
            self._result = self.stage
        elif "postgres_agent_task" in statement:
            self._result = self.task
        elif "post_commit_effect_failed" in statement:
            self._result = self.failure
        else:
            self._result = self.rows

    def fetchone(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self):
        return self.rows if isinstance(self._result, list) else []


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


def test_read_reconciliation_works_after_terminal_task_without_live_owner(monkeypatch):
    plan = _plan(1)
    request_id = plan.requests[0].request_id
    row = {"id": request_id, "scope": "session", "content": "durable"}
    receipt = _execution(plan, [request_id], state="completed")
    receipt = receipt.model_copy(update={
        "row_receipts": [receipt.row_receipts[0].model_copy(update={"row_sha256": _row_hash(row)})],
    })
    receipt = receipt.model_copy(update={
        "asset_sha256": approval_value_hash(receipt.model_dump(mode="json", exclude={"asset_sha256"}))
    })
    owner = _owner()
    task = {
        "id": owner.task_id,
        "status": "completed",
        "attempt_id": owner.attempt_id,
        "runtime_binding": owner.binding.model_dump(mode="json"),
    }
    cursor = _Cursor(task, {"record": {}}, [row], None)
    settings = SimpleNamespace(database=SimpleNamespace(
        postgres_agent_task_table="postgres_agent_task",
        postgres_memory_table="postgres_memory",
        postgres_event_table="postgres_event",
    ))
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection.postgres_connect",
                        lambda _settings: _Connection(cursor))
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection.validate_message_stage",
                        lambda record: SimpleNamespace(phase="projected", source_manifest={}))
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection._plan_from_stage",
                        lambda _stage, _owner: plan)
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection._execution",
                        lambda _stage, _plan: receipt)
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection._lock_stage_and_plan",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("read-only reconciliation must not require a live owner")))

    result = read_turn_memory_reconciliation(owner, settings)

    assert result.status == "completed"
    assert result.request_ids == [request_id]


def test_read_reconciliation_promotes_missing_rows_to_unknown_from_failure_event(monkeypatch):
    plan = _plan(2)
    first = plan.requests[0].request_id
    row = {"id": first, "scope": "session", "content": "durable"}
    receipt = _execution(plan, [first])
    receipt = receipt.model_copy(update={
        "row_receipts": [receipt.row_receipts[0].model_copy(update={"row_sha256": _row_hash(row)})],
    })
    receipt = receipt.model_copy(update={
        "asset_sha256": approval_value_hash(receipt.model_dump(mode="json", exclude={"asset_sha256"}))
    })
    owner = _owner()
    task = {
        "id": owner.task_id,
        "status": "recovery_required",
        "attempt_id": owner.attempt_id,
        "runtime_binding": owner.binding.model_dump(mode="json"),
    }
    cursor = _Cursor(task, {"record": {}}, [row], {"present": 1})
    settings = SimpleNamespace(database=SimpleNamespace(
        postgres_agent_task_table="postgres_agent_task",
        postgres_memory_table="postgres_memory",
        postgres_event_table="postgres_event",
    ))
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection.postgres_connect",
                        lambda _settings: _Connection(cursor))
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection.validate_message_stage",
                        lambda record: SimpleNamespace(phase="projected", source_manifest={}))
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection._plan_from_stage",
                        lambda _stage, _owner: plan)
    monkeypatch.setattr("src.runtime.framework_turn_memory_projection._execution",
                        lambda _stage, _plan: receipt)

    result = read_turn_memory_reconciliation(owner, settings)

    assert result.status == "unknown"
    assert result.unknown_request_ids == [plan.requests[1].request_id]
    assert result.reason_code == "effect_failure_observed"


@pytest.mark.asyncio
async def test_memory_runtime_service_exposes_read_only_reconciliation(monkeypatch):
    from src.application.context.memory_runtime_service import MemoryRuntimeService

    settings = object()
    store = SimpleNamespace(_settings=settings)
    service = MemoryRuntimeService(store)
    expected = object()
    calls = []

    async def read(owner, actual_settings):
        calls.append((owner, actual_settings))
        return expected

    monkeypatch.setattr(
        "src.runtime.framework_turn_memory_projection.read_turn_memory_reconciliation_async",
        read,
    )
    owner = object()

    assert await service.reconcile_turn_memory(owner) is expected
    assert calls == [(owner, settings)]

from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from threading import Event

import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.task_deferred import TaskDeferred
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision
from tests.test_framework_approval_runtime import runtime_harness, fresh_runtime_harness
from tests.test_framework_approval_graph_input import (
    prepared_approval_runtime, frozen_approval_result, queue_approval_finalization,
)
from tests.test_framework_approval_settlement import projected_approval, settlement_successor
from tests.test_framework_approval_reader_admission import database_state, dispatcher, deliver
from tests.test_framework_approval_recovery import expire, run_delivery, outbox


@pytest_asyncio.fixture(autouse=True)
async def assert_checkpoint_isolation(checkpoint_harness):
    # The same dependency orders the graph fixtures after checkpoint isolation.
    assert checkpoint_harness.settings.deep_agents.checkpoint_schema.startswith("fence_test_")


def change_proof(h, successor, change):
    proof = deepcopy(successor.input["recovery_proof"])
    change(proof)
    payload = {**successor.input, "recovery_proof": proof}
    # Preserve issuance equality/hash so the sample exercises reader admission,
    # not merely a changed parent-child identity or input digest.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=%s::jsonb,input_hash=%s WHERE id=%s",
            (json.dumps(payload), approval_value_hash(payload), successor.id))
        cur.execute(f"UPDATE {h.tasks._tasks} SET result_payload=jsonb_set(result_payload,'{{proof}}',%s::jsonb) WHERE id=%s",
            (json.dumps(proof), successor.parent_task_id))


async def assert_unavailable(h, successor, change):
    change_proof(h, successor, change)
    before = database_state(h)
    selected = dispatcher(h)
    with pytest.raises(TaskDeferred, match="framework_recovery_proof_format_unavailable"):
        await deliver(h, selected, successor)
    assert database_state(h) == before and h.executions == []


def future_format(proof, change):
    if change == "lineage_field":
        field = "finalization" if proof["scope"] == "application_finalization" else "settlement"
        proof[field]["future_field"] = "private-lineage-input"
    else:
        proof[change] = {"schema_version": 99, "scope": "future_application_scope",
            "future_field": "private-proof-input"}[change]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["schema_version", "scope", "future_field", "lineage_field"])
async def test_future_finalization_proof_does_not_change_queued_successor(frozen_approval_result, change, caplog):
    h = frozen_approval_result
    _, successor = await queue_approval_finalization(h)
    await assert_unavailable(h, successor, lambda proof: future_format(proof, change))
    records = [row for row in caplog.records if row.message == "framework_recovery_proof_reader_unavailable"]
    assert len(records) == 1 and records[0].task_id == successor.id
    assert records[0].binding_id == h.binding.binding_id and records[0].worker_id == "approval-format-reader"
    assert records[0].reason == "framework_recovery_proof_format_unavailable" and records[0].exc_info is None
    assert "private-proof-input" not in caplog.text and "private-lineage-input" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["schema_version", "scope", "future_field", "lineage_field"])
async def test_future_settlement_proof_does_not_change_queued_successor(projected_approval, change):
    h = projected_approval
    _, successor = await settlement_successor(h)
    await assert_unavailable(h, successor, lambda proof: future_format(proof, change))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["claimed", "running", "cancelled"])
async def test_future_proof_is_checked_before_expired_attempt_or_terminal_return(frozen_approval_result, status):
    h = frozen_approval_result
    _, successor = await queue_approval_finalization(h)
    owner = await h.tasks.claim(successor.id, worker_id="original-proof-owner", expected_binding=h.binding)
    if status == "running":
        await h.tasks.begin(owner, resource_id="proof-format", fencing_token=3)
    if status == "cancelled":
        await h.tasks.finish(owner, status="cancelled", error_code="test_fixture_cancelled")
    else:
        assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
    await assert_unavailable(h, successor, lambda proof: proof.update(schema_version=99))


@pytest.mark.asyncio
async def test_proof_format_is_read_after_waiting_for_session_lock(frozen_approval_result, monkeypatch):
    h = frozen_approval_result
    _, successor = await queue_approval_finalization(h)
    selected = dispatcher(h)
    reached, original = Event(), h.tasks._lock_session

    def lock(cur, session_id):
        reached.set()
        return original(cur, session_id)

    monkeypatch.setattr(h.tasks, "_lock_session", lock)
    pending = None
    try:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {h.tasks._session_table} WHERE id=%s FOR UPDATE", (h.session_id,))
            pending = asyncio.create_task(deliver(h, selected, successor))
            assert await asyncio.to_thread(reached.wait, 5)
            change_proof(h, successor, lambda proof: proof.update(schema_version=99))
            before = database_state(h)
        with pytest.raises(TaskDeferred, match="framework_recovery_proof_format_unavailable"):
            await pending
        assert database_state(h) == before and h.executions == []
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_compatible_finalization_proof_still_requires_original_authority(frozen_approval_result):
    h = frozen_approval_result
    _, successor = await queue_approval_finalization(h)
    await deliver(h, dispatcher(h), successor)
    claimed = await h.tasks.get(successor.id)
    assert claimed.status == "claimed" and claimed.attempt_no == 1 and h.executions == [successor.id]


@pytest.mark.asyncio
async def test_compatible_settlement_proof_still_requires_original_authority(projected_approval):
    h = projected_approval
    _, successor = await settlement_successor(h)
    await deliver(h, dispatcher(h), successor)
    claimed = await h.tasks.get(successor.id)
    assert claimed.status == "claimed" and claimed.attempt_no == 1 and h.executions == [successor.id]


@pytest.mark.asyncio
async def test_supported_format_does_not_authorize_a_different_lineage(frozen_approval_result):
    h = frozen_approval_result
    _, successor = await queue_approval_finalization(h)
    change_proof(h, successor, lambda proof: proof["finalization"].update(predecessor_attempt_id="different-attempt"))
    await deliver(h, dispatcher(h), successor)
    rejected = await h.tasks.get(successor.id)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0 and h.executions == []


@pytest.mark.asyncio
async def test_known_proof_value_error_is_not_a_compatibility_deferral(frozen_approval_result):
    h = frozen_approval_result
    _, successor = await queue_approval_finalization(h)
    change_proof(h, successor, lambda proof: proof["finalization"].update(predecessor_owner_epoch=-1))
    before = database_state(h)
    with pytest.raises(ValidationError):
        await deliver(h, dispatcher(h), successor)
    assert database_state(h) == before and h.executions == []


async def native_successor(h):
    origin = await claim_decision(h)
    await expire(h, origin)
    reconciler = await h.tasks.ensure_framework_reconciliation(origin.id)
    await run_delivery(h, reconciler.id)
    reconciled = await h.tasks.get(reconciler.id)
    assert reconciled.status == "completed", reconciled.error_code
    successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
    assert "scope" not in successor.input["recovery_proof"]
    return successor


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["schema_version", "scope"])
async def test_unknown_native_proof_declaration_does_not_change_task(runtime_harness, change):
    h = runtime_harness
    successor = await native_successor(h)
    await assert_unavailable(h, successor, lambda proof: future_format(proof, change))


@pytest.mark.asyncio
async def test_native_free_metadata_is_not_replaced_with_a_strict_field_whitelist(runtime_harness):
    h = runtime_harness
    successor = await native_successor(h)
    change_proof(h, successor, lambda proof: proof.update(metadata={"schema_version": 99, "future_field": True}))
    await deliver(h, dispatcher(h), successor)
    claimed = await h.tasks.get(successor.id)
    assert claimed.status == "claimed" and claimed.attempt_no == 1 and h.executions == [successor.id]


@pytest.mark.asyncio
async def test_absent_native_proof_still_fails_original_issuance_validation(runtime_harness):
    h = runtime_harness
    successor = await native_successor(h)
    payload = {key: value for key, value in successor.input.items() if key != "recovery_proof"}
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=%s::jsonb,input_hash=%s WHERE id=%s",
            (json.dumps(payload), approval_value_hash(payload), successor.id))
    await deliver(h, dispatcher(h), successor)
    rejected = await h.tasks.get(successor.id)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0 and h.executions == []


@pytest.mark.asyncio
async def test_unknown_proof_keeps_real_redis_delivery_pending_without_retry(projected_approval):
    h = projected_approval
    _, successor = await settlement_successor(h)
    change_proof(h, successor, lambda proof: proof.update(schema_version=99))
    selected = dispatcher(h)
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.prefix + ":proof-format",
        group="proof-format")
    await queue.connect()
    received = []

    async def handle(delivery):
        received.append(delivery.retry_count)
        await selected.handle_v2(delivery, worker_id="proof-format-worker")

    try:
        await queue.enqueue(outbox(h, successor.id)["payload"])
        before = database_state(h)
        entries = await h.redis.xrange(queue.stream)
        assert await RedisTaskWorker(queue, consumer="proof-format-worker", handler=handle, block_ms=1).run_once() == 0
        assert received == [0] and await h.redis.xrange(queue.stream) == entries
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xlen(queue.stream + ":dead_letter") == 0
        assert database_state(h) == before and h.executions == []
    finally:
        await queue.close()

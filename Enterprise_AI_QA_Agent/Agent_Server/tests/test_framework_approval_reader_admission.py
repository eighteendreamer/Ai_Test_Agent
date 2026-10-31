from __future__ import annotations

import hashlib
import json
import asyncio
from threading import Event

import pytest
import pytest_asyncio
from psycopg import sql
from pydantic import ValidationError

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_submission import approval_harness, submit
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, run_plan, service, receipt
from tests.test_framework_approval_runtime import runtime_harness, resume


@pytest_asyncio.fixture(autouse=True)
async def assert_checkpoint_isolation(checkpoint_harness):
    # Resolve and verify isolation before explicit approval fixtures open savers.
    assert checkpoint_harness.settings.deep_agents.checkpoint_schema.startswith("fence_test_")


def database_state(h):
    # Compare all original business/journal/native rows without leaking tokens
    # or serialized checkpoint input into a failing assertion.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        tables = [("public", name) for name in h.tables]
        if hasattr(h, "job_store"):
            tables.extend(("public", name) for name in (
                h.settings.database.postgres_tool_job_table, h.settings.database.postgres_tool_artifact_table))
        schema = h.settings.deep_agents.checkpoint_schema
        assert schema.startswith("fence_test_")
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,))
        tables.extend((schema, row["tablename"]) for row in cur.fetchall())
        result = {}
        for schema, table in tables:
            cur.execute(sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
            rows = sorted(json.dumps(row, sort_keys=True, default=str) for row in cur.fetchall())
            result[f"{schema}.{table}"] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
        return result


def dispatcher(h):
    selected = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
        framework_capabilities=FrameworkWorkerCapabilities(h.binding.dependency_lock_sha256, True))
    h.executions = []

    async def execute(task):
        h.executions.append(task.id)

    selected._execute = execute
    return selected


async def deliver(h, selected, task):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{task.id}",))
        payload = cur.fetchone()["payload"]
    await selected.handle_v2(QueuedTask("approval-format", payload), worker_id="approval-format-reader")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "claimed", "running", "cancelled"])
@pytest.mark.parametrize("field,value", [("future_field", "private-approval-diagnostic"),
    ("format_version", "langgraph-next")])
async def test_future_interrupt_format_defers_before_changing_original_task(approval_harness, status, field, value, caplog):
    h = approval_harness
    task = await submit(h)
    if status != "queued":
        task = await h.tasks.claim(task.id, worker_id="original-approval", expected_binding=h.binding)
        if status == "running":
            await h.tasks.begin(task, resource_id="approval-format", fencing_token=2)
        if status == "cancelled":
            await h.tasks.finish(task, status="cancelled", error_code="test_fixture_cancelled")
        else:
            assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=jsonb_set(link,%s,%s::jsonb) WHERE approval_id=%s",
            ([field], json.dumps(value), task.reference_id))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_approval_link_format_unavailable"):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == []
    records = [row for row in caplog.records if row.message == "framework_approval_reader_unavailable"]
    assert len(records) == 1 and records[0].task_id == task.id
    assert records[0].binding_id == h.binding.binding_id and records[0].worker_id == "approval-format-reader"
    assert records[0].reason == "framework_approval_link_format_unavailable" and records[0].exc_info is None
    assert "private-approval-diagnostic" not in caplog.text


@pytest.mark.asyncio
async def test_future_receipt_column_defers_before_changing_queued_task(approval_harness):
    h = approval_harness
    task = await submit(h)
    # SELECT * is the installed receipt reader's contract. A future DDL column
    # breaks extra=forbid even though the stored decision JSON did not change.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"ALTER TABLE {h.tasks._tasks}_decision ADD COLUMN future_field TEXT")
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_approval_receipt_format_unavailable"):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [["format_version"], ["application", "format_version"]])
async def test_future_native_proof_format_defers_before_expired_running_transition(continuation_harness, path):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(task.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    await service(h).settle(task.runtime_owner, task.reference_id, task.input["decision_id"])
    original = receipt(h)
    assert original.state == "delivered" and original.applied_at is not None and len(h.effects) == 1
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_decision SET proof=jsonb_set(proof,%s,%s::jsonb) WHERE approval_id=%s",
            (path, json.dumps("langgraph-next"), task.reference_id))
    before, effects = database_state(h), list(h.effects)
    with pytest.raises(TaskDeferred, match="framework_approval_receipt_format_unavailable"):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == [] and h.effects == effects


@pytest.mark.asyncio
async def test_unreadable_sibling_interrupt_is_checked_for_the_same_binding(approval_harness):
    h = approval_harness
    task = await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=link || %s::jsonb WHERE approval_id=%s",
            (json.dumps({"future_field": "private-sibling-input"}), h.links["A"].link.approval_id))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_approval_link_format_unavailable"):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == []


@pytest.mark.asyncio
async def test_approval_format_is_rechecked_after_waiting_for_session_lock(approval_harness, monkeypatch):
    h = approval_harness
    task = await submit(h)
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
            pending = asyncio.create_task(deliver(h, selected, task))
            assert await asyncio.to_thread(reached.wait, 5)
            with postgres_connect(h.settings) as writer, writer.cursor() as update:
                update.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=link || %s::jsonb WHERE approval_id=%s",
                    (json.dumps({"future_field": "private-race-input"}), task.reference_id))
            before = database_state(h)
        with pytest.raises(TaskDeferred, match="framework_approval_link_format_unavailable"):
            await pending
        assert database_state(h) == before and h.executions == h.native.calls == []
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_known_interrupt_validation_error_is_not_a_compatibility_wait(approval_harness):
    h = approval_harness
    task = await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=jsonb_set(link,'{{resume_index}}','-1') WHERE approval_id=%s",
            (task.reference_id,))
    before = database_state(h)
    with pytest.raises(ValidationError):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == []


@pytest.mark.asyncio
async def test_compatible_receipt_still_uses_original_claim_authority(approval_harness):
    h = approval_harness
    task = await submit(h)
    await deliver(h, dispatcher(h), task)
    current = await h.tasks.get(task.id)
    assert current.status == "claimed" and current.attempt_no == 1 and h.executions == [task.id]
    assert h.native.calls == []


@pytest.mark.asyncio
async def test_format_compatibility_does_not_authorize_a_changed_business_identity(approval_harness):
    h = approval_harness
    task = await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=jsonb_set(link,'{{tool_job_id}}',%s::jsonb) WHERE approval_id=%s",
            (json.dumps("different-job"), task.reference_id))
    await deliver(h, dispatcher(h), task)
    current = await h.tasks.get(task.id)
    assert current.status == "recovery_required" and current.attempt_no == 0
    assert h.executions == h.native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["schema_version", "future_field", "digest"])
async def test_original_rejection_projection_is_read_before_expired_running_mutation(runtime_harness, change):
    from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY

    h = runtime_harness
    session = await h.store.get_session(h.session_id)
    for state in (session.metadata["pending_turn"], session.metadata["pending_turn"]["graph_state"]):
        state["context_bundle"]["framework_tool_projection_version"] = 2
    await h.store.save_session(session)
    task = await claim_decision(h, decision="denied")
    await resume(h, task)
    saved = receipt(h)
    assert saved.applied_at is not None and TOOL_PROJECTION_KEY in saved.proof["application"]
    assert h.effects == []
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    field = change if change != "digest" else "model_content"
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_decision SET proof=jsonb_set(proof,%s,%s::jsonb) WHERE approval_id=%s",
            (["application", TOOL_PROJECTION_KEY, field], json.dumps(99 if change == "schema_version" else "private-input"),
                task.reference_id))
    before = database_state(h)
    expected = ValidationError if change == "digest" else TaskDeferred
    with pytest.raises(expected):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == h.effects == []


@pytest.mark.asyncio
async def test_unreadable_approval_journal_is_also_deferred_for_reconciliation(runtime_harness):
    h = runtime_harness
    original = await claim_decision(h)
    assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
    expired = await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
    assert expired.status == "recovery_required"
    task = await h.tasks.ensure_framework_reconciliation(original.id)
    assert task.operation == "framework_reconcile"
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=link || %s::jsonb WHERE approval_id=%s",
            (json.dumps({"future_field": "private-reconciliation-input"}), original.reference_id))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_approval_link_format_unavailable"):
        await deliver(h, dispatcher(h), task)
    assert database_state(h) == before and h.executions == h.native.calls == h.effects == []


@pytest.mark.asyncio
async def test_unknown_journal_format_keeps_original_redis_delivery_pending(approval_harness):
    h = approval_harness
    task = await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_interrupt SET link=link || %s::jsonb WHERE approval_id=%s",
            (json.dumps({"future_field": "private-pending-input"}), task.reference_id))
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="approval-reader", client=h.redis)
    await queue.connect()
    # The fixture also has the terminal initial message outbox. Both entries
    # share this binding, so neither may pass an unreadable journal format.
    assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 2
    before = database_state(h)
    original_entries = await h.redis.xrange(queue.stream)
    selected = dispatcher(h)
    received = []

    async def handle(item):
        received.append(item)
        await selected.handle_v2(item, worker_id="approval-format-reader")

    worker = RedisTaskWorker(queue, consumer="approval-format-reader", handler=handle, batch_size=2, block_ms=0)
    assert await worker.run_once() == 0
    pending = await queue.pending()
    assert len(pending) == 2
    assert all(item["consumer"] == "approval-format-reader" for item in pending)
    entries = await h.redis.xrange(queue.stream)
    assert entries == original_entries and len(received) == 2
    assert all(item.retry_count == 0 for item in received) and task.id in {item.payload["task_id"] for item in received}
    assert await h.redis.xlen(queue.stream + ":dead_letter") == 0
    assert database_state(h) == before and h.executions == h.native.calls == []

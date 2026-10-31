from __future__ import annotations

from copy import deepcopy
import json

import pytest
import pytest_asyncio

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.redis_task_worker import RedisTaskWorker
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_model_reader_admission import database_state, dispatcher


@pytest_asyncio.fixture(autouse=True)
async def assert_checkpoint_isolation(isolate_recovery_stores, admission):
    assert admission.settings.deep_agents.checkpoint_schema == admission.prefix + "_checkpoints"


def future_command(h, change):
    wire = deepcopy(h.command.model_dump(mode="json"))
    if change == "schema_version":
        wire[change] = 3
    elif change == "binding_field":
        wire["runtime_binding"]["future_field"] = "private-command-input"
    elif change == "command_field":
        wire["future_field"] = "private-command-input"
    elif change in {"operation", "task_type"}:
        wire[change] = "future-command-selector"
    elif change in {"runtime_kind", "model_transport"}:
        wire["runtime_binding"][change] = "future-command-selector"
    return wire


@pytest.mark.asyncio
@pytest.mark.parametrize("change,status,retries", [
    (change, "queued", 0) for change in ("schema_version", "binding_field", "command_field", "operation", "task_type",
        "runtime_kind", "model_transport")
] + [(change, status, 3) for change in ("schema_version", "binding_field") for status in ("claimed", "cancelled")])
async def test_unknown_command_retains_same_real_delivery_without_retry_or_claim(admission, change, status, retries, caplog):
    h = admission
    task = await submit(h)
    if status != "queued":
        owner = await h.tasks.claim(task.id, worker_id="command-origin", expected_binding=h.binding)
        if status == "claimed":
            assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
        else:
            await h.tasks.finish(owner, status="cancelled", error_code="test_fixture_cancelled")
    selected = dispatcher(h)
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="command-format")
    await queue.connect()
    received = []

    async def handle(item):
        received.append(item.retry_count)
        await selected.handle_v2(item, worker_id="command-reader")

    try:
        # Inject only this isolated outbox's future envelope, then use the real
        # relay. The durable task/binding is not rewritten into another format.
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_task_outbox_table} SET payload=%s::jsonb WHERE event_key=%s",
                (json.dumps(future_command(h, change)), f"agent_turn:{task.id}"))
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        entries, before = await h.redis.xrange(queue.stream), database_state(h)
        worker = RedisTaskWorker(queue, consumer="command-reader", handler=handle, block_ms=1, max_retries=retries)
        assert await worker.run_once() == 0
        assert received == [0] and database_state(h) == before and h.executions == []
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xrange(queue.stream) == entries
        assert await h.redis.xlen(queue.stream + ":dead_letter") == 0
        pending = await queue.consume(consumer="command-reader", stream_id="0", block_ms=0)
        assert len(pending) == 1 and pending[0].retry_count == 0
        assert pending[0].message_id == entries[0][0] and pending[0].payload == future_command(h, change)
        records = [row for row in caplog.records if row.message == "agent_v2_command_reader_unavailable"]
        assert len(records) == 1 and records[0].worker_id == "command-reader"
        assert records[0].message_id == entries[0][0]
        assert records[0].reason == "framework_command_format_unavailable" and records[0].exc_info is None
        assert "private-command-input" not in caplog.text and "future-command-selector" not in caplog.text
    finally:
        await queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["constraint", "identity"])
async def test_known_command_errors_keep_original_dead_letter_policy_and_no_claim(admission, change):
    h = admission
    await submit(h)
    selected = dispatcher(h)
    wire = h.command.model_dump(mode="json")
    wire["priority" if change == "constraint" else "request_id"] = 101 if change == "constraint" else "wrong-request"
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.prefix + ":command-error", group="command-error")
    await queue.connect()

    async def handle(item):
        await selected.handle_v2(item, worker_id="command-error-reader")

    try:
        await queue.enqueue(wire)
        before = database_state(h)
        assert await RedisTaskWorker(queue, consumer="command-error-reader", handler=handle,
            block_ms=1, max_retries=0).run_once() == 0
        assert database_state(h) == before and h.executions == []
        assert not await queue.pending() and await h.redis.xlen(queue.stream + ":dead_letter") == 1
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_compatible_command_with_free_payload_completes_once_and_acks_redelivery(admission):
    h = admission
    task = await submit(h)
    wire = h.command.model_dump(mode="json")
    wire["payload"] = {"schema_version": 99, "metadata": {"operation": "future", "future_field": True}}
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_task_outbox_table} SET payload=%s::jsonb WHERE event_key=%s",
            (json.dumps(wire), f"agent_turn:{task.id}"))
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="compatible-command")
    await queue.connect()
    selected = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources, framework_model_calls=h.calls,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))

    async def handle(item):
        await selected.handle_v2(item, worker_id="compatible-command-reader")

    try:
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        worker = RedisTaskWorker(queue, consumer="compatible-command-reader", handler=handle, block_ms=1)
        assert await worker.run_once() == 1
        saved = await h.tasks.get(task.id)
        assert saved.status == "completed" and saved.attempt_no == 1
        assert h.runtime.calls == [h.binding.turn_id]
        before = database_state(h)
        await queue.enqueue(wire)
        assert await worker.run_once() == 1
        assert database_state(h) == before and h.runtime.calls == [h.binding.turn_id]
        assert not await queue.pending() and await h.redis.xlen(queue.stream + ":dead_letter") == 0
    finally:
        await queue.close()

from __future__ import annotations

from copy import deepcopy
import asyncio
import json
from threading import Event

import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.task_deferred import TaskDeferred
from src.schemas.runtime_binding import RuntimeBinding
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_model_reader_admission import database_state, dispatcher


def change_persisted_binding(h, target, **changes):
    binding = {**h.binding.model_dump(mode="json"), **changes}
    table = h.tasks._tasks if target == "task" else h.tasks._tasks + "_runtime"
    field, identity = ("id", h.command.task_id) if target == "task" else ("binding_id", h.binding.binding_id)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {table} SET runtime_binding=%s::jsonb WHERE {field}=%s", (json.dumps(binding), identity))
        assert cur.rowcount == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["task", "owner"])
@pytest.mark.parametrize("status", ["queued", "claimed", "running", "cancelled"])
async def test_future_persisted_binding_defers_raw_claim_without_mutation(admission, target, status):
    h = admission
    task = await submit(h)
    if status != "queued":
        owner = await h.tasks.claim(task.id, worker_id="binding-origin", expected_binding=h.binding)
        if status == "running":
            await h.tasks.begin(owner, resource_id="binding-fixture", fencing_token=1)
        if status == "cancelled":
            await h.tasks.finish(owner, status="cancelled", error_code="test_fixture_cancelled")
        else:
            assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
    change_persisted_binding(h, target, future_field="private-persisted-binding")
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_binding_format_unavailable"):
        await h.tasks.claim(h.command.task_id, worker_id="persisted-binding-reader", expected_binding=h.binding,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert database_state(h) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["task", "owner"])
@pytest.mark.parametrize("retries", [0, 3])
async def test_future_persisted_binding_keeps_compatible_wire_in_original_real_pel(admission, target, retries, caplog):
    h = admission
    await submit(h)
    change_persisted_binding(h, target, future_field="private-persisted-binding")
    selected = dispatcher(h)
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="persisted-binding-format")
    await queue.connect()
    received = []

    async def handle(item):
        received.append(item.retry_count)
        await selected.handle_v2(item, worker_id="persisted-binding-reader")

    try:
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        entries, before = await h.redis.xrange(queue.stream), database_state(h)
        assert await RedisTaskWorker(queue, consumer="persisted-binding-reader", handler=handle,
            block_ms=1, max_retries=retries).run_once() == 0
        assert received == [0] and database_state(h) == before and h.executions == []
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xrange(queue.stream) == entries
        assert await h.redis.xlen(queue.stream + ":dead_letter") == 0
        pending = await queue.consume(consumer="persisted-binding-reader", stream_id="0", block_ms=0)
        assert len(pending) == 1 and pending[0].retry_count == 0
        assert pending[0].message_id == entries[0][0]
        assert pending[0].payload == h.command.model_dump(mode="json")
        records = [row for row in caplog.records if row.message in {
            "agent_v2_binding_reader_unavailable", "framework_binding_reader_unavailable"}]
        assert len(records) == 1 and records[0].task_id == h.command.task_id
        assert records[0].reason == "framework_binding_format_unavailable" and records[0].exc_info is None
        assert "private-persisted-binding" not in caplog.text
    finally:
        await queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["task", "owner"])
@pytest.mark.parametrize("changes", [{"runtime_kind": "future-binding-runtime"},
    {"model_transport": "future-binding-transport"},
    {"release_reference": {"schema_version": 2, "archive_sha256": "b" * 64,
        "size_bytes": 1234, "source_revision": "c" * 40}}])
async def test_future_persisted_binding_selectors_are_read_without_execution(admission, target, changes):
    h = admission
    await submit(h)
    change_persisted_binding(h, target, **changes)
    before = database_state(h)
    selected = dispatcher(h)
    from tests.test_framework_model_reader_admission import deliver

    with pytest.raises(TaskDeferred, match="framework_binding_format_unavailable"):
        await deliver(h, selected)
    assert database_state(h) == before and h.executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["task", "owner"])
async def test_binding_format_is_rechecked_after_waiting_for_session_lock(admission, target, monkeypatch):
    from tests.test_framework_model_reader_admission import deliver

    h = admission
    await submit(h)
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
            pending = asyncio.create_task(deliver(h, selected))
            assert await asyncio.to_thread(reached.wait, 5)
            change_persisted_binding(h, target, future_field="private-after-lock-binding")
            before = database_state(h)
        with pytest.raises(TaskDeferred, match="framework_binding_format_unavailable"):
            await pending
        assert database_state(h) == before and h.executions == []
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["task", "owner"])
@pytest.mark.parametrize("change", ["constraint", "fingerprint", "identity"])
async def test_known_persisted_binding_error_keeps_original_failure_not_compatibility_wait(admission, target, change):
    from tests.test_framework_model_reader_admission import deliver

    h = admission
    await submit(h)
    changes = {"dependency_lock_sha256": "invalid"} if change == "constraint" else {"fingerprint": "0" * 64}
    if change == "identity":
        changes = RuntimeBinding.model_validate({**h.binding.model_dump(mode="json", exclude={"fingerprint"}),
            "prompt_version": "changed-known-prompt"}).model_dump(mode="json")
    change_persisted_binding(h, target, **changes)
    before = database_state(h)
    selected = dispatcher(h)
    with pytest.raises(ValidationError if change != "identity" else ValueError):
        await deliver(h, selected)
    assert database_state(h) == before and h.executions == []
    with pytest.raises(ValidationError if change != "identity" else ValueError):
        await h.tasks.claim(h.command.task_id, worker_id="known-error-reader", expected_binding=h.binding,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert database_state(h) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["task", "owner"])
async def test_unconfigured_raw_readers_do_not_silently_acquire_framework_compatibility(admission, target):
    h = admission
    await submit(h)
    change_persisted_binding(h, target, future_field="private-unconfigured-binding")
    before = database_state(h)
    if target == "task":
        with pytest.raises(ValidationError):
            await h.tasks.get(h.command.task_id)
    with pytest.raises(ValidationError):
        await h.tasks.claim(h.command.task_id, worker_id="unconfigured-reader", expected_binding=h.binding)
    assert database_state(h) == before


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

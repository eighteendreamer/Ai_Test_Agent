"""Real Redis/PG delivery faults on test-owned streams/tables only."""
import asyncio
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from src.core.config import get_settings
from src.core.request_context import get_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.redis_task_scripts import QUARANTINE, TRANSFER
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.task_deferred import TaskDeferred
from src.schemas.run_dispatch import RunDispatchTask
from tests.live_postgres_config import LivePostgresTestConfig

pytestmark = pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 for real Redis/PostgreSQL delivery faults",
)


@pytest_asyncio.fixture
async def queue():
    stream = f"qa:test:delivery:{uuid4().hex}"
    queue = RedisTaskQueue(get_settings().database.redis_url, stream=stream, group="test-workers")
    await queue.connect()
    try:
        yield queue
    finally:
        # Exact keys belong to this fixture, never flush or scan-delete business streams.
        await queue._client.delete(stream, f"{stream}:dead_letter")
        await queue.close()


def payload():
    return RunDispatchTask(task_id="task-test", request_id="req-test", trace_id="trace-test",
                           run_id="run-test", project_id="project-test", session_id="session-test").model_dump(mode="json")


def worker(queue, handler, **kwargs):
    return RedisTaskWorker(queue, consumer="worker-test", handler=handler, block_ms=0,
                           reclaim_idle_ms=60, retry_base_ms=25, retry_max_ms=100, **kwargs)


async def until_processed(worker):
    async def poll():
        while not await worker.run_once():
            await asyncio.sleep(.03)
    await asyncio.wait_for(poll(), timeout=3)


@pytest.mark.asyncio
async def test_atomic_dead_letter_failure_and_owner_fencing(queue):
    message_id = await queue.enqueue(payload())
    task = (await queue.consume(consumer="original", block_ms=0))[0]
    destination = f"{queue.stream}:dead_letter"
    await queue._client.set(destination, "wrong-type-for-fault-test")
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await queue.transfer(task, consumer="original", retry_count=3, dead_letter_reason="RuntimeError")
    assert (await queue.pending())[0]["message_id"] == message_id
    await queue._client.delete(destination)
    await queue._client.xclaim(queue.stream, queue.group, "new-owner", 0, [message_id], justid=True)
    assert await queue.ack_owned(task, consumer="original") == 0
    assert await queue.transfer(task, consumer="original", retry_count=1) is None
    successor = await queue.transfer(task, consumer="new-owner", retry_count=3, dead_letter_reason="RuntimeError")
    assert successor and not await queue.pending()
    assert await queue.transfer(task, consumer="new-owner", retry_count=3, dead_letter_reason="RuntimeError") is None
    assert await queue._client.xlen(destination) == 1


@pytest.mark.asyncio
async def test_publish_reply_loss_does_not_drop_or_duplicate_retry(queue):
    original_payload = payload()
    await queue.enqueue(original_payload)
    received = []
    async def handler(task):
        RunDispatchTask.model_validate(task.payload)
        assert task.payload == original_payload
        received.append(task.retry_count)
        if task.retry_count == 0:
            raise RuntimeError("first delivery failed")

    current = worker(queue, handler)
    real_eval = queue._client.eval
    async def lost_response(script, *args):
        result = await real_eval(script, *args)
        if script == TRANSFER:
            raise RedisConnectionError("reply lost after Redis committed transfer")
        return result
    queue._client.eval = lost_response
    with pytest.raises(RedisConnectionError):
        await current.run_once()
    queue._client.eval = real_eval
    assert await queue._client.xlen(queue.stream) == 2
    await until_processed(current)
    assert received == [0, 1]
    assert not await queue.pending()


@pytest.mark.asyncio
async def test_not_before_and_abandoned_pending_recovery(queue):
    await queue.enqueue(payload())
    task = (await queue.consume(consumer="abandoned", block_ms=0))[0]
    successor = await queue.transfer(task, consumer="abandoned", retry_count=1, delay_ms=250)
    scheduled = (await queue.consume(consumer="abandoned", block_ms=0))[0]
    assert scheduled.message_id == successor
    assert not await queue.ready(scheduled, consumer="abandoned")
    calls = []
    async def handler(task):
        calls.append(task.retry_count)
    await asyncio.sleep(.26)
    await until_processed(worker(queue, handler))
    assert calls == [1] and not await queue.pending()


@pytest.mark.asyncio
async def test_running_delivery_heartbeat_prevents_reclaim(queue):
    await queue.enqueue(payload())
    started, release = asyncio.Event(), asyncio.Event()
    async def handler(task):
        started.set()
        await release.wait()
    current = RedisTaskWorker(queue, consumer="active", handler=handler, reclaim_idle_ms=300, block_ms=0)
    running = asyncio.create_task(current.run_once())
    try:
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(5):
            await asyncio.sleep(.1)
            assert await queue.reclaim(consumer="rival", min_idle_ms=300) == []
        release.set()
        assert await running == 1
        assert not await queue.pending()
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_timeout_and_deferred_delivery_are_distinct(queue):
    await queue.enqueue(payload())
    async def never_returns(task):
        await asyncio.Event().wait()
    assert await worker(queue, never_returns, max_retries=0, task_timeout_seconds=.03).run_once() == 0
    assert not await queue.pending()
    assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1
    await queue.enqueue(payload())
    async def deferred(task):
        raise TaskDeferred("waiting for resources")
    await worker(queue, deferred, max_retries=0).run_once()
    assert len(await queue.pending()) == 1
    assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1


@pytest.mark.asyncio
async def test_worker_stops_execution_after_ownership_loss(queue):
    message_id = await queue.enqueue(payload())
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def handler(task):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    current = worker(queue, handler)
    running = asyncio.create_task(current.run_once())
    try:
        await asyncio.wait_for(started.wait(), 2)
        await queue._client.xclaim(queue.stream, queue.group, "replacement", 0, [message_id], justid=True)
        assert await asyncio.wait_for(running, 2) == 0
        assert cancelled.is_set()
        pending = await queue.pending()
        owned = [item for item in pending if item["message_id"] == message_id]
        assert len(owned) == 1 and owned[0]["consumer"] == "replacement"
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
@pytest.mark.asyncio
async def test_retry_after_database_commit_preserves_one_durable_result(queue):
    settings = get_settings()
    table = f"test_delivery_{uuid4().hex[:12]}"
    with postgres_connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE TABLE {table} (task_id TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
    seen = []
    async def handler(task):
        command = RunDispatchTask.model_validate(task.payload)
        context = get_request_context()
        assert context.session_id == command.session_id and context.worker_id == "worker-test"
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO {table} VALUES (%s, %s) ON CONFLICT DO NOTHING", (command.task_id, "completed"))
        seen.append(task.retry_count)
        if task.retry_count == 0:
            raise RuntimeError("simulated process failure after durable commit, before ACK")
    try:
        await queue.enqueue(payload())
        current = worker(queue, handler)
        await current.run_once()
        await until_processed(current)
        assert seen == [0, 1] and not await queue.pending()
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM {table}")
                assert cur.fetchone()["total"] == 1
        print("delivery_pg_commit=verified retry_envelope=valid durable_result_count=1")
    finally:
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE {table}")


@pytest.mark.parametrize("delivery", ["consume", "reclaim"])
@pytest.mark.parametrize("bad_fields, reason", [
    ({"unexpected": "preserve-this-field"}, "missing_payload"),
    ({"payload": "{broken-private-body"}, "invalid_json"),
    ({"payload": "[]"}, "payload_not_object"),
    ({"payload": '{"task_id":"bad"}', "retry_count": "invalid"}, "invalid_delivery_metadata"),
    ({"payload": '{"task_id":"bad"}', "not_before_ms": "invalid"}, "invalid_delivery_metadata"),
])
@pytest.mark.asyncio
async def test_malformed_delivery_does_not_stop_valid_batch(queue, delivery, bad_fields, reason, caplog):
    """P1-68: inject a bad Redis entry between two real worker deliveries."""
    await queue.enqueue({"task_id": "before"})
    bad_id = await queue._client.xadd(queue.stream, bad_fields)
    await queue.enqueue({"task_id": "after"})
    if delivery == "reclaim":
        await queue._client.xreadgroup(queue.group, "abandoned", {queue.stream: ">"}, count=3)
        await asyncio.sleep(.02)
    handled = []

    async def handler(task):
        handled.append(task.payload["task_id"])

    current = RedisTaskWorker(queue, consumer="worker-test", handler=handler,
                              batch_size=3, block_ms=0, reclaim_idle_ms=1)
    assert await current.run_once() == 2
    assert handled == ["before", "after"]
    assert not await queue.pending()
    letters = await queue._client.xrange(f"{queue.stream}:dead_letter")
    assert len(letters) == 1
    archived = json.loads(letters[0][1]["payload"])
    assert archived["source_message_id"] == bad_id
    assert archived["source_group"] == queue.group
    assert archived["dead_letter_reason"] == reason
    raw_fields = archived["source_fields"]
    assert dict(zip(raw_fields[::2], raw_fields[1::2])) == bad_fields
    assert "broken-private-body" not in caplog.text
    assert "preserve-this-field" not in caplog.text


@pytest.mark.asyncio
async def test_quarantine_archive_failure_retains_entire_batch_for_recovery(queue):
    before = await queue.enqueue({"task_id": "before"})
    bad_id = await queue._client.xadd(queue.stream, {"unexpected": "bad"})
    after = await queue.enqueue({"task_id": "after"})
    destination = f"{queue.stream}:dead_letter"
    await queue._client.set(destination, "wrong-type-for-fault-test")
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await queue.consume(consumer="original", count=3, block_ms=0)
    assert {row["message_id"] for row in await queue.pending()} == {before, bad_id, after}
    await queue._client.delete(destination)
    await asyncio.sleep(.02)
    handled = []

    async def handler(task):
        handled.append(task.payload["task_id"])

    current = RedisTaskWorker(queue, consumer="replacement", handler=handler,
                              batch_size=3, block_ms=0, reclaim_idle_ms=1)
    assert await current.run_once() == 2
    assert handled == ["before", "after"] and not await queue.pending()
    assert await queue._client.xlen(destination) == 1


@pytest.mark.asyncio
async def test_quarantine_reply_loss_preserves_valid_deliveries_without_duplicate_archive(queue):
    before = await queue.enqueue({"task_id": "before"})
    await queue._client.xadd(queue.stream, {"payload": "{broken"})
    after = await queue.enqueue({"task_id": "after"})
    real_eval = queue._client.eval

    async def lose_quarantine_reply(script, *args):
        result = await real_eval(script, *args)
        if script == QUARANTINE:
            raise RedisConnectionError("reply lost after archive committed")
        return result

    queue._client.eval = lose_quarantine_reply
    try:
        with pytest.raises(RedisConnectionError):
            await queue.consume(consumer="original", count=3, block_ms=0)
    finally:
        queue._client.eval = real_eval
    assert {row["message_id"] for row in await queue.pending()} == {before, after}
    await asyncio.sleep(.02)
    tasks = await queue.reclaim(consumer="replacement", count=3, min_idle_ms=1)
    assert [task.payload["task_id"] for task in tasks] == ["before", "after"]
    for task in tasks:
        assert await queue.ack_owned(task, consumer="replacement") == 1
    assert not await queue.pending()
    assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1


@pytest.mark.asyncio
async def test_quarantine_checks_current_owner_and_does_not_archive_twice(queue):
    bad_id = await queue._client.xadd(queue.stream, {"payload": "null"})
    real_eval = queue._client.eval

    async def steal_before_quarantine(script, *args):
        if script == QUARANTINE:
            await queue._client.xclaim(queue.stream, queue.group, "replacement", 0, [bad_id], justid=True)
        return await real_eval(script, *args)

    queue._client.eval = steal_before_quarantine
    try:
        assert await queue.consume(consumer="original", block_ms=0) == []
    finally:
        queue._client.eval = real_eval
    assert (await queue.pending())[0]["consumer"] == "replacement"
    assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 0
    assert await queue.consume(consumer="replacement", stream_id="0", block_ms=0) == []
    assert not await queue.pending()
    assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1
    assert await queue._quarantine(bad_id, consumer="replacement", reason="payload_not_object") is None
    assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1


@pytest.mark.asyncio
async def test_deleted_pending_body_is_archived_as_missing_before_ack(queue):
    message_id = await queue.enqueue({"task_id": "deleted-by-fault-test"})
    await queue.consume(consumer="original", block_ms=0)
    await queue._client.xdel(queue.stream, message_id)
    assert await queue.consume(consumer="original", stream_id="0", block_ms=0) == []
    assert not await queue.pending()
    letters = await queue._client.xrange(f"{queue.stream}:dead_letter")
    assert len(letters) == 1
    archived = json.loads(letters[0][1]["payload"])
    assert archived["source_message_id"] == message_id
    assert archived["source_entry_missing"] is True

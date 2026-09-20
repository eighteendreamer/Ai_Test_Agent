"""P4-B fault injection: an outage may stall the pipeline, never invent progress.

The plan forbids publishing a command while Redis is unreachable and forbids
claiming a task while PostgreSQL is unreachable. Both facts live in the durable
stores, so every case here needs the real PostgreSQL tables and the real Redis
stream to prove nothing was fabricated and that delivery resumes afterwards.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_agent_task_dispatch import Runtime, build_services
from tests.test_live_postgres_concurrency import _drop_tables


class PostgresOutage(RuntimeError):
    """Stands in for the driver error a down PostgreSQL raises on contact."""


class UnreachableRedis:
    """A stream handle whose every publish fails, as during a Redis outage."""

    def __init__(self, stream: str) -> None:
        self.stream = stream

    async def enqueue(self, payload):
        raise ConnectionError("redis is unavailable")


async def outbox_row(harness, event_key: str) -> dict:
    with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT published_at, last_error, attempts, payload FROM "
            f"{harness.settings.database.postgres_task_outbox_table} WHERE event_key = %s",
            (event_key,),
        )
        row = cur.fetchone()
    assert row is not None, f"missing outbox row {event_key}"
    return row


@pytest_asyncio.fixture
async def harness():
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    fields = ("session", "message", "event", "snapshot", "approval", "task_outbox", "agent_task", "agent_attempt")
    tables = [f"p4b_test_{field}_{suffix}" for field in fields]
    for field, table in zip(fields, tables):
        setattr(settings.database, f"postgres_{field}_table", table)
    prefix = f"p4b_test_{suffix}"
    stream = f"{prefix}:tasks:agent"
    settings.orchestration.agent_task_stream = stream
    settings.orchestration.agent_task_control_poll_seconds = .05
    # An outage must be retriable inside the test, not parked by the default backoff.
    settings.orchestration.task_outbox_retry_seconds = 0
    redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
    await resources.connect()
    runtime = Runtime()
    store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
    outbox = PostgresTaskOutbox(settings)
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=stream,
        group="p4b-test",
        client=redis,
    )
    try:
        await store.initialize()
        await outbox.initialize()
        await tasks.initialize()
        await queue.connect()
        session_id = str(uuid4())
        await store.save_session(SessionRecord(
            id=session_id,
            title="fault injection parent",
            status=SessionStatus.idle,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        receipt = await dispatcher.submit(
            session_id,
            SendMessageRequest(content="produce the plan", metadata={"client_message_id": "p4b-input-1"}),
        )
        yield SimpleNamespace(
            settings=settings,
            prefix=prefix,
            stream=stream,
            redis=redis,
            resources=resources,
            runtime=runtime,
            store=store,
            tasks=tasks,
            sessions=sessions,
            dispatcher=dispatcher,
            outbox=outbox,
            queue=queue,
            session_id=session_id,
            parent_task_id=receipt.task.id,
            event_key=f"agent_turn:{receipt.task.id}",
        )
    finally:
        try:
            await redis.delete(stream)
        finally:
            await redis.aclose()
            _drop_tables(settings, list(reversed(tables)))


@pytest.mark.asyncio
async def test_redis_outage_parks_the_command_without_faking_task_progress(harness):
    """Redis down: the command stays unpublished and the task stays queued."""
    h = harness

    assert await h.outbox.relay_once(UnreachableRedis(h.stream)) == 0

    row = await outbox_row(h, h.event_key)
    assert row["published_at"] is None, "a failed publish must not mark the command delivered"
    assert row["last_error"] == "ConnectionError"
    assert int(row["attempts"]) >= 1
    assert await h.redis.xlen(h.stream) == 0, "nothing may reach the stream during the outage"
    assert (await h.tasks.get(h.parent_task_id)).status == "queued"
    assert h.runtime.calls == [], "a task nobody received must not run"


@pytest.mark.asyncio
async def test_redis_recovery_republishes_the_parked_command(harness):
    """Redis back: the same durable row is published once and then sealed."""
    h = harness
    assert await h.outbox.relay_once(UnreachableRedis(h.stream)) == 0

    assert await h.outbox.relay_once(h.queue) == 1
    assert await h.redis.xlen(h.stream) == 1
    assert (await outbox_row(h, h.event_key))["published_at"] is not None
    # A sealed row is never offered again, so a recovery cannot double-publish.
    assert await h.outbox.relay_once(h.queue) == 0
    assert await h.redis.xlen(h.stream) == 1


@pytest.mark.asyncio
async def test_postgres_outage_stops_claim_and_the_worker_resumes_afterwards(harness):
    """PostgreSQL down: no claim, no running state, and delivery resumes later."""
    h = harness
    delivery = QueuedTask("p4b-delivery", dict((await outbox_row(h, h.event_key))["payload"]))
    original_claim = h.tasks.claim

    async def outage(task_id, **kwargs):
        raise PostgresOutage("injected postgres outage at claim")

    h.tasks.claim = outage
    try:
        with pytest.raises(PostgresOutage):
            await h.dispatcher.handle(delivery, worker_id="p4b-worker")
        assert (await h.tasks.get(h.parent_task_id)).status == "queued", (
            "a worker that could not read the task must not report running"
        )
        assert h.runtime.calls == []
    finally:
        h.tasks.claim = original_claim

    await h.dispatcher.handle(delivery, worker_id="p4b-worker")
    assert (await h.tasks.get(h.parent_task_id)).status == "completed"
    assert h.runtime.calls, "the same delivery must run once the database answers again"


@pytest.mark.asyncio
async def test_postgres_outage_on_the_running_write_never_starts_the_runtime(harness):
    """An outage after the claim must leave the task unexecuted, not running.

    ``begin`` is the only writer of the running state, so the whole point of the
    claim/running split is that a delivery which cannot record running also gets
    no runtime work at all. The raise is also what keeps the Worker from ACKing.
    """
    h = harness
    delivery = QueuedTask("p4b-running-outage", dict((await outbox_row(h, h.event_key))["payload"]))
    originals = {name: getattr(h.tasks, name) for name in ("begin", "finish")}

    async def outage(*args, **kwargs):
        raise PostgresOutage("injected postgres outage at the running write")

    try:
        for name in originals:
            setattr(h.tasks, name, outage)
        with pytest.raises(PostgresOutage):
            await h.dispatcher.handle(delivery, worker_id="p4b-worker")
    finally:
        for name, original in originals.items():
            setattr(h.tasks, name, original)

    assert (await h.tasks.get(h.parent_task_id)).status == "claimed", (
        "a delivery that could not write running must not look started or finished"
    )
    assert h.runtime.calls == []
    assert await h.resources.get(resource_type="agent", resource_id=f"turn:{h.parent_task_id}") is None, (
        "the admission slot must come back even when the database write failed"
    )

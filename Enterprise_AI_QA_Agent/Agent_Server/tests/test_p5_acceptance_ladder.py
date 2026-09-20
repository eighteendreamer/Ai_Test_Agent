"""P5 acceptance ladder: concurrency, SSE fan-out, durable coordinator fan-out
and dead-letter traceability on the real PostgreSQL + Redis pair.

Every case here runs production wiring end to end:

    submit (HTTP route or AgentTaskDispatcher)
      -> PostgreSQL task row + transactional outbox row
      -> outbox relay -> Redis Stream (consumer group)
      -> N independent multiprocessing Worker processes (+ the in-process one)
      -> session / message / event / attempt facts back in PostgreSQL

The gates asserted below are the P5 acceptance thresholds: zero lost tasks, zero
duplicate execution, zero cross-session trace mixing, work spread over more than
one Worker, zero SSE event loss for concurrent subscribers, and a dead letter
entry that still carries the offending payload.

Gated twice on purpose: the whole module needs RUN_LIVE_POSTGRES_TESTS *and*
RUN_LIVE_P5_ACCEPTANCE because it occupies real Worker slots and database tables.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import random
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import Redis

from src.api.routes.sessions import router
from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.application.orchestration.coordinator_runtime_service import (
    CoordinatorRuntimeService,
)
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.sessions.session_service import SessionService
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.schemas.agent_task import TERMINAL_AGENT_TASK_STATUSES
from src.schemas.session import (
    RuntimeMode,
    SendMessageRequest,
    SessionMode,
    SessionStatus,
)
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_agent_task_dispatch import Runtime, build_services
from tests.test_live_postgres_concurrency import _drop_tables

_TEST_CONFIG = LivePostgresTestConfig()

pytestmark = pytest.mark.skipif(
    not (_TEST_CONFIG.run_live_postgres_tests and _TEST_CONFIG.run_live_p5_acceptance),
    reason="P5 acceptance needs RUN_LIVE_POSTGRES_TESTS and RUN_LIVE_P5_ACCEPTANCE",
)

CONCURRENCY_LEVELS = (1, 2, 5, 10, 20)
LEVEL_TIMEOUT_SECONDS = 120
# The pump gives up before the whole-level deadline so the failure message can
# name the stuck tasks instead of only the level.
LADDER_PUMP_TIMEOUT_SECONDS = 95
WORKER_GROUP = "p5-acceptance"
# A redelivered command may never overtake a Worker that is still inside the
# runtime, so reclaim only touches deliveries idle for far longer than one stub
# turn takes. FIFO-blocked inputs are retried on exactly this same clock.
RECLAIM_IDLE_MS = 6000
TABLE_FIELDS = (
    "session",
    "message",
    "event",
    "snapshot",
    "approval",
    "task_outbox",
    "agent_task",
    "agent_attempt",
)


class LadderRuntime(Runtime):
    """Stub Agent runtime with a bounded random pause inside the turn."""

    delay_range = (0.0, 0.0)

    async def execute_turn(self, session, request, **kwargs):
        low, high = self.delay_range
        if high:
            await asyncio.sleep(random.uniform(low, high))
        return await super().execute_turn(session, request, **kwargs)


async def build_worker_stack(settings, prefix, worker_id, *, runtime):
    """Rebuild every service inside a Worker process; nothing crosses the boundary.

    The coordinator is wired here too, because the Worker that settles a durable
    child is the one that has to queue its parent continuation.
    """
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix)
    await resources.connect()
    store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
    coordinator = CoordinatorRuntimeService(
        settings=settings,
        store=store,
        session_service=sessions,
        agent_registry=AgentRegistry(),
    )
    coordinator.set_agent_task_store(tasks)
    coordinator.set_agent_task_dispatcher(dispatcher)
    dispatcher.set_coordinator_service(coordinator)
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=settings.orchestration.agent_task_stream,
        group=WORKER_GROUP,
    )
    await queue.connect()
    return SimpleNamespace(
        resources=resources,
        queue=queue,
        dispatcher=dispatcher,
        coordinator=coordinator,
    )


def p5_worker_process(settings, prefix, worker_id, stop, options=None, ready=None):
    """Child-process Worker: consume the Agent stream until told to stop.

    ``options`` has to survive the spawn boundary, so it is a plain dict:
    ``max_deliveries`` bounds how many deliveries one Worker may own (0 = until
    stop), ``deadline_seconds`` bounds the whole process, ``delay_range`` is the
    stub runtime's random pause, and ``ready`` is released once this consumer is
    registered on the stream.
    """
    options = options or {}
    delay = tuple(options.get("delay_range") or (0.0, 0.0))
    max_deliveries = int(options.get("max_deliveries") or 0)
    deadline_seconds = float(options.get("deadline_seconds") or 100)
    block_ms = int(options.get("block_ms") or 200)
    reclaim_idle_ms = int(options.get("reclaim_idle_ms") or RECLAIM_IDLE_MS)

    class WorkerRuntime(LadderRuntime):
        delay_range = delay

    async def run():
        stack = await build_worker_stack(
            settings,
            prefix,
            worker_id,
            runtime=WorkerRuntime(),
        )
        try:
            worker = RedisTaskWorker(
                stack.queue,
                consumer=worker_id,
                handler=lambda item: stack.dispatcher.handle(item, worker_id=worker_id),
                block_ms=block_ms,
                reclaim_idle_ms=reclaim_idle_ms,
            )
            processed = 0
            deadline = time.monotonic() + deadline_seconds
            if ready is not None:
                ready.set()
            while not stop.is_set() and time.monotonic() < deadline:
                processed += await worker.run_once()
                if max_deliveries and processed >= max_deliveries:
                    break
        finally:
            await stack.queue.close()
            await stack.resources.close()

    asyncio.run(run())


class WorkerFleet:
    """Spawned Worker processes bound to one harness (tables + stream prefix)."""

    def __init__(self, harness, *, count, options=None, name_prefix="p5-w"):
        self._settings = harness.settings
        self._prefix = harness.prefix
        self._context = multiprocessing.get_context("spawn")
        self._options = dict(options or {})
        self.stop = self._context.Event()
        self.worker_ids = [f"{name_prefix}{index}" for index in range(count)]
        self._processes: list = []
        self._ready: list = []

    def start(self) -> list[str]:
        for worker_id in self.worker_ids:
            ready = self._context.Event()
            process = self._context.Process(
                target=p5_worker_process,
                args=(self._settings, self._prefix, worker_id, self.stop, self._options, ready),
            )
            process.start()
            self._processes.append(process)
            self._ready.append(ready)
        return list(self.worker_ids)

    async def wait_ready(self, timeout: float = 90) -> None:
        """Every consumer must be up before anything is relayed to the stream."""
        deadline = time.monotonic() + timeout
        for ready in self._ready:
            remaining = max(0.1, deadline - time.monotonic())
            assert await asyncio.to_thread(ready.wait, remaining), (
                "a P5 Worker process did not come up within the ready window"
            )

    async def shutdown(self, timeout: float = 20) -> list[int | None]:
        """Ask the Workers to stop, then hard-stop anything still alive."""
        self.stop.set()
        for process in self._processes:
            await asyncio.to_thread(process.join, timeout)
        for process in self._processes:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        return [process.exitcode for process in self._processes]


def message_payload(content, client_message_id, **kwargs) -> SendMessageRequest:
    return SendMessageRequest(
        content=content,
        metadata={"client_message_id": client_message_id},
        **kwargs,
    )


async def create_session(harness, title, **kwargs) -> str:
    session_id = str(uuid4())
    await harness.store.save_session(SessionRecord(
        id=session_id,
        title=title,
        status=kwargs.get("status", SessionStatus.idle),
        session_mode=kwargs.get("session_mode", SessionMode.normal),
        runtime_mode=kwargs.get("runtime_mode", RuntimeMode.interactive),
        mode_key="default",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        metadata=kwargs.get("metadata", {}),
    ))
    return session_id


async def fetch_rows(harness, sql, params=()):
    def run():
        with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])

    return await asyncio.to_thread(run)


async def task_rows(harness) -> dict[str, dict]:
    rows = await fetch_rows(
        harness,
        "SELECT id, session_id, turn_id, operation, status, worker_id, attempt_no, parent_task_id, "
        "updated_at "
        f"FROM {harness.settings.database.postgres_agent_task_table}",
    )
    return {row["id"]: row for row in rows}


async def relay_all(harness, *, limit: int = 200, rounds: int = 10) -> int:
    """Publish every pending outbox row; the relay is the only stream producer."""
    published = 0
    for _ in range(rounds):
        count = await harness.outbox.relay_once(harness.queue, limit=limit)
        published += count
        if not count:
            break
    return published


async def post_message(client, session_id, payload) -> str:
    response = await client.post(
        f"/sessions/{session_id}/messages",
        json=payload.model_dump(mode="json"),
    )
    assert response.status_code == 202, f"submission failed: {response.status_code} {response.text}"
    return response.json()["task"]["id"]


async def pending_count(harness) -> int:
    return int((await harness.redis.xpending(harness.queue.stream, WORKER_GROUP))["pending"])


async def wait_until_terminal(harness, task_ids, *, timeout, what="tasks") -> dict[str, dict]:
    """Poll durable task rows only; used when separate processes do the work."""
    deadline = time.monotonic() + timeout
    while True:
        rows = await task_rows(harness)
        wanted = {task_id: rows.get(task_id) for task_id in task_ids}
        if all(row and row["status"] in TERMINAL_AGENT_TASK_STATUSES for row in wanted.values()):
            return wanted
        if time.monotonic() >= deadline:
            stuck = {
                task_id: (row["status"] if row else "missing")
                for task_id, row in wanted.items()
                if not row or row["status"] not in TERMINAL_AGENT_TASK_STATUSES
            }
            pytest.fail(f"P5 {what}: {len(stuck)} task(s) never reached a terminal state: {stuck}")
        await asyncio.sleep(.15)


async def wait_for_parent_dispatches(harness, parent_id, *, timeout: float = 10):
    """Poll the parent projection until it stops moving, then hand it back.

    Every child row is terminal and every Worker has exited before this runs, so
    a stable read is the durable truth. The settle window only exists because a
    parent continuation is queued *after* the projection write; it must never be
    needed to make a child's own outcome visible.
    """
    deadline = time.monotonic() + timeout
    records: list[dict] = []
    while True:
        records = (await harness.store.get_session(parent_id)).metadata["worker_dispatches"]
        if all(str(record.get("status") or "") == "completed" for record in records):
            return records
        if time.monotonic() >= deadline:
            return records
        await asyncio.sleep(.2)


async def drain_pending(harness, *, timeout: float = 30, worker_id="p5-drain") -> None:
    """Close the delivery loop: every stream message ends acknowledged.

    Safe only once the tasks are terminal - a redelivered command for a settled
    task short-circuits in ``AgentTaskDispatcher.handle`` and is acknowledged.
    """
    worker = RedisTaskWorker(
        harness.queue,
        consumer=worker_id,
        handler=lambda item: harness.dispatcher.handle(item, worker_id=worker_id),
        block_ms=0,
        reclaim_idle_ms=200,
    )
    deadline = time.monotonic() + timeout
    while True:
        await worker.run_once()
        if await pending_count(harness) == 0:
            return
        if time.monotonic() >= deadline:
            pytest.fail(
                f"P5 delivery gate: {await pending_count(harness)} deliverie(s) stayed "
                f"unacknowledged: {await harness.queue.pending()}"
            )
        await asyncio.sleep(.1)


async def pump_until_terminal(harness, task_ids, *, timeout, worker_id="p5-main"):
    """Drive the in-process Worker while polling for terminal task rows.

    Returns ``(rows, pump_errors)``. The same-session FIFO rule defers a later
    input while its earlier sibling is unsettled, which leaves that delivery
    unacknowledged until ``reclaim_idle_ms`` has passed; the pump therefore runs
    for the whole level instead of once per delivery.
    """
    errors: list[str] = []
    stop = asyncio.Event()
    worker = RedisTaskWorker(
        harness.queue,
        consumer=worker_id,
        handler=lambda item: harness.dispatcher.handle(item, worker_id=worker_id),
        block_ms=100,
        reclaim_idle_ms=RECLAIM_IDLE_MS,
    )

    async def pump():
        while not stop.is_set():
            try:
                await worker.run_once()
            except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
                errors.append(f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(.05)

    pump_task = asyncio.create_task(pump())
    try:
        deadline = time.monotonic() + timeout
        while True:
            rows = await task_rows(harness)
            wanted = {task_id: rows.get(task_id) for task_id in task_ids}
            if all(row and row["status"] in TERMINAL_AGENT_TASK_STATUSES for row in wanted.values()):
                return wanted, errors
            if time.monotonic() >= deadline:
                stuck = {
                    task_id: (row["status"] if row else "missing")
                    for task_id, row in wanted.items()
                    if not row or row["status"] not in TERMINAL_AGENT_TASK_STATUSES
                }
                pytest.fail(
                    f"P5 ladder: {len(stuck)} task(s) never reached a terminal state "
                    f"after {timeout}s: {stuck}"
                )
            await asyncio.sleep(.15)
    finally:
        stop.set()
        worker.stop()
        try:
            await asyncio.wait_for(pump_task, 10)
        except (TimeoutError, asyncio.CancelledError):
            pump_task.cancel()
            await asyncio.gather(pump_task, return_exceptions=True)


async def run_ladder_case(harness, fleet) -> None:
    """One concurrency level: two messages per session spread over N Worker processes."""
    level = harness.concurrency
    session_count = max(4, 2 * level)
    fleet.start()

    async def submit_pair(client, index):
        session_id = await create_session(harness, f"P5 ladder session {index}")
        first = await post_message(
            client,
            session_id,
            message_payload(f"ladder {level} first {index}", f"p5-{level}-{index}-a"),
        )
        # A follow-up input on a session that already owns pending work must use
        # queued submission, otherwise the store answers 409 by contract.
        second = await post_message(
            client,
            session_id,
            message_payload(
                f"ladder {level} second {index}",
                f"p5-{level}-{index}-b",
                submit_mode="queued",
            ),
        )
        return [first, second]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app),
        base_url="http://test",
    ) as client:
        nested = await asyncio.gather(*(
            submit_pair(client, index) for index in range(session_count)
        ))
    task_ids = [task_id for pair in nested for task_id in pair]
    expected = 2 * session_count
    assert len(set(task_ids)) == expected, "each submission must own exactly one durable task"

    # Consumers are up before delivery, so the spawned processes cannot be
    # starved by the faster in-process one.
    await fleet.wait_ready()
    assert await relay_all(harness) == expected, "every task must reach the Agent stream"

    rows, pump_errors = await pump_until_terminal(
        harness,
        task_ids,
        timeout=LADDER_PUMP_TIMEOUT_SECONDS,
    )
    codes = await fleet.shutdown()
    assert set(codes) == {0}, f"Worker processes exited with {codes}"
    assert pump_errors == [], f"the in-process Worker raised while draining: {pump_errors}"
    await drain_pending(harness)

    # Gate a) zero loss, and exactly one terminal state per task.
    statuses = await fetch_rows(
        harness,
        "SELECT status, count(*) AS count "
        f"FROM {harness.settings.database.postgres_agent_task_table} GROUP BY status",
    )
    counts = {row["status"]: int(row["count"]) for row in statuses}
    assert counts == {"completed": expected}, (
        f"loss gate: expected {expected} completed tasks, got {counts}"
    )
    assert all(row["status"] == "completed" for row in rows.values())

    # Gate b) one claim row per task with attempt_no == 1 => no replayed execution.
    attempts = harness.settings.database.postgres_agent_attempt_table
    repeated = await fetch_rows(
        harness,
        f"SELECT task_id, count(*) AS claims FROM {attempts} "
        "GROUP BY task_id HAVING count(*) > 1",
    )
    assert repeated == [], f"duplicate-execution gate: repeated claims on {repeated}"
    bumped = await fetch_rows(
        harness,
        f"SELECT task_id, max(attempt_no) AS attempt_no FROM {attempts} "
        "GROUP BY task_id HAVING max(attempt_no) > 1",
    )
    assert bumped == [], f"duplicate-execution gate: retried attempts {bumped}"
    attempt_total = await fetch_rows(harness, f"SELECT count(*) AS count FROM {attempts}")
    assert int(attempt_total[0]["count"]) == expected
    assert {row["attempt_no"] for row in rows.values()} == {1}
    orphan_attempts = await fetch_rows(
        harness,
        f"SELECT a.id FROM {attempts} a LEFT JOIN "
        f"{harness.settings.database.postgres_agent_task_table} t ON a.task_id = t.id "
        "WHERE t.id IS NULL",
    )
    assert orphan_attempts == [], f"claims without a owning task: {orphan_attempts}"

    # Gate c) no cross-session trace mixing: every turn id in a session's FlowEvent
    # stream must be a turn that session owns, and no turn id may span sessions.
    event_turns = await fetch_rows(
        harness,
        "SELECT session_id, "
        "COALESCE(payload->'_harness_context'->>'turn_id', payload->>'turn_id') AS turn_id "
        f"FROM {harness.settings.database.postgres_event_table} "
        "GROUP BY 1, 2 HAVING COALESCE("
        "payload->'_harness_context'->>'turn_id', payload->>'turn_id') IS NOT NULL",
    )
    owned = await fetch_rows(
        harness,
        "SELECT session_id, turn_id "
        f"FROM {harness.settings.database.postgres_agent_task_table} GROUP BY 1, 2",
    )
    owners: dict[str, set[str]] = {}
    for row in owned:
        owners.setdefault(row["session_id"], set()).add(row["turn_id"])
    foreign = [
        (row["session_id"], row["turn_id"])
        for row in event_turns
        if row["turn_id"] not in owners.get(row["session_id"], set())
    ]
    assert foreign == [], f"trace-mixing gate: events carry another session's turn {foreign[:10]}"
    shared: dict[str, set[str]] = {}
    for row in event_turns:
        shared.setdefault(row["turn_id"], set()).add(row["session_id"])
    crossed = sorted(turn for turn, session_ids in shared.items() if len(session_ids) > 1)
    assert crossed == [], f"trace-mixing gate: turn id reused across sessions {crossed[:10]}"
    assert set(owners) <= {row["session_id"] for row in event_turns}, (
        "every session must own a persisted trace"
    )

    # Gate d) the ladder really spread over separate Worker processes.
    workers = {row["worker_id"] for row in rows.values()} - {None}
    assert workers, "completed tasks must record the Worker that executed them"
    spawned = set(fleet.worker_ids) & workers
    assert spawned, f"no task was executed by a spawned Worker process, only {workers}"
    assert len(workers) >= (2 if level > 1 else 1), (
        f"concurrency gate: level N={level} spread over only {workers}"
    )


@pytest_asyncio.fixture
async def p5_harness(request):
    level = int(getattr(request, "param", 1))
    if not (_TEST_CONFIG.run_live_postgres_tests and _TEST_CONFIG.run_live_p5_acceptance):
        pytest.skip("requires real PostgreSQL + Redis and RUN_LIVE_P5_ACCEPTANCE")
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    tables = [f"p5_test_{field}_{suffix}" for field in TABLE_FIELDS]
    for field, table in zip(TABLE_FIELDS, tables):
        setattr(settings.database, f"postgres_{field}_table", table)
    prefix = f"p5_test_{suffix}"
    settings.orchestration.agent_task_stream = f"{prefix}:tasks:agent"
    settings.orchestration.agent_task_control_poll_seconds = .05
    settings.orchestration.session_sse_poll_seconds = .05
    settings.orchestration.coordinator_durable_children = True
    settings.orchestration.coordinator_max_workers = max(4, level)
    redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
    await resources.connect()
    # One admission slot per concurrent Worker plus the in-process driver. A
    # deferral at admission is legal, but it would burn an attempt number, and
    # this ladder gate is about replayed *execution*, not capacity back-pressure.
    await resources.configure_quota(scope="resource_type", identifier="agent", limit=level + 2)
    runtime = LadderRuntime()
    runtime.delay_range = (0.0, .02)
    store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
    outbox = PostgresTaskOutbox(settings)
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=settings.orchestration.agent_task_stream,
        group=WORKER_GROUP,
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
            title="P5 acceptance session",
            status=SessionStatus.idle,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        app = FastAPI()
        app.state.session_service = sessions
        app.state.agent_task_dispatcher = dispatcher
        app.state.settings = settings
        app.include_router(router)
        yield SimpleNamespace(
            settings=settings,
            prefix=prefix,
            tables=tables,
            redis=redis,
            resources=resources,
            runtime=runtime,
            store=store,
            tasks=tasks,
            sessions=sessions,
            dispatcher=dispatcher,
            outbox=outbox,
            queue=queue,
            app=app,
            session_id=session_id,
            concurrency=level,
        )
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        _drop_tables(settings, list(reversed(tables)))


@pytest.mark.asyncio
@pytest.mark.parametrize("p5_harness", CONCURRENCY_LEVELS, indirect=True)
async def test_concurrency_ladder_no_duplicate_no_loss(p5_harness):
    h = p5_harness
    level = h.concurrency
    fleet = WorkerFleet(
        h,
        count=level,
        options={"delay_range": [0.0, .05], "block_ms": 200, "deadline_seconds": 110},
    )
    try:
        await asyncio.wait_for(run_ladder_case(h, fleet), LEVEL_TIMEOUT_SECONDS)
    except TimeoutError:
        pytest.fail(
            f"P5 concurrency ladder level N={level} exceeded the "
            f"{LEVEL_TIMEOUT_SECONDS}s budget"
        )
    finally:
        await fleet.shutdown()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def serve_app(app):
    """Serve the harness app over real TCP so SSE is streamed, not buffered.

    httpx's ASGITransport accumulates the whole response body before returning,
    so an open-ended ``text/event-stream`` can only be exercised over a socket.
    """
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        access_log=False,
        timeout_graceful_shutdown=1,
    ))
    task = asyncio.create_task(server.serve())
    started = time.monotonic()
    try:
        while not server.started:
            if task.done():
                await task  # surface a bind/start failure instead of hanging
            assert time.monotonic() - started < 30, "uvicorn did not start within 30s"
            await asyncio.sleep(.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 15)
        except (TimeoutError, asyncio.CancelledError):
            server.force_exit = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def collect_sse(base_url, session_id, *, cursor, received, stop_types, timeout):
    """Read one SSE connection until a stop event lands in ``received``."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        async with client.stream(
            "GET",
            f"{base_url}/sessions/{session_id}/events",
            params={"last_event_id": cursor} if cursor else {},
        ) as response:
            assert response.status_code == 200, f"SSE handshake failed with {response.status_code}"
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue  # keep-alive comments carry no facts
                event = json.loads(line[len("data:"):].strip())
                received.append(event)
                if event["type"] in stop_types:
                    return received
    return received


async def run_sse_case(h) -> None:
    h.runtime.finish = asyncio.Event()
    receipt = await h.dispatcher.submit(
        h.session_id,
        message_payload("stream me to three clients", "p5-sse-1"),
    )
    task_id = receipt.task.id
    turn_id = receipt.task.turn_id
    assert await relay_all(h) == 1
    deliveries = await fetch_rows(
        h,
        f"SELECT payload FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
        (f"agent_turn:{task_id}",),
    )
    assert deliveries, "the turn command must be in the outbox before delivery"

    async with serve_app(h.app) as base_url:
        executing = asyncio.create_task(
            h.dispatcher.handle(
                QueuedTask("p5-sse-delivery", deliveries[0]["payload"]),
                worker_id="p5-sse-executor",
            )
        )
        collectors: list[asyncio.Task] = []
        streams: list[list[dict]] = []
        try:
            await asyncio.wait_for(h.runtime.entered.wait(), 20)
            persisted = await h.store.list_events(h.session_id)
            started_event = next(event for event in persisted if event.type == "turn.started")
            assert started_event.turn_id == turn_id, "turn.started must name the owning turn"
            # Three starting cursors: from the beginning, after the first event,
            # and after the second one - so each connection replays its own window.
            cursors = ["", persisted[0].id, persisted[1].id]
            for cursor in cursors:
                received: list[dict] = []
                streams.append(received)
                collectors.append(asyncio.create_task(collect_sse(
                    base_url,
                    h.session_id,
                    cursor=cursor,
                    received=received,
                    stop_types={"turn.completed"},
                    timeout=40,
                )))
            # Hold the turn open until all three subscribers have seen it start.
            started = time.monotonic()
            while not all(any(event["type"] == "turn.started" for event in stream) for stream in streams):
                assert not any(collector.done() for collector in collectors), (
                    "an SSE subscriber ended before the running turn reached it"
                )
                assert time.monotonic() - started < 20, "SSE clients never saw turn.started"
                await asyncio.sleep(.05)
            h.runtime.finish.set()
            await asyncio.wait_for(
                asyncio.gather(*collectors),
                timeout=40,
            )
        finally:
            h.runtime.finish.set()
            for collector in collectors:
                if not collector.done():
                    collector.cancel()
            await asyncio.gather(*collectors, return_exceptions=True)
            await executing

    assert (await h.tasks.get(task_id)).status == "completed"
    persisted = await h.store.list_events(h.session_id)
    order = [event.id for event in persisted]
    completed_at = next(index for index, event in enumerate(persisted) if event.type == "turn.completed")
    expected_full = order[: completed_at + 1]
    critical = {"turn.started", "turn.completed"}

    for cursor, received in zip(cursors, streams):
        types = {event["type"] for event in received}
        assert critical <= types, f"SSE loss gate: cursor {cursor!r} missed {critical - types}"
        assert all(event["session_id"] == h.session_id for event in received)
        index = order.index(received[0]["id"])
        assert [event["id"] for event in received] == order[index: completed_at + 1], (
            f"SSE loss gate: the stream from cursor {cursor!r} is not the intact "
            "suffix of the persisted trace"
        )
        last = received[-1]
        assert last["type"] == "turn.completed" and last["turn_id"] == turn_id
        # A connection's cursor is its own state: it always equals the last event
        # it delivered, never another client's.
        assert last["id"] == expected_full[-1]

    # Independent windows over one durable trace.
    assert len({tuple(event["id"] for event in stream) for stream in streams}) == 3
    assert streams[0][0]["id"] == expected_full[0]
    assert streams[1][0]["id"] == order[1]
    assert streams[2][0]["id"] == order[2]
    assert len(streams[0]) > len(streams[1]) > len(streams[2])


@pytest.mark.asyncio
@pytest.mark.parametrize("p5_harness", [1], indirect=True)
async def test_sse_multiple_clients_independent(p5_harness):
    h = p5_harness
    try:
        await asyncio.wait_for(run_sse_case(h), 90)
    except TimeoutError:
        pytest.fail("P5 SSE fan-out exceeded the 90s budget")


async def run_coordinator_case(h, fleet) -> None:
    parent_id = await create_session(
        h,
        "P5 coordinator parent",
        session_mode=SessionMode.coordinator,
        runtime_mode=RuntimeMode.interactive,
    )
    specs = [
        {
            "task_id": f"p5-child-{index}",
            "description": f"P5 child {index}",
            "prompt": f"produce child plan {index}",
            "agent_key": "qa-planner",
        }
        for index in range(3)
    ]

    runtime = LadderRuntime()
    store = PostgresSessionStore(h.settings)
    tasks = PostgresAgentTaskStore(h.settings, store)
    modes = ModeRegistry()
    sessions = SessionService(
        store,
        InputOrchestratorService(modes),
        runtime,
        modes,
        turn_execution_heartbeat_seconds=.1,
    )
    sessions.set_agent_task_store(tasks)
    dispatcher = AgentTaskDispatcher(h.settings, tasks, sessions, h.resources)
    sessions.set_agent_task_dispatcher(dispatcher)
    coordinator = CoordinatorRuntimeService(
        settings=h.settings,
        store=store,
        session_service=sessions,
        agent_registry=AgentRegistry(),
    )
    coordinator.set_agent_task_store(tasks)
    coordinator.set_agent_task_dispatcher(dispatcher)
    dispatcher.set_coordinator_service(coordinator)

    dispatched: dict = {}

    class DispatchingRuntime(LadderRuntime):
        async def execute_turn(self, session, request, **kwargs):
            # This is exactly where the subagent-dispatch tool runs: inside the
            # parent turn, while its durable task is still owned by a Worker.
            if session.id == parent_id and not dispatched:
                dispatched.update(await coordinator.dispatch(
                    {"workers": specs},
                    {
                        "session_id": parent_id,
                        "turn_id": request.turn_id,
                        "trace_id": "p5-root-trace",
                    },
                ))
            return await super().execute_turn(session, request, **kwargs)

    sessions._runtime_service = DispatchingRuntime()
    receipt = await dispatcher.submit(
        parent_id,
        message_payload("orchestrate three durable workers", "p5-parent-1"),
    )
    parent_task_id = receipt.task.id
    assert await relay_all(h) == 1

    parent_worker = RedisTaskWorker(
        h.queue,
        consumer="p5-parent-main",
        handler=lambda item: dispatcher.handle(item, worker_id="p5-parent-main"),
        block_ms=0,
        reclaim_idle_ms=RECLAIM_IDLE_MS,
    )
    assert await parent_worker.run_once() == 1, "the parent turn must settle in one delivery"
    assert dispatched.get("ok") is True, f"coordinator dispatch failed: {dispatched}"
    assert dispatched["metrics"]["worker_count"] == 3, dispatched

    children = await h.tasks.list_children(parent_task_id)
    assert len(children) == 3
    assert {child.operation for child in children} == {"coordinator_child"}
    assert {child.parent_task_id for child in children} == {parent_task_id}
    child_ids = [child.id for child in children]
    parent = await h.tasks.get(parent_task_id)
    assert parent.status == "completed", "the parent turn settles while its children run"

    fleet.start()
    await fleet.wait_ready()
    assert await relay_all(h) == 3, "each child command needs exactly one delivery"
    rows = await wait_until_terminal(h, child_ids, timeout=80, what="coordinator children")
    codes = await fleet.shutdown()
    assert set(codes) == {0}, f"child Worker processes exited with {codes}"
    await drain_pending(h)

    assert {row["status"] for row in rows.values()} == {"completed"}
    settled_workers = {row["worker_id"] for row in rows.values()}
    assert settled_workers == set(fleet.worker_ids), (
        f"the three durable children must be finished by three different Workers: "
        f"{ {row['id']: row['worker_id'] for row in rows.values()} }"
    )

    attempts = h.settings.database.postgres_agent_attempt_table
    repeated = await fetch_rows(
        h,
        f"SELECT task_id, count(*) AS claims FROM {attempts} "
        "GROUP BY task_id HAVING count(*) > 1",
    )
    assert repeated == [], f"a coordinator child ran twice: {repeated}"

    # Exactly one parent continuation per settled child.
    all_rows = await task_rows(h)
    resumes = [row for row in all_rows.values() if row["operation"] == "coordinator_resume"]
    assert len(resumes) == 3, f"expected one parent resume per child, got {resumes}"
    assert {row["parent_task_id"] for row in resumes} == {parent_task_id}
    assert {row["turn_id"] for row in resumes} == {parent.turn_id}
    assert {row["session_id"] for row in resumes} == {parent_id}
    notifications = await fetch_rows(
        h,
        "SELECT count(*) AS count "
        f"FROM {h.settings.database.postgres_event_table} "
        "WHERE type = %s AND session_id = %s",
        ("worker.task_notification_received", parent_id),
    )
    assert int(notifications[0]["count"]) == 3

    # Completion order must not change the parent's outcome: whichever child
    # settled first, all three dispatch records end terminal and distinct.
    dispatch_records = await wait_for_parent_dispatches(h, parent_id)
    timing = {row["id"]: (row["worker_id"], str(row["updated_at"])) for row in rows.values()}
    assert len(dispatch_records) == 3, f"the parent lost a worker projection: {dispatch_records}"
    assert {record["task_id"] for record in dispatch_records} == {spec["task_id"] for spec in specs}
    assert {record["status"] for record in dispatch_records} == {"completed"}, (
        f"the parent projection is missing a settled child; children settled at {timing}"
    )
    assert {record["child_session_id"] for record in dispatch_records} == {
        row["session_id"] for row in rows.values()
    }
    # Three different Workers holding one child each also proves the children
    # overlapped: a serialised fan-out could never spread this way.


@pytest.mark.asyncio
@pytest.mark.parametrize("p5_harness", [3], indirect=True)
async def test_coordinator_parent_child_concurrency(p5_harness):
    h = p5_harness
    assert h.settings.orchestration.coordinator_durable_children is True
    fleet = WorkerFleet(
        h,
        count=3,
        options={
            "max_deliveries": 1,
            # Randomised per-child work keeps the three children finishing in an
            # arbitrary order, so the parent's result cannot depend on the order.
            "delay_range": [0.5, 0.5],
            "block_ms": 2_000,
            "deadline_seconds": 60,
        },
    )
    try:
        await asyncio.wait_for(run_coordinator_case(h, fleet), LEVEL_TIMEOUT_SECONDS)
    except TimeoutError:
        pytest.fail("P5 coordinator parent/child concurrency exceeded the 120s budget")
    finally:
        await fleet.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("p5_harness", [1], indirect=True)
async def test_dead_letter_traceable(p5_harness):
    """A command no Worker can parse must end up traceable in the dead letter stream.

    ``RedisTaskWorker`` only dead-letters once a delivery's retry count reaches
    ``max_retries`` (``run_once`` -> ``RedisTaskQueue.transfer(dead_letter_reason=...)``
    -> ``{stream}:dead_letter``), so this probe is redelivered a bounded number of
    times and the reason is recorded on the parked entry's ``failure_type`` field.
    """
    h = p5_harness
    dead_stream = f"{h.settings.orchestration.agent_task_stream}:dead_letter"
    assert await h.redis.xlen(dead_stream) == 0
    assert await relay_all(h) == 0, "no legitimate work may be in flight"

    marker = f"p5-dead-letter-{uuid4().hex[:8]}"
    # A missing task_id breaks the AgentTaskCommand contract: the Worker cannot
    # resolve which durable task the delivery owns at all.
    envelope = {
        "schema_version": 1,
        "task_type": "agent_turn",
        "operation": "message",
        "session_id": h.session_id,
        "turn_id": f"turn-{marker}",
        "request_id": f"req-{marker}",
        "trace_id": f"trace-{marker}",
    }
    await h.queue.enqueue(envelope)

    worker = RedisTaskWorker(
        h.queue,
        consumer="p5-dead-letter-probe",
        handler=lambda item: h.dispatcher.handle(item, worker_id="p5-dead-letter-probe"),
        block_ms=0,
        max_retries=2,
        reclaim_idle_ms=5,
        retry_base_ms=20,
        retry_max_ms=40,
    )
    processed = 0
    parked = 0
    for _ in range(60):
        processed += await worker.run_once()
        parked = await h.redis.xlen(dead_stream)
        if parked:
            break
        await asyncio.sleep(.05)

    assert parked == 1, "the broken delivery must be parked exactly once"
    assert processed == 0, "an unparsable command must never be acknowledged as completed work"
    entries = await h.redis.xrange(dead_stream)
    dead_id, fields = entries[0]
    assert dead_id, "the parked delivery must carry its own stream id for operators"
    body = json.loads(fields["payload"])
    assert body["turn_id"] == envelope["turn_id"], (
        "the dead letter entry must still carry the original payload"
    )
    assert body["request_id"] == envelope["request_id"]
    assert body["session_id"] == h.session_id
    assert "task_id" not in body
    assert fields["failure_type"] == "ValidationError", (
        f"the dead letter must record why it was parked, got {fields}"
    )
    assert int(fields["retry_count"]) >= 2
    pending = await h.redis.xpending(h.queue.stream, WORKER_GROUP)
    assert int(pending["pending"]) == 0, (
        f"parking a delivery must retire the original: {pending}"
    )
    assert await task_rows(h) == {}, "a dead lettered envelope must not create durable task rows"
    assert h.runtime.calls == [], "a dead lettered envelope must not reach the runtime"

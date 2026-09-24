from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.application.observability import business_metrics, metrics_collector
from src.application.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    render,
    reset_registry,
)
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_agent_task_dispatch import Runtime, build_services, payload
from tests.test_live_postgres_concurrency import _drop_tables

LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')
METRIC_TYPES = (Counter, Gauge, Histogram)


@pytest.fixture(autouse=True)
def clean_registry():
    """Business call sites resolve handles through the registry, so a reset is safe."""
    reset_registry()
    yield
    reset_registry()


def samples(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    """Flatten the exposition into {(name, sorted label pairs): value}."""
    values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        rendered, _, value = line.rpartition(" ")
        if rendered.endswith("}"):
            name, _, blocks = rendered.partition("{")
            labels = tuple(sorted(
                (found.group("key"), found.group("value")) for found in LABEL_RE.finditer(blocks)
            ))
        else:
            name, labels = rendered, ()
        values[(name, labels)] = float(value)
    return values


def accessor_names() -> list[str]:
    """Every zero-argument handle exported by business_metrics."""
    found: list[str] = []
    for name, value in vars(business_metrics).items():
        if not name.islower() or isinstance(value, type):
            continue
        try:
            metric = value()
        except Exception:
            continue
        if isinstance(metric, METRIC_TYPES):
            found.append(name)
    return found


def declared_names() -> set[str]:
    return {
        value
        for name, value in vars(business_metrics).items()
        if name.isupper() and isinstance(value, str) and value.startswith("qa_")
    }


def test_business_metric_handles_are_prefixed_unique_and_conflict_free():
    """Guard the P4-A rule: each name and label set is declared exactly once."""
    accessors = accessor_names()
    assert accessors, "business_metrics must expose metric handles"
    seen: dict[str, str] = {}
    for accessor_name in accessors:
        accessor = getattr(business_metrics, accessor_name)
        metric = accessor()
        assert metric.name.startswith("qa_"), accessor_name
        assert metric.help, accessor_name
        assert accessor() is metric, accessor_name
        assert metric.name not in seen, f"{metric.name} declared by {seen.get(metric.name)}"
        seen[metric.name] = accessor_name
    assert set(seen) == declared_names()


class FakeQueue:
    """The delivery surface a worker needs, without a Redis transport."""

    def __init__(self, stream: str) -> None:
        self.stream = stream
        self.group = "fake-group"
        self.deliveries: list[QueuedTask] = []
        self.transfers: list[str | None] = []

    async def reclaim(self, *, consumer: str, min_idle_ms: int, count: int = 100):
        taken, self.deliveries = self.deliveries[:count], self.deliveries[count:]
        return taken

    async def consume(self, *, consumer: str, count: int = 1, block_ms: int = 1000, stream_id: str = ">"):
        return []

    async def ready(self, task: QueuedTask, *, consumer: str) -> bool:
        return True

    async def touch(self, task: QueuedTask, *, consumer: str) -> bool:
        return True

    async def ack_owned(self, task: QueuedTask, *, consumer: str) -> int:
        return 1

    async def transfer(
        self, task: QueuedTask, *, consumer: str, retry_count: int,
        delay_ms: int = 0, dead_letter_reason: str | None = None,
    ) -> str | None:
        self.transfers.append(dead_letter_reason)
        return "successor-1"


@pytest.mark.asyncio
async def test_worker_exports_deferral_failure_and_iteration_time():
    stream = f"qa:tasks:metrics-{uuid4().hex[:8]}"
    queue = FakeQueue(stream)
    queue.deliveries = [
        QueuedTask("1-1", {"task_id": "task-deferred"}),
        QueuedTask("1-2", {"task_id": "task-failed"}),
    ]

    async def handler(task: QueuedTask) -> None:
        if task.payload["task_id"] == "task-deferred":
            raise TaskDeferred("resource waiting")
        raise RuntimeError("handler exploded")

    worker = RedisTaskWorker(
        queue, consumer="metrics-worker", handler=handler,
        batch_size=len(queue.deliveries), block_ms=0, max_retries=0,
    )
    assert await worker.run_once() == 0

    values = samples(render())
    stream_labels = (("stream", stream),)
    assert values[("qa_task_deferred_total", stream_labels)] == 1
    assert values[("qa_worker_errors_total", stream_labels)] == 1
    assert values[("qa_worker_iteration_seconds_count", stream_labels)] == 2
    # Retries were exhausted, so the envelope is handed to the dead letter destination.
    assert queue.transfers == ["RuntimeError"]


@pytest_asyncio.fixture
async def harness():
    """Random tables, Redis prefix and stream, mirroring the durable dispatch suite."""
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    fields = ("session", "message", "event", "snapshot", "approval", "task_outbox", "agent_task", "agent_attempt")
    tables = [f"metrics_test_{field}_{suffix}" for field in fields]
    for field, table in zip(fields, tables):
        setattr(settings.database, f"postgres_{field}_table", table)
    prefix = f"metrics_test_{suffix}"
    stream = f"{prefix}:tasks:agent"
    settings.orchestration.agent_task_stream = stream
    settings.orchestration.redis_task_consumer_group = "metrics-test"
    settings.orchestration.agent_task_control_poll_seconds = .05
    redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
    await resources.connect()
    await resources.configure_quota(scope="resource_type", identifier="agent", limit=2)
    runtime = Runtime()
    store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
    outbox = PostgresTaskOutbox(settings)
    queue = RedisTaskQueue(
        settings.database.redis_url, stream=stream, group="metrics-test", client=redis,
    )
    try:
        await store.initialize()
        await outbox.initialize()
        await tasks.initialize()
        await queue.connect()
        session_id = str(uuid4())
        await store.save_session(SessionRecord(
            id=session_id,
            title="Metrics business path",
            status=SessionStatus.idle,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        yield SimpleNamespace(
            settings=settings, prefix=prefix, stream=stream, redis=redis, store=store,
            tasks=tasks, sessions=sessions, dispatcher=dispatcher, outbox=outbox, queue=queue,
            session_id=session_id,
        )
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        _drop_tables(settings, list(reversed(tables)))


@pytest.mark.asyncio
async def test_submission_and_relay_export_queue_depth(harness):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload(key="metrics-client-1"))

    values = samples(render())
    assert values[(
        "qa_agent_tasks_submitted_total",
        (("queue", h.stream), ("task_type", "agent_turn")),
    )] == 1
    assert "qa_agent_tasks_finished_total" not in {name for name, _labels in values}

    assert await h.outbox.relay_once(h.queue) == 1
    values = samples(render())
    assert values[("qa_outbox_published_total", (("stream", h.stream),))] == 1
    # Every claimed row was published, so the durable backlog for this stream is empty.
    assert values[("qa_outbox_pending", (("stream", h.stream),))] == 0
    assert (await h.tasks.get(receipt.task.id)).status == "queued"


@pytest.mark.asyncio
async def test_worker_completion_exports_task_lifecycle_metrics(harness):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload(key="metrics-client-2"))
    assert await h.outbox.relay_once(h.queue) == 1
    worker = RedisTaskWorker(
        h.queue,
        consumer="metrics-worker",
        handler=lambda item: h.dispatcher.handle(item, worker_id="metrics-worker"),
        block_ms=0,
    )
    assert await worker.run_once() == 1
    assert (await h.tasks.get(receipt.task.id)).status == "completed"

    values = samples(render())
    stream_labels = (("stream", h.stream),)
    turn_labels = (("task_type", "agent_turn"),)
    assert values[("qa_task_claimed_total", stream_labels)] == 1
    assert values[("qa_worker_iteration_seconds_count", stream_labels)] == 1
    assert values[("qa_resource_acquire_total", (("resource_type", "agent"), ("result", "ok")))] == 1
    assert values[("qa_agent_task_wait_seconds_count", turn_labels)] == 1
    assert values[("qa_agent_task_execution_seconds_count", turn_labels)] == 1
    assert values[(
        "qa_agent_tasks_finished_total",
        (("status", "completed"), ("task_type", "agent_turn")),
    )] == 1
    assert "qa_agent_task_retries_total" not in {name for name, _labels in values}
    assert "qa_task_deferred_total" not in {name for name, _labels in values}


@pytest.mark.asyncio
async def test_collector_exports_pool_and_stream_depth(harness):
    h = harness
    await h.dispatcher.submit(h.session_id, payload(key="metrics-client-3"))
    assert await h.outbox.relay_once(h.queue) == 1

    await metrics_collector.collect_once(h.settings)

    values = samples(render())
    assert values[("qa_stream_length", (("stream", h.stream),))] == 1
    # Relayed but never delivered: the consumer group has nothing unacknowledged.
    assert values[("qa_stream_pending", (("stream", h.stream),))] == 0
    assert values[("qa_stream_length", (("stream", f"{h.stream}:dead_letter"),))] == 0
    assert values[("qa_redis_used_memory_bytes", ())] > 0
    assert values[("qa_pg_pool_size", ())] > 0
    assert ("qa_pg_pool_checked_out", ()) in values
    assert {
        dict(labels)["item"]
        for (name, labels) in values
        if name == "qa_collector_errors_total"
    } == set()


class _CompactionStore:
    def __init__(self, *, backlog: int | None = None, error: Exception | None = None) -> None:
        self._backlog = backlog
        self._error = error

    async def uncompacted_event_count(self) -> int:
        if self._error is not None:
            raise self._error
        assert self._backlog is not None
        return self._backlog


@pytest.mark.asyncio
async def test_collector_exports_compaction_backlog_from_watermark(harness):
    h = harness
    await metrics_collector.collect_once(
        h.settings, compaction_store=_CompactionStore(backlog=12)
    )
    assert samples(render())[("qa_compaction_lag_events", ())] == 12


@pytest.mark.asyncio
async def test_compaction_backlog_failure_does_not_stop_other_exports(harness):
    h = harness
    await metrics_collector.collect_once(
        h.settings, compaction_store=_CompactionStore(error=RuntimeError("postgres down"))
    )
    values = samples(render())
    assert values[("qa_collector_errors_total", (("item", "compaction_lag"),))] == 1
    assert values[("qa_redis_used_memory_bytes", ())] > 0
    assert values[("qa_compaction_lag_events", ())] == 0


class _LeaseManager:
    def __init__(
        self,
        *,
        usage: dict[str, int] | None = None,
        limits: dict[str, int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._usage = usage or {}
        self._limits = limits or {}
        self._error = error

    async def usage_snapshot(self) -> dict[str, int]:
        if self._error is not None:
            raise self._error
        return self._usage

    async def quota_limits(self) -> dict[str, int]:
        if self._error is not None:
            raise self._error
        return self._limits


@pytest.mark.asyncio
async def test_collector_exports_resource_occupancy_without_unbounded_labels(harness):
    h = harness
    await metrics_collector.collect_once(
        h.settings,
        lease_manager=_LeaseManager(
            usage={
                "resource_type:browser": 3,
                "resource_type:docker": 1,
                "project:11111111-1111-1111-1111-111111111111": 7,
                "run:22222222-2222-2222-2222-222222222222": 9,
            },
            limits={"resource_type:browser": 5},
        ),
    )

    values = samples(render())
    assert values[("qa_resource_quota_used", (("resource_type", "browser"),))] == 3
    assert values[("qa_resource_quota_limit", (("resource_type", "browser"),))] == 5
    # No ceiling was configured for docker, so only its occupancy is known.
    assert values[("qa_resource_quota_used", (("resource_type", "docker"),))] == 1
    assert ("qa_resource_quota_limit", (("resource_type", "docker"),)) not in values
    exported_types = {
        dict(labels)["resource_type"]
        for (name, labels) in values
        if name == "qa_resource_quota_used"
    }
    assert exported_types == {"browser", "docker"}


@pytest.mark.asyncio
async def test_occupancy_failure_does_not_stop_other_exports(harness):
    h = harness
    await metrics_collector.collect_once(
        h.settings, lease_manager=_LeaseManager(error=RuntimeError("redis down"))
    )
    values = samples(render())
    assert values[("qa_collector_errors_total", (("item", "resource_occupancy"),))] == 1
    assert values[("qa_redis_used_memory_bytes", ())] > 0
    assert "qa_resource_quota_used" not in {name for name, _labels in values}


@pytest.mark.asyncio
async def test_collector_exports_redis_slowlog_depth_and_worst_duration(harness):
    """Force one slow command so the export is proven against a real server."""
    h = harness
    client = Redis.from_url(h.settings.database.redis_url, decode_responses=True)
    previous = None
    try:
        try:
            previous = (await client.config_get("slowlog-log-slower-than"))["slowlog-log-slower-than"]
            await client.slowlog_reset()
            await client.config_set("slowlog-log-slower-than", 0)
        except Exception as exc:
            pytest.skip(f"Redis in this environment forbids CONFIG/SLOWLOG administration: {exc}")
        await client.ping()

        await metrics_collector.collect_once(h.settings)

        values = samples(render())
        assert values[("qa_redis_slowlog_entries", ())] >= 1
        assert values[("qa_redis_slowlog_max_duration_microseconds", ())] > 0
    finally:
        if previous is not None:
            await client.config_set("slowlog-log-slower-than", previous)
            await client.slowlog_reset()
        await client.aclose()


def test_commit_chokepoint_counts_only_real_transactions():
    """The exit commit after an explicit store commit is a no-op and must stay invisible."""
    from src.infrastructure.sqlalchemy_runtime import _commit

    class _Connection:
        def __init__(self, open_transaction: bool) -> None:
            self.open_transaction = open_transaction
            self.commits = 0

        def in_transaction(self) -> bool:
            return self.open_transaction

        def commit(self) -> None:
            self.commits += 1

    idle = _Connection(False)
    _commit(idle, "postgres")
    assert idle.commits == 1
    assert "qa_db_commits_total" not in render()

    busy = _Connection(True)
    _commit(busy, "postgres")
    assert busy.commits == 1
    assert 'qa_db_commits_total{database="postgres"}' in render()
    assert 'qa_db_commit_duration_seconds_count{database="postgres"}' in render()


def test_failed_commit_is_counted_and_reraised():
    from src.infrastructure.sqlalchemy_runtime import _commit

    class _Failing:
        def in_transaction(self) -> bool:
            return True

        def commit(self) -> None:
            raise RuntimeError("commit exploded")

    with pytest.raises(RuntimeError, match="commit exploded"):
        _commit(_Failing(), "mysql")
    exported = render()
    assert 'qa_db_commit_errors_total{database="mysql"}' in exported
    assert 'qa_db_commits_total{database="mysql"}' not in exported


def test_tool_dispatch_wrapper_exports_terminal_status_and_aborts():
    import asyncio

    from src.application.mcp.host.namespace import encode
    from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
    from src.schemas.tool_runtime import ModelToolCall

    class _Stub:
        def __init__(self, outcome) -> None:
            self.outcome = outcome

        async def _dispatch(self, tool, call, context):
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return SimpleNamespace(status=self.outcome)

    context = ToolExecutionContext(
        session_id="metrics-session",
        turn_id="metrics-turn",
        trace_id="metrics-trace",
        user_message="metrics",
        normalized_input="metrics",
        context_bundle={},
    )
    record = asyncio.run(
        ToolRuntimeService.execute(
            _Stub("completed"),
            SimpleNamespace(key=encode("srv", "tool")),
            ModelToolCall(id="metrics-call-1", name=encode("srv", "tool"), arguments={}),
            context,
        )
    )
    assert record.status == "completed"
    exported = render()
    assert 'qa_tool_calls_total{tool_key="mcp",status="completed"}' in exported
    assert 'qa_tool_call_seconds_count{tool_key="mcp"}' in exported

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ToolRuntimeService.execute(
                _Stub(asyncio.CancelledError()),
                SimpleNamespace(key="cli-executor"),
                ModelToolCall(id="metrics-call-2", name="cli-executor", arguments={}),
                context,
            )
        )
    aborted = render()
    assert 'qa_tool_calls_total{tool_key="cli-executor",status="aborted"}' in aborted
    assert 'qa_tool_call_seconds_count{tool_key="cli-executor"}' in aborted


class _LagClient:
    """Minimal stand-in exposing only what the lag export touches."""

    def __init__(self, groups=None, error: Exception | None = None) -> None:
        self._groups = groups or []
        self._error = error

    async def xlen(self, stream: str) -> int:
        return 0

    async def xpending(self, stream: str, group: str) -> dict[str, object]:
        return {"pending": 0}

    async def xinfo_groups(self, stream: str):
        if self._error is not None:
            raise self._error
        return self._groups


@pytest.mark.asyncio
async def test_consumer_lag_exports_group_lag_and_tolerates_older_redis():
    from src.application.observability.metrics_collector import _collect_stream

    await _collect_stream(
        _LagClient([{"name": "other", "lag": 99}, {"name": "g1", "lag": 7}]), "orders", group="g1"
    )
    assert 'qa_stream_consumer_lag{stream="orders"}' in render()
    assert samples(render())[(business_metrics.STREAM_CONSUMER_LAG, (("stream", "orders"),))] == 7.0

    # Redis < 7.0 omits the key and 7.0 reports None before the group has a cursor.
    reset_registry()
    await _collect_stream(_LagClient([{"name": "g1"}]), "legacy", group="g1")
    await _collect_stream(_LagClient([{"name": "g1", "lag": None}]), "fresh", group="g1")
    assert "qa_stream_consumer_lag" not in render()


@pytest.mark.asyncio
async def test_consumer_lag_failure_is_isolated_per_source():
    from src.application.observability.metrics_collector import _collect_stream

    await _collect_stream(_LagClient(error=RuntimeError("boom")), "broken", group="g1")
    exported = render()
    assert 'qa_collector_errors_total{item="stream_consumer_lag"}' in exported


@pytest.mark.asyncio
async def test_absent_consumer_group_is_idle_queue_not_a_fault():
    from src.application.observability.metrics_collector import _collect_stream

    await _collect_stream(
        _LagClient(error=Exception("NOGROUP No such key 'x' consumer group 'g'")),
        "ungrouped",
        group="g1",
    )
    assert "qa_stream_consumer_lag" not in render()
    assert 'item="stream_consumer_lag"' not in render()

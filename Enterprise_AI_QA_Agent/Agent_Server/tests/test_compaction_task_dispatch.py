from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.compaction_task_handler import CompactionTaskHandler
from src.runtime.task_deferred import TaskDeferred
from src.runtime.postgres_session_store import PostgresSessionStore
from src.schemas.session import ExecutionEvent


def test_terminal_session_event_writes_compaction_outbox_in_same_transaction(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement, parameters=None):
            calls.append((statement, parameters))

        def fetchone(self):
            return {"id": "event-1"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            calls.append(("commit", None))

    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_event_table="events",
            postgres_session_table="sessions",
            postgres_task_outbox_table="task_outbox",
        )
    )
    store = PostgresSessionStore.__new__(PostgresSessionStore)
    store._settings = settings
    store._compaction_outbox_table = "task_outbox"
    store._compaction_task_stream = "qa:tasks:compaction"
    store._assert_continuation_lease_sync = lambda *args: None
    store._assert_turn_lease_sync = lambda *args: None
    monkeypatch.setattr(
        "src.runtime.postgres_session_store.postgres_connect",
        lambda _settings: Connection(),
    )

    event = ExecutionEvent(
        id="event-1",
        type="turn.completed",
        session_id="session-1",
        turn_id="turn-1",
        trace_id="trace-1",
        timestamp=datetime.now(timezone.utc),
    )
    store._append_event_sync("session-1", event)

    outbox = next(
        parameters
        for statement, parameters in calls
        if "INSERT INTO task_outbox" in statement
    )
    assert outbox[0] == "compaction:session-1:event-1"
    assert outbox[1] == "qa:tasks:compaction"
    payload = json.loads(outbox[2])
    assert payload["task_type"] == "compaction_task"
    assert payload["session_id"] == "session-1"
    assert payload["turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_compaction_task_handler_defers_until_cursor_commit():
    class Hot:
        async def get_compaction_state(self, session_id):
            return {"status": "pending", "last_event_id": "e1"}

    class Worker:
        def __init__(self):
            self._hot = Hot()

        async def compact_session(self, **kwargs):
            return False

    handler = CompactionTaskHandler(Worker())
    with pytest.raises(TaskDeferred):
        await handler(
            QueuedTask(
                "1-0",
                {
                    "task_id": "task-1",
                    "session_id": "session-1",
                    "turn_id": "turn-1",
                    "trace_id": "trace-1",
                },
            )
        )


def _sweep_store(monkeypatch, candidates, calls):
    class Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement, parameters=None):
            calls.append((statement, parameters))

        def fetchall(self):
            return candidates

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return Cursor()

    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_event_table="events",
            postgres_compaction_table="compaction_records",
            postgres_task_outbox_table="task_outbox",
        )
    )
    store = PostgresSessionStore.__new__(PostgresSessionStore)
    store._settings = settings
    store._compaction_outbox_table = "task_outbox"
    store._compaction_task_stream = "qa:tasks:compaction"
    monkeypatch.setattr(
        "src.runtime.postgres_session_store.postgres_connect", lambda _settings: Connection()
    )
    return store


def test_idle_sweep_queues_only_sessions_with_unfolded_backlog(monkeypatch):
    import asyncio

    calls = []
    store = _sweep_store(
        monkeypatch,
        [
            {"session_id": "session-1", "id": "event-1", "event_sequence": 7},
            {"session_id": "session-2", "id": "event-2", "event_sequence": 9},
        ],
        calls,
    )

    assert asyncio.run(store.queue_idle_compaction_tasks(idle_seconds=900, limit=10)) == 2

    select_statement, select_parameters = next(
        (statement, parameters) for statement, parameters in calls if statement.lstrip().startswith("WITH")
    )
    assert select_parameters == (900, 10)
    # Idleness is resolved inside PostgreSQL so a naive datetime can never be compared
    # against a TIMESTAMPTZ column, and only a watermark below the last event qualifies.
    assert "now() - (%s || ' seconds')::interval" in select_statement
    assert "latest.event_sequence > COALESCE(watermark.watermark, 0)" in select_statement

    inserts = [parameters for statement, parameters in calls if "INSERT INTO task_outbox" in statement]
    assert [parameters[0] for parameters in inserts] == [
        "compaction:session-1:event-1",
        "compaction:session-2:event-2",
    ]
    payload = json.loads(inserts[0][2])
    assert payload["task_type"] == "compaction_task"
    assert payload["session_id"] == "session-1"
    assert payload["payload"] == {"source_event_id": "event-1", "source_event_type": "idle_sweep"}


def test_idle_sweep_is_inert_without_compaction_outbox_wiring(monkeypatch):
    import asyncio

    calls = []
    store = _sweep_store(monkeypatch, [{"session_id": "s", "id": "e", "event_sequence": 1}], calls)
    store._compaction_task_stream = None

    assert asyncio.run(store.queue_idle_compaction_tasks(idle_seconds=1, limit=5)) == 0
    assert calls == []


def test_backlog_sweeper_keeps_running_when_the_store_is_down():
    import asyncio

    from src.runtime.compaction_backlog_sweeper import CompactionBacklogSweeper

    class FailingStore:
        def __init__(self):
            self.calls = 0

        async def queue_idle_compaction_tasks(self, *, idle_seconds, limit):
            self.calls += 1
            raise RuntimeError("postgresql is down")

    with pytest.raises(ValueError, match="must be positive"):
        CompactionBacklogSweeper(FailingStore(), idle_seconds=0, interval_seconds=1)
    with pytest.raises(ValueError, match="must be positive"):
        CompactionBacklogSweeper(FailingStore(), idle_seconds=1, interval_seconds=0)

    store = FailingStore()
    sweeper = CompactionBacklogSweeper(store, idle_seconds=60, interval_seconds=0.01)

    async def scenario():
        loop = asyncio.get_running_loop()
        task = loop.create_task(sweeper.run())
        deadline = asyncio.get_running_loop().time() + 2
        while store.calls < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        sweeper.stop()
        await asyncio.wait_for(task, 2)
        return store.calls

    # run() must survive repeated store failures instead of killing the process.
    assert asyncio.run(scenario()) >= 2

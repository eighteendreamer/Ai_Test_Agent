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

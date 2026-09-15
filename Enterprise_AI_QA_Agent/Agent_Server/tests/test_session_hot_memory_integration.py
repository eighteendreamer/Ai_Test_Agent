from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from src.runtime.postgres_session_store import PostgresSessionStore
from src.schemas.session import ExecutionEvent


def test_postgres_session_store_writes_hot_memory_after_fact_commit(monkeypatch):
    calls = []

    class HotStore:
        async def append_event(self, session_id, event):
            calls.append((session_id, event["type"]))

    store = PostgresSessionStore.__new__(PostgresSessionStore)
    store._settings = object()
    store._queues = defaultdict(asyncio.Queue)
    store._hot_memory_store = HotStore()
    store._append_event_sync = lambda *args: None

    async def run():
        event = ExecutionEvent(type="test.started", session_id="s1", timestamp=datetime.now(timezone.utc))
        await store.append_event("s1", event)

    asyncio.run(run())
    assert calls == [("s1", "test.started")]

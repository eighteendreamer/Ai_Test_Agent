from __future__ import annotations

import asyncio

from src.runtime.postgres_task_outbox import PostgresTaskOutbox


def test_outbox_enqueue_is_idempotent(monkeypatch):
    calls = []

    class Cursor:
        rowcount = 1
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None): calls.append((sql, params))

    class Conn:
        def cursor(self): return Cursor()
        def commit(self): calls.append(("commit", None))

    monkeypatch.setattr("src.runtime.postgres_task_outbox.postgres_connect", lambda settings: _cm(Conn()))
    settings = type("S", (), {"database": type("D", (), {"postgres_task_outbox_table": "outbox"})()})()
    assert asyncio.run(PostgresTaskOutbox(settings).enqueue(event_key="task-1", stream="qa:tasks:test", payload={"task_id": "task-1"}))
    assert "ON CONFLICT (event_key) DO NOTHING" in calls[0][0]


class _cm:
    def __init__(self, value): self.value = value
    def __enter__(self): return self.value
    def __exit__(self, *args): pass

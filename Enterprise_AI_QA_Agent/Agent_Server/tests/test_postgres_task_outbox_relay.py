from __future__ import annotations

import asyncio

from src.runtime.postgres_task_outbox import PostgresTaskOutbox, TaskOutboxRecord


def test_relay_marks_success_and_preserves_failed(monkeypatch):
    settings = type("S", (), {"database": type("D", (), {"postgres_task_outbox_table": "outbox"})()})()
    outbox = PostgresTaskOutbox(settings)
    records = [
        TaskOutboxRecord(1, "ok", "qa:tasks:test", {"task_id": "ok"}, 1),
        TaskOutboxRecord(2, "bad", "qa:tasks:test", {"task_id": "bad"}, 1),
    ]
    marked = []
    failed = []

    async def claim(*, limit):
        return records

    async def mark_published(outbox_id):
        marked.append(outbox_id)

    async def mark_failed(outbox_id, *, error):
        failed.append((outbox_id, error))

    outbox.claim = claim
    outbox.mark_published = mark_published
    outbox.mark_failed = mark_failed

    class Queue:
        async def enqueue(self, payload):
            if payload["task_id"] == "bad":
                raise RuntimeError("redis unavailable")
            return "1-0"

    assert asyncio.run(outbox.relay_once(Queue())) == 1
    assert marked == [1]
    assert failed and failed[0][0] == 2

from __future__ import annotations

import asyncio

from src.runtime.postgres_task_outbox import PostgresTaskOutbox, TaskOutboxRecord


def test_relay_routes_each_record_to_its_declared_stream():
    settings = type("S", (), {"database": type("D", (), {"postgres_task_outbox_table": "outbox"})()})()
    outbox = PostgresTaskOutbox(settings)
    records = [
        TaskOutboxRecord(1, "a", "qa:tasks:test", {"task_id": "a"}, 1),
        TaskOutboxRecord(2, "b", "qa:tasks:cleanup", {"task_id": "b"}, 1),
    ]
    published = []

    async def claim(*, limit, relay_id, stream=None): return [records.pop(0)] if records else []
    async def mark_published(outbox_id, *, relay_id): published.append(outbox_id); return True
    async def mark_failed(outbox_id, *, error, relay_id): raise AssertionError(error)
    outbox.claim, outbox.mark_published, outbox.mark_failed = claim, mark_published, mark_failed

    class Queue:
        def __init__(self, stream): self.stream = stream
        async def enqueue(self, payload): return self.stream

    queues = {"qa:tasks:test": Queue("qa:tasks:test"), "qa:tasks:cleanup": Queue("qa:tasks:cleanup")}
    assert asyncio.run(outbox.relay_once(queues["qa:tasks:test"], queue_for_stream=queues.get)) == 2
    assert published == [1, 2]


def test_single_queue_rejects_mismatched_stream_without_publish():
    settings = type("S", (), {"database": type("D", (), {"postgres_task_outbox_table": "outbox"})()})()
    outbox = PostgresTaskOutbox(settings)
    published, failed = [], []
    records = [TaskOutboxRecord(1, "a", "stream-a", {"task_id": "a"}, 1)]
    async def claim(*, limit, relay_id, stream=None): return [records.pop(0)] if records else []
    async def mark_published(*args, **kwargs): published.append(1); return True
    async def mark_failed(*args, **kwargs): failed.append(1); return True
    outbox.claim, outbox.mark_published, outbox.mark_failed = claim, mark_published, mark_failed
    class Queue:
        stream = "stream-b"
        async def enqueue(self, payload): raise AssertionError("must not publish")
    assert asyncio.run(outbox.relay_once(Queue())) == 0
    assert published == [] and failed == [1]

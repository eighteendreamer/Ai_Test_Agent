from __future__ import annotations

import pytest

from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.redis_task_worker import RedisTaskWorker


class QueueStub:
    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.acked = []

    async def consume(self, **kwargs):
        return self.tasks

    async def ack(self, message_id):
        self.acked.append(message_id)
        return 1


@pytest.mark.asyncio
async def test_worker_acks_only_successful_tasks() -> None:
    queue = QueueStub([
        QueuedTask("1-0", {"task_id": "ok"}),
        QueuedTask("2-0", {"task_id": "bad"}),
    ])
    handled = []

    async def handler(task):
        handled.append(task.payload["task_id"])
        if task.payload["task_id"] == "bad":
            raise RuntimeError("failure")

    worker = RedisTaskWorker(queue, consumer="worker-1", handler=handler)
    assert await worker.run_once() == 1
    assert handled == ["ok", "bad"]
    assert queue.acked == ["1-0"]


@pytest.mark.asyncio
async def test_worker_propagates_cancellation() -> None:
    queue = QueueStub([QueuedTask("1-0", {"task_id": "cancel"})])

    async def handler(task):
        raise asyncio.CancelledError

    import asyncio

    worker = RedisTaskWorker(queue, consumer="worker-1", handler=handler)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_once()
    assert queue.acked == []

from __future__ import annotations

import pytest

from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.redis_task_worker import RedisTaskWorker


class RetryQueue:
    def __init__(self, task):
        self.tasks = [task]
        self.acked = []
        self.enqueued = []
        self.dead = []
        self.retry_counts = []

    async def reclaim(self, **kwargs): return []
    async def ready(self, task, **kwargs): return True
    async def touch(self, task, **kwargs): return True
    async def ack_owned(self, task, **kwargs): return await self.ack(task.message_id)

    async def transfer(self, task, *, consumer, retry_count, delay_ms, dead_letter_reason):
        if dead_letter_reason:
            successor = await self.dead_letter(task.payload, reason=dead_letter_reason)
        else:
            successor = await self.enqueue(task.payload)
            self.retry_counts.append(retry_count)
        await self.ack(task.message_id)
        return successor

    async def consume(self, **kwargs):
        tasks, self.tasks = self.tasks, []
        return tasks

    async def ack(self, message_id):
        self.acked.append(message_id)
        return 1

    async def enqueue(self, payload, **kwargs):
        self.enqueued.append(payload)
        return "retry-1"

    async def dead_letter(self, payload, *, reason):
        self.dead.append((payload, reason))
        return "dead-1"


@pytest.mark.asyncio
async def test_worker_requeues_failed_task_until_retry_limit():
    queue = RetryQueue(QueuedTask("1-0", {"task_id": "t1"}))
    worker = RedisTaskWorker(queue, consumer="w1", handler=lambda task: _fail(), max_retries=2)
    assert await worker.run_once() == 0
    assert queue.acked == ["1-0"]
    assert queue.enqueued[0] == {"task_id": "t1"}
    assert queue.retry_counts == [1]
    assert queue.dead == []


@pytest.mark.asyncio
async def test_worker_dead_letters_after_retry_limit():
    queue = RetryQueue(QueuedTask("1-0", {"task_id": "t1"}, retry_count=2))
    worker = RedisTaskWorker(queue, consumer="w1", handler=lambda task: _fail(), max_retries=2)
    await worker.run_once()
    assert queue.acked == ["1-0"]
    assert queue.enqueued == []
    assert queue.dead[0][0]["task_id"] == "t1"


async def _fail():
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_retry_publish_failure_must_not_ack_original():
    queue = RetryQueue(QueuedTask("1-0", {"task_id": "t1"}))
    async def unavailable(*args, **kwargs):
        raise ConnectionError("retry publish unavailable")
    queue.enqueue = unavailable
    worker = RedisTaskWorker(queue, consumer="w1", handler=lambda task: _fail())
    with pytest.raises(ConnectionError):
        await worker.run_once()
    assert queue.acked == []

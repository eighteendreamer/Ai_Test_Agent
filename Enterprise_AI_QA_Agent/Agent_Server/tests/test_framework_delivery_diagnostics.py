"""Observe PEL retention without ACK, retry or delivery-policy changes."""
from __future__ import annotations

import logging

import pytest

from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from tests.test_agent_task_dispatch import harness
from tests.test_redis_task_worker import QueueStub


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "ack"])
async def test_undelivered_or_unacked_work_has_safe_distinct_diagnostics_without_retry(caplog, phase):
    task = QueuedTask("1-0", {"task_id": "task", "content": "private-payload"}, retry_count=2, not_before_ms=123)

    class Queue(QueueStub):
        stream = "diagnostic-stream"

        def __init__(self):
            super().__init__([task])
            self.transfers, self.handled, self.calls = [], [], []

        async def ready(self, task, **kwargs):
            self.calls.append("ready")
            return phase != "ready"

        async def ack_owned(self, task, **kwargs):
            self.calls.append("ack")
            return 0

        async def transfer(self, task, **kwargs):
            self.transfers.append(kwargs)

    queue = Queue()

    async def handle(task):
        queue.handled.append(task)

    with caplog.at_level(logging.INFO, logger="src.runtime.redis_task_worker"):
        assert await RedisTaskWorker(queue, consumer="worker", handler=handle, block_ms=0).run_once() == 0
    name = "redis_task_delivery_not_ready" if phase == "ready" else "redis_task_ack_owner_lost"
    record, = [r for r in caplog.records if r.message == name]
    assert record.task_id == "task" and record.delivery_message_id == "1-0" and record.worker_id == "worker"
    assert record.delivery_stream == queue.stream and record.retry_count == 2 and record.not_before_ms == 123
    assert queue.handled == ([] if phase == "ready" else [task])
    assert queue.calls == (["ready"] if phase == "ready" else ["ready", "ack"])
    assert queue.acked == [] and queue.transfers == []
    assert "private-" not in caplog.text
    assert not any("private-" in str(value) for value in record.__dict__.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "ack"])
async def test_real_redis_retains_exact_pel_owner_and_wire_when_ready_or_ack_is_rejected(harness, caplog, phase):
    h = harness
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.prefix+":diagnostics", group="diagnostics", client=h.redis)
    await queue.connect()
    task_id = await queue.enqueue({"task_id": "diagnostic-task", "body": "private-wire"})
    original = await h.redis.xrange(queue.stream)
    ready, handled = queue.ready, []

    async def change_owner():
        assert await h.redis.xclaim(queue.stream, queue.group, "other-owner", 0, [task_id], justid=True) == [task_id]

    async def ownership_ready(task, **kwargs):
        if phase == "ready":
            await change_owner()
        return await ready(task, **kwargs)

    async def handle(task):
        handled.append(task.message_id)
        await change_owner()

    queue.ready = ownership_ready
    with caplog.at_level(logging.INFO, logger="src.runtime.redis_task_worker"):
        assert await RedisTaskWorker(queue, consumer="original-owner", handler=handle, block_ms=0).run_once() == 0
    pending, = await queue.pending()
    assert pending["message_id"] == task_id and pending["consumer"] == "other-owner"
    assert await h.redis.xrange(queue.stream) == original
    assert await h.redis.xlen(queue.stream+":dead_letter") == 0
    assert handled == ([] if phase == "ready" else [task_id])
    name = "redis_task_delivery_not_ready" if phase == "ready" else "redis_task_ack_owner_lost"
    record, = [r for r in caplog.records if r.message == name]
    assert record.delivery_message_id == task_id and record.delivery_stream == queue.stream
    assert record.retry_count == 0 and record.not_before_ms == 0
    assert "private-wire" not in caplog.text

from __future__ import annotations

import asyncio

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.redis_task_worker import RedisTaskWorker


class QueueStub:
    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.acked = []

    async def reclaim(self, **kwargs): return []
    async def ready(self, task, **kwargs): return True
    async def touch(self, task, **kwargs): return True
    async def ack_owned(self, task, **kwargs): return await self.ack(task.message_id)
    async def transfer(self, task, **kwargs): return None

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


@pytest.mark.asyncio
async def test_stop_finishes_current_and_does_not_start_next():
    queue = QueueStub([QueuedTask("1-0", {"task_id": "first"}), QueuedTask("2-0", {"task_id": "next"})])
    handled = []
    async def handler(task):
        handled.append(task.message_id)
        worker.stop()
    worker = RedisTaskWorker(queue, consumer="w", handler=handler)
    assert await worker.run_once() == 1
    assert handled == ["1-0"] and queue.acked == ["1-0"]
    assert await worker.run_once() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("successful_touches", [0, 1])
@pytest.mark.parametrize("failure,reason,error_type", [
    ("owner", "owner_changed", "DeliveryOwnershipLost"),
    ("transport", "transport_unavailable", "ConnectionError"),
    ("unexpected", "unexpected_error", "RuntimeError"),
])
async def test_heartbeat_failure_keeps_safe_source_diagnostics_and_delivery_policy(
        caplog, successful_touches, failure, reason, error_type):
    entered, settled = asyncio.Event(), asyncio.Event()

    class FailedHeartbeat(QueueStub):
        stream = "unit-heartbeat"

        def __init__(self):
            super().__init__([QueuedTask("1-0", {"task_id": "heartbeat-probe", "body": "private-payload-probe"})])
            self.touches = 0
            self.transfers = []

        async def touch(self, task, **kwargs):
            await entered.wait()
            self.touches += 1
            if self.touches <= successful_touches:
                return True
            if failure == "transport":
                raise RedisConnectionError("private-transport-probe")
            if failure == "unexpected":
                raise RuntimeError("private-unexpected-probe")
            return False

        async def transfer(self, task, **kwargs):
            self.transfers.append(kwargs)
            return None

    async def handler(task):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    queue = FailedHeartbeat()
    worker = RedisTaskWorker(queue, consumer="heartbeat-worker", handler=handler,
        reclaim_idle_ms=3, max_retries=0)
    assert await asyncio.wait_for(worker.run_once(), 2) == 0
    assert settled.is_set() and queue.acked == []
    assert len(queue.transfers) == int(failure == "unexpected")
    if queue.transfers:
        assert queue.transfers[0]["dead_letter_reason"] == "RuntimeError"
    records = [record for record in caplog.records if record.message == "redis_task_heartbeat_failed"]
    assert len(records) == 1
    record = records[0]
    assert record.task_id == "heartbeat-probe" and record.delivery_message_id == "1-0"
    assert record.worker_id == "heartbeat-worker" and record.delivery_stream == "unit-heartbeat"
    assert record.heartbeat_operation == "touch" and record.heartbeat_failure_reason == reason
    assert record.error_type == error_type and record.reclaim_idle_ms == 3
    assert record.successful_touches == successful_touches
    assert record.heartbeat_seconds >= record.touch_seconds >= 0
    if successful_touches:
        assert record.since_last_touch_seconds >= record.touch_seconds
    else:
        assert record.since_last_touch_seconds is None
    assert not any("private-" in str(value) for value in record.__dict__.values())
    assert "private-" not in caplog.text


@pytest.mark.asyncio
async def test_successful_heartbeat_does_not_emit_failure_or_change_ack(caplog):
    touched = asyncio.Event()

    class HealthyHeartbeat(QueueStub):
        async def touch(self, task, **kwargs):
            touched.set()
            return True

    async def handler(task):
        await touched.wait()

    queue = HealthyHeartbeat([QueuedTask("1-0", {"task_id": "healthy"})])
    worker = RedisTaskWorker(queue, consumer="healthy-worker", handler=handler, reclaim_idle_ms=3)
    assert await asyncio.wait_for(worker.run_once(), 2) == 1
    assert queue.acked == ["1-0"] and touched.is_set()
    assert not any(record.message == "redis_task_heartbeat_failed" for record in caplog.records)


@pytest.mark.asyncio
async def test_cancellation_settles_inflight_heartbeat_without_false_failure_or_ack(caplog):
    touching, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class InflightHeartbeat(QueueStub):
        async def touch(self, task, **kwargs):
            touching.set()
            await release.wait()
            return True

    async def handler(task):
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    queue = InflightHeartbeat([QueuedTask("1-0", {"task_id": "cancelled"})])
    worker = RedisTaskWorker(queue, consumer="cancelled-worker", handler=handler, reclaim_idle_ms=3)
    running = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(touching.wait(), 2)
        running.cancel()
        await asyncio.wait_for(settled.wait(), 2)
        assert not running.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 2)
        assert queue.acked == []
        assert not any(record.message == "redis_task_heartbeat_failed" for record in caplog.records)
    finally:
        release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("previous_touch", [None, .5])
def test_configured_handler_renders_heartbeat_diagnostics_without_opening_arbitrary_extras(monkeypatch, previous_touch):
    import io
    import json
    import logging

    from src.core.log_format import configure_logging

    output = io.StringIO()
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [logging.StreamHandler(output)])
    configure_logging(root.level)
    expected = {"task_id": "heartbeat-probe", "delivery_message_id": "1-0", "delivery_stream": "stream-probe",
        "heartbeat_operation": "touch", "heartbeat_failure_reason": "transport_unavailable",
        "error_type": "ConnectionError", "reclaim_idle_ms": 60, "heartbeat_seconds": .75,
        "touch_seconds": .25, "successful_touches": int(previous_touch is not None),
        "since_last_touch_seconds": previous_touch}
    private = "private-heartbeat-formatter-probe"
    logger = logging.getLogger("src.runtime.redis_task_worker")
    logger.warning("redis_task_heartbeat_failed", extra={**expected, "payload": private, "redis_url": private})
    logger.warning("ordinary_event", extra={"message_id": private, "stream": private, "failure_reason": private})
    lines = output.getvalue().splitlines()
    assert len(lines) == 2 and private not in output.getvalue()
    assert json.loads(lines[0].split(" diagnostics=", 1)[1]) == expected
    assert lines[1].endswith(": ordinary_event")


class TransferProbe(QueueStub):
    stream = "unit-execution-deadline"

    def __init__(self):
        super().__init__([QueuedTask("1-0", {"task_id": "deadline-probe", "body": "private-deadline-input"})])
        self.transfers = []

    async def transfer(self, task, **kwargs):
        self.transfers.append(kwargs)
        return "retry-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("max_retries", [0, 3])
@pytest.mark.parametrize("cancellation", ["raise", "return", "error"])
async def test_worker_forced_deadline_never_means_confirmed_failure_or_retry(caplog, max_retries, cancellation):
    queue = TransferProbe()
    entered, settled = asyncio.Event(), asyncio.Event()
    async def handler(task):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if cancellation == "return":
                return
            if cancellation == "error":
                raise RuntimeError("private-deadline-cleanup-error") from None
            raise
        finally:
            settled.set()
    worker = RedisTaskWorker(queue, consumer="deadline-worker", handler=handler,
        task_timeout_seconds=.05, max_retries=max_retries)
    assert await asyncio.wait_for(worker.run_once(), 2) == 0
    assert entered.is_set() and settled.is_set()
    assert queue.acked == [] and queue.transfers == []
    records = [r for r in caplog.records if r.message == "redis_task_execution_deadline_unconfirmed"]
    assert len(records) == 1
    record = records[0]
    assert record.recovery_reason == "execution_deadline_receipt_unknown"
    assert record.delivery_message_id == "1-0" and record.worker_id == "deadline-worker"
    assert record.task_id == "deadline-probe" and record.delivery_stream == queue.stream
    assert record.operation_seconds >= 0
    assert "private-" not in caplog.text


@pytest.mark.asyncio
async def test_handler_reported_timeout_keeps_existing_failure_transfer_policy(caplog):
    queue = TransferProbe()
    async def handler(task):
        raise TimeoutError("private-handler-timeout")
    worker = RedisTaskWorker(queue, consumer="handler-timeout-worker", handler=handler,
        max_retries=0, task_timeout_seconds=2)
    assert await worker.run_once() == 0
    assert len(queue.transfers) == 1 and queue.transfers[0]["dead_letter_reason"] == "TimeoutError"
    assert queue.acked == []
    assert not any(r.message == "redis_task_execution_deadline_unconfirmed" for r in caplog.records)
    assert "private-" not in caplog.text

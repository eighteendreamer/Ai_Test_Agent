from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.application.orchestration.redis_execution_dispatcher import RedisExecutionDispatcher
from src.core.request_context import get_request_context
from src.infrastructure.redis_task_queue import QueuedTask


class QueueStub:
    stream = "stream:test"
    async def enqueue(self, payload):
        self.payload = payload
        return "1-0"


@pytest.mark.asyncio
async def test_dispatch_enqueues_run_without_claiming_lease():
    run = SimpleNamespace(id="run-1", project_id="project-1", session_id="session-1", status="queued")
    runs = SimpleNamespace(get_record=lambda run_id: _return(run))
    dispatcher = RedisExecutionDispatcher(queue=QueueStub(), test_run_service=runs, execution_service=None)
    accepted = await dispatcher.dispatch("run-1")
    assert accepted.status == "queued"
    assert dispatcher._queue.payload["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_dispatch_records_outbox_intent_without_direct_redis_publish():
    run = SimpleNamespace(id="run-1", project_id="project-1", session_id="session-1", status="queued")
    runs = SimpleNamespace(get_record=lambda run_id: _return(run))
    class Outbox:
        async def ensure(self, **kwargs):
            self.payload = kwargs
            return type("Record", (), {"id": 42})()

    outbox = Outbox()
    dispatcher = RedisExecutionDispatcher(queue=QueueStub(), test_run_service=runs, execution_service=None, outbox=outbox)
    accepted = await dispatcher.dispatch("run-1")
    assert accepted.message_id == "outbox:42"
    assert outbox.payload["event_key"] == "test_run_dispatch:run-1"


@pytest.mark.asyncio
async def test_handle_claims_and_executes_each_item():
    run = SimpleNamespace(id="run-1", project_id="project-1", session_id="session-1", status="running")
    claim = SimpleNamespace(claims=[SimpleNamespace(item=SimpleNamespace(id="item-1"), attempt=SimpleNamespace(id="attempt-1", attempt_no=1), lease_token="lease-1")])
    class Runs:
        def __init__(self): self.calls = 0
        async def get_record(self, run_id):
            self.calls += 1
            return run if self.calls == 1 else SimpleNamespace(**{**run.__dict__, "status": "completed"})
        async def claim(self, run_id, payload):
            assert payload.worker_id == "worker-1"
            return claim
    class Execution:
        async def execute_item(self, item_id, payload):
            assert (item_id, payload.lease_token) == ("item-1", "lease-1")
            context = get_request_context()
            assert context is not None
            assert context.run_id == "run-1"
            assert context.run_item_id == "item-1"
            assert context.attempt_id == "attempt-1"
            assert context.worker_id == "worker-1"
            assert context.turn_id == "test-run-item:item-1:attempt:1"
    await RedisExecutionDispatcher(queue=QueueStub(), test_run_service=Runs(), execution_service=Execution()).handle(
        QueuedTask("1-0", {"task_type": "test_run", "task_id": "task-1", "request_id": "req-1", "trace_id": "trace-1", "project_id": "project-1", "session_id": "session-1", "run_id": "run-1"}),
        worker_id="worker-1",
    )
    assert get_request_context() is None


async def _return(value):
    return value

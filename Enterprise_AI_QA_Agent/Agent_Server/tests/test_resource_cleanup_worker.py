from __future__ import annotations

import asyncio

import pytest

from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.resource_cleanup_worker import (
    ResourceCleanupTaskHandler,
    ResourceCleanupWorker,
)
from src.runtime.resource_lease_manager import ExpiredResourceLease


class ReceiptStore:
    def __init__(self):
        self.statuses = []

    async def begin(self, *args, **kwargs):
        return not self.statuses or self.statuses[-1] not in {"completed", "skipped"}

    async def finish(self, task_id, *, worker_id, status, error_type=None):
        self.statuses.append(status)


class Manager:
    def __init__(self):
        self.reconciled = 0

    async def reap_expired(self, *, limit):
        return [expired_lease("docker", "docker-slot-1", "container-123")]

    async def reconcile_quota_usage(self):
        self.reconciled += 1
        return 1


def expired_lease(resource_type, resource_id, external_resource_id=None):
    return ExpiredResourceLease(
        resource_id=resource_id,
        resource_type=resource_type,
        external_resource_id=external_resource_id,
        project_id="project-1",
        session_id="session-1",
        turn_id="turn-1",
        run_id="run-1",
        run_item_id="item-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        lease_token="lease-1",
        fencing_token=7,
        request_id="request-1",
        trace_id="trace-1",
    )


@pytest.mark.asyncio
async def test_cleanup_worker_reaps_to_stream_and_reconciles_quota():
    manager = Manager()
    worker = ResourceCleanupWorker(manager, interval_seconds=1)
    assert await worker.run_once() == 1
    assert manager.reconciled == 1


@pytest.mark.asyncio
async def test_cleanup_task_handler_uses_external_resource_id():
    calls = []

    async def docker(lease):
        calls.append((lease.resource_id, lease.external_resource_id))

    store = ReceiptStore()
    handler = ResourceCleanupTaskHandler(store, {"docker": docker})
    await handler(QueuedTask("1-0", {
        "task_type": "cleanup_task",
        **expired_lease("docker", "docker-slot-1", "container-123").__dict__,
    }))
    assert calls == [("docker-slot-1", "container-123")]
    assert store.statuses == ["completed"]


@pytest.mark.asyncio
async def test_cleanup_task_handler_propagates_failure_for_stream_retry():
    async def fail(_lease):
        raise RuntimeError("docker unavailable")

    store = ReceiptStore()
    handler = ResourceCleanupTaskHandler(store, {"docker": fail})
    with pytest.raises(RuntimeError, match="docker unavailable"):
        await handler(QueuedTask("1-0", {
            "task_type": "cleanup_task",
            **expired_lease("docker", "docker-slot-1", "container-123").__dict__,
        }))
    assert store.statuses == ["failed"]


@pytest.mark.asyncio
async def test_missing_browser_adapter_is_failed_instead_of_acknowledged():
    store = ReceiptStore()
    handler = ResourceCleanupTaskHandler(store)
    with pytest.raises(NotImplementedError):
        await handler(QueuedTask("1-0", {
            "task_type": "cleanup_task",
            **expired_lease("browser", "browser-1", "session-browser-1").__dict__,
        }))
    assert store.statuses == ["failed"]


@pytest.mark.asyncio
async def test_receipt_failure_is_retried_and_duplicate_success_is_idempotent():
    store = ReceiptStore()
    calls = []

    async def docker(lease):
        calls.append(lease.external_resource_id)

    handler = ResourceCleanupTaskHandler(store, {"docker": docker})
    task = QueuedTask("1-0", {
        "task_type": "cleanup_task",
        **expired_lease("docker", "slot-1", "container-1").__dict__,
    })
    finish = store.finish

    async def failed_commit(*args, **kwargs):
        if kwargs["status"] == "completed":
            raise ConnectionError("PostgreSQL unavailable")
        await finish(*args, **kwargs)

    store.finish = failed_commit
    with pytest.raises(ConnectionError):
        await handler(task)
    store.finish = finish
    await handler(task)
    await handler(task)
    assert calls == ["container-1", "container-1"]
    assert store.statuses == ["failed", "completed"]


def test_cleanup_interval_must_be_positive():
    with pytest.raises(ValueError):
        ResourceCleanupWorker(Manager(), interval_seconds=0)

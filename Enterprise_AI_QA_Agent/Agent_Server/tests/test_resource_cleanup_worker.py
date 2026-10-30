from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import sql

from src.infrastructure.redis_task_queue import QueuedTask
from src.application.sessions.session_service import SessionService
from src.application.resources.session_resource_service import (
    SessionResourceService,
    reconcile_session_resource_cleanup,
)
from src.runtime.resource_cleanup_worker import (
    ResourceCleanupTaskHandler,
    ResourceCleanupWorker,
)
from src.runtime.postgres_resource_cleanup_store import (
    PostgresResourceCleanupStore,
    reconcile_cleanup_receipt,
)
from src.runtime.resource_lease_manager import ExpiredResourceLease
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import SessionStatus
from src.runtime.session_resource_store import InMemorySessionResourceStore
from src.schemas.session_resource import (
    SessionResourceKind,
    SessionResourceRecord,
    SessionResourceStatus,
    session_resource_cleanup_operation_id,
)


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
        self.finalized = []

    async def complete_cleanup(self, lease):
        self.finalized.append(lease.resource_id)
        return True

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
    manager = Manager()
    handler = ResourceCleanupTaskHandler(store, {"docker": docker}, manager=manager)
    await handler(QueuedTask("1-0", {
        "task_type": "cleanup_task",
        **expired_lease("docker", "docker-slot-1", "container-123").__dict__,
    }))
    assert calls == [("docker-slot-1", "container-123")]
    assert store.statuses == ["completed"]
    assert manager.finalized == ["docker-slot-1"]


@pytest.mark.asyncio
async def test_prepared_allocation_without_container_id_requires_cleanup():
    calls = []
    async def docker(lease): calls.append(lease.allocation_id)
    store = ReceiptStore()
    manager = Manager()
    lease = replace(expired_lease("docker", "slot"), allocation_id="a" * 32)
    handler = ResourceCleanupTaskHandler(store, {"docker": docker}, manager=manager)
    await handler(QueuedTask("1-0", {"task_type": "cleanup_task", **lease.__dict__}))
    assert calls == ["a" * 32]
    assert store.statuses == ["completed"]
    assert manager.finalized == ["slot"]


@pytest.mark.asyncio
async def test_cleanup_task_handler_propagates_failure_for_stream_retry():
    async def fail(_lease):
        raise RuntimeError("docker unavailable")

    store = ReceiptStore()
    manager = Manager()
    handler = ResourceCleanupTaskHandler(store, {"docker": fail}, manager=manager)
    with pytest.raises(RuntimeError, match="docker unavailable"):
        await handler(QueuedTask("1-0", {
            "task_type": "cleanup_task",
            **expired_lease("docker", "docker-slot-1", "container-123").__dict__,
        }))
    assert store.statuses == ["failed"]
    assert manager.finalized == []


@pytest.mark.asyncio
async def test_missing_browser_adapter_is_failed_instead_of_acknowledged():
    store = ReceiptStore()
    handler = ResourceCleanupTaskHandler(store, manager=Manager())
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

    handler = ResourceCleanupTaskHandler(store, {"docker": docker}, manager=Manager())
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


@pytest.mark.asyncio
async def test_pg_commit_then_redis_failure_replays_only_finalization():
    store = ReceiptStore()
    manager = Manager()
    calls = []
    async def docker(lease):
        calls.append(lease.external_resource_id)
    async def unavailable(lease):
        raise ConnectionError("Redis unavailable after PG commit")
    finalize = manager.complete_cleanup
    manager.complete_cleanup = unavailable
    handler = ResourceCleanupTaskHandler(store, {"docker": docker}, manager=manager)
    task = QueuedTask("1-0", {
        "task_type": "cleanup_task",
        **expired_lease("docker", "slot-1", "container-1").__dict__,
    })
    with pytest.raises(ConnectionError, match="after PG commit"):
        await handler(task)
    assert store.statuses == ["completed"]
    manager.complete_cleanup = finalize
    await handler(task)
    assert calls == ["container-1"]
    assert manager.finalized == ["slot-1"]


@pytest.mark.asyncio
async def test_cleanup_handler_does_not_call_external_adapter_for_in_progress_receipt():
    calls = []

    class InProgressStore:
        async def begin(self, *_args, **_kwargs):
            return "in_progress"

        async def finish(self, *_args, **_kwargs):
            raise AssertionError("an in-progress receipt must not be finalized")

    async def docker(_lease):
        calls.append(True)

    manager = Manager()
    handler = ResourceCleanupTaskHandler(InProgressStore(), {"docker": docker}, manager=manager)
    with pytest.raises(TaskDeferred, match="cleanup receipt is already running"):
        await handler(QueuedTask("1-0", {
            "task_type": "cleanup_task",
            **expired_lease("docker", "slot-in-progress", "container-in-progress").__dict__,
        }))
    assert calls == []
    assert manager.finalized == []


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_POSTGRES_TESTS") != "1",
    reason="set RUN_LIVE_POSTGRES_TESTS=1 for the durable cleanup receipt contract",
)
@pytest.mark.asyncio
async def test_postgres_cleanup_begin_does_not_reclaim_running_receipt():
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"cleanup-running-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-running", "container-running"),
                    lease_token=f"lease-{uuid4().hex}")
    try:
        results = await asyncio.gather(
            store.begin(task_id, lease, worker_id="worker-a"),
            store.begin(task_id, lease, worker_id="worker-b"),
        )
        assert sorted(results) == ["in_progress", "started"]
        result = await store.reconcile(task_id, lease)
        assert result.status == "unknown"
        assert result.reason_code == "receipt_running"
        assert result.attempts == 1
        assert result.worker_id in {"worker-a", "worker-b"}
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(
                sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_id,))


def test_cleanup_interval_must_be_positive():
    with pytest.raises(ValueError):
        ResourceCleanupWorker(Manager(), interval_seconds=0)


def test_running_cleanup_receipt_is_read_only_unknown_effect():
    lease = expired_lease("docker", "slot-unknown", "container-unknown")
    record = lease.__dict__.copy()
    record.pop("lease_token")
    result = reconcile_cleanup_receipt(
        "task-cleanup-unknown",
        lease,
        {
            "task_id": "task-cleanup-unknown",
            "record": record,
            "status": "running",
            "worker_id": "worker-a",
            "attempts": 1,
            "error_type": None,
            "completed_at": None,
        },
    )

    assert result.status == "unknown"
    assert result.receipt_status == "running"
    assert result.reason_code == "receipt_running"


def test_missing_cleanup_receipt_is_unknown_without_execution_authority():
    lease = expired_lease("docker", "slot-missing", "container-missing")
    result = reconcile_cleanup_receipt("task-cleanup-missing", lease, None)

    assert result.status == "unknown"
    assert result.receipt_status is None
    assert result.reason_code == "receipt_missing"


def test_cleanup_receipt_identity_drift_is_not_terminal_success():
    lease = expired_lease("docker", "slot-drift", "container-drift")
    record = lease.__dict__.copy()
    record.pop("lease_token")
    record["resource_id"] = "different-slot"
    result = reconcile_cleanup_receipt(
        "task-cleanup-drift",
        lease,
        {
            "task_id": "task-cleanup-drift",
            "record": record,
            "status": "completed",
            "worker_id": "worker-a",
            "attempts": 1,
            "error_type": None,
            "completed_at": None,
        },
    )

    assert result.status == "drifted"
    assert result.reason_code == "receipt_identity_drift"


@pytest.mark.parametrize(
    ("receipt_status", "expected_reason", "expected_terminal"),
    [
        ("completed", "receipt_completed", "completed"),
        ("skipped", "receipt_skipped", "skipped"),
    ],
)
def test_terminal_cleanup_receipt_is_known_only_after_identity_check(
    receipt_status, expected_reason, expected_terminal,
):
    lease = expired_lease("docker", "slot-terminal", "container-terminal")
    record = lease.__dict__.copy()
    record.pop("lease_token")
    result = reconcile_cleanup_receipt(
        "task-cleanup-terminal",
        lease,
        {
            "task_id": "task-cleanup-terminal",
            "record": record,
            "status": receipt_status,
            "worker_id": "worker-a",
            "attempts": 2,
            "error_type": None,
            "completed_at": None,
        },
    )

    assert result.status == "completed"
    assert result.reason_code == expected_reason
    assert result.terminal_status == expected_terminal


def test_failed_cleanup_receipt_remains_unknown():
    lease = expired_lease("docker", "slot-failed", "container-failed")
    record = lease.__dict__.copy()
    record.pop("lease_token")
    result = reconcile_cleanup_receipt(
        "task-cleanup-failed",
        lease,
        {
            "task_id": "task-cleanup-failed",
            "record": record,
            "status": "failed",
            "worker_id": "worker-a",
            "attempts": 2,
            "error_type": "ConnectionError",
            "completed_at": None,
        },
    )

    assert result.status == "unknown"
    assert result.reason_code == "receipt_failed"
    assert result.error_type == "ConnectionError"


@pytest.mark.asyncio
async def test_terminal_session_cleanup_surfaces_error_status_as_unknown_effect():
    async def cleanup_session(_session_id, *, reason):
        return [SimpleNamespace(
            kind=SessionResourceKind.docker_container,
            resource_key="container-incomplete",
            status=SessionResourceStatus.error,
        )]

    service = SessionService.__new__(SessionService)
    service._session_resource_service = SimpleNamespace(cleanup_session=cleanup_session)
    async def append_event(*_args, **_kwargs):
        return None
    service._store = SimpleNamespace(append_event=append_event)
    session = SimpleNamespace(
        id="session-cleanup-incomplete",
        status=SessionStatus.completed,
        metadata={},
        snapshot_count=0,
    )

    with pytest.raises(RuntimeError, match="resource cleanup"):
        await service._cleanup_session_resources_if_terminal(session)


@pytest.mark.asyncio
async def test_session_resource_cleanup_records_stable_operation_journal():
    store = InMemorySessionResourceStore()
    service = SessionResourceService(store)
    resource = await service.register(
        session_id="session-resource-journal",
        kind=SessionResourceKind.browser_profile,
        resource_key="profile-journal",
    )

    cleaned = await service.cleanup_session(resource.session_id, reason="session_completed")

    operation_id = session_resource_cleanup_operation_id(
        resource.session_id, resource.id, "session_completed",
    )
    assert cleaned[0].metadata["cleanup_operation_id"] == operation_id
    assert cleaned[0].metadata["cleanup_state"] == "completed"
    current = await store.list(resource.session_id)
    assert current[0].metadata["cleanup_operation_id"] == operation_id


@pytest.mark.asyncio
async def test_session_resource_cleanup_failure_keeps_unknown_operation_journal(monkeypatch):
    store = InMemorySessionResourceStore()
    service = SessionResourceService(store)
    resource = await service.register(
        session_id="session-resource-journal-failure",
        kind=SessionResourceKind.docker_container,
        resource_key="container-journal-failure",
    )

    async def fail(_container_name):
        raise ConnectionError("fixture-private-cleanup-failure")

    monkeypatch.setattr(service, "_cleanup_docker_container", fail)
    cleaned = await service.cleanup_session(resource.session_id, reason="session_completed")

    operation_id = session_resource_cleanup_operation_id(
        resource.session_id, resource.id, "session_completed",
    )
    assert cleaned[0].status == SessionResourceStatus.error
    assert cleaned[0].metadata["cleanup_operation_id"] == operation_id
    assert cleaned[0].metadata["cleanup_state"] == "unknown"
    assert "fixture-private" not in str(cleaned[0].metadata)


@pytest.mark.asyncio
async def test_session_resource_cleanup_does_not_retry_unknown_same_operation(caplog):
    calls = []

    async def cleanup(resource_key, *, allocation_id):
        calls.append((resource_key, allocation_id))

    store = InMemorySessionResourceStore()
    service = SessionResourceService(store, browser_cleanup=cleanup)
    resource = await service.register(
        session_id="session-resource-unknown-retry",
        kind=SessionResourceKind.browser_session,
        resource_key="browser-unknown-retry",
        metadata={"allocation_id": "allocation-unknown-retry"},
    )
    operation_id = session_resource_cleanup_operation_id(
        resource.session_id, resource.id, "session_completed",
    )
    await store.mark_status(
        resource.id,
        SessionResourceStatus.active,
        {
            "cleanup_operation_id": operation_id,
            "cleanup_state": "unknown",
            "cleanup_reason": "session_completed",
        },
    )

    with caplog.at_level(logging.WARNING):
        cleaned = await service.cleanup_session(resource.session_id, reason="session_completed")

    assert calls == []
    assert cleaned[0].status == SessionResourceStatus.active
    assert cleaned[0].metadata["cleanup_operation_id"] == operation_id
    assert cleaned[0].metadata["cleanup_state"] == "unknown"
    assert any(
        record.getMessage() == "session_resource_cleanup_unknown_requires_reconciliation"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_browser_cleanup_marks_same_allocation_released_but_not_replacement():
    store = InMemorySessionResourceStore()
    closed = []

    async def close_browser(resource_key, *, allocation_id):
        closed.append((resource_key, allocation_id))

    service = SessionResourceService(store, browser_cleanup=close_browser)
    resource = await service.register(
        session_id="session-browser-journal",
        kind=SessionResourceKind.browser_session,
        resource_key="browser-journal",
        metadata={"allocation_id": "allocation-a"},
    )
    cleaned = await service.cleanup_session(resource.session_id, reason="session_completed")

    assert closed == [("browser-journal", "allocation-a")]
    assert cleaned[0].status == SessionResourceStatus.released
    assert cleaned[0].metadata["cleanup_state"] == "completed"

    replacement = await store.save(resource.model_copy(update={
        "metadata": {"allocation_id": "allocation-b"},
        "status": SessionResourceStatus.active,
    }))
    assert replacement.metadata["allocation_id"] == "allocation-b"
    current = await service._cleanup_one(resource, "delayed_old_cleanup")
    assert current.metadata["allocation_id"] == "allocation-b"
    assert closed == [("browser-journal", "allocation-a")]


@pytest.mark.asyncio
async def test_browser_cleanup_failure_persists_error_journal_without_error_text():
    async def fail(_resource_key, *, allocation_id):
        raise ConnectionError(f"private-browser-failure-{allocation_id}")

    store = InMemorySessionResourceStore()
    service = SessionResourceService(store, browser_cleanup=fail)
    resource = await service.register(
        session_id="session-browser-journal-failure",
        kind=SessionResourceKind.browser_session,
        resource_key="browser-journal-failure",
        metadata={"allocation_id": "allocation-failure"},
    )

    cleaned = await service.cleanup_session(resource.session_id, reason="session_completed")
    current = (await store.list(resource.session_id))[0]

    assert cleaned[0].status == SessionResourceStatus.error
    assert current.status == SessionResourceStatus.error
    assert current.metadata["cleanup_state"] == "unknown"
    assert current.metadata["cleanup_error_type"] == "ConnectionError"
    assert "private-browser-failure" not in str(current.metadata)


@pytest.mark.asyncio
async def test_session_resource_reconciliation_is_read_only_and_known_after_journal():
    store = InMemorySessionResourceStore()
    service = SessionResourceService(store)
    resource = await service.register(
        session_id="session-resource-reconcile",
        kind=SessionResourceKind.browser_profile,
        resource_key="profile-reconcile",
    )
    await service.cleanup_session(resource.session_id, reason="session_completed")

    result = await service.reconcile_session_cleanup(
        resource.session_id, reason="session_completed",
    )

    assert len(result) == 1
    assert result[0].status == "completed"
    assert result[0].reason_code == "cleanup_completed"


@pytest.mark.asyncio
async def test_session_resource_reconciliation_keeps_missing_journal_unknown():
    store = InMemorySessionResourceStore()
    service = SessionResourceService(store)
    resource = await service.register(
        session_id="session-resource-reconcile-missing",
        kind=SessionResourceKind.browser_profile,
        resource_key="profile-reconcile-missing",
    )

    result = await service.reconcile_session_cleanup(
        resource.session_id, reason="session_completed",
    )

    assert result[0].status == "unknown"
    assert result[0].reason_code == "cleanup_not_started"


@pytest.mark.asyncio
async def test_session_resource_reconciliation_does_not_invoke_cleanup_for_unknown_effect():
    calls = []

    async def forbidden_cleanup(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("reconciliation must not invoke cleanup")

    store = InMemorySessionResourceStore()
    service = SessionResourceService(store, browser_cleanup=forbidden_cleanup)
    resource = await service.register(
        session_id="session-resource-reconcile-unknown",
        kind=SessionResourceKind.browser_session,
        resource_key="browser-reconcile-unknown",
        metadata={"allocation_id": "allocation-reconcile-unknown"},
    )
    operation_id = session_resource_cleanup_operation_id(
        resource.session_id, resource.id, "session_completed",
    )
    await store.mark_status(
        resource.id,
        SessionResourceStatus.active,
        {
            "cleanup_operation_id": operation_id,
            "cleanup_state": "unknown",
            "cleanup_reason": "session_completed",
        },
    )

    result = await service.reconcile_session_cleanup(
        resource.session_id, reason="session_completed",
    )

    assert calls == []
    assert result[0].status == "unknown"
    assert result[0].reason_code == "cleanup_unknown"


@pytest.mark.asyncio
async def test_session_resource_reconciliation_distinguishes_other_operation_from_identity_drift():
    store = InMemorySessionResourceStore()
    service = SessionResourceService(store)
    resource = await service.register(
        session_id="session-resource-reconcile-identity",
        kind=SessionResourceKind.browser_profile,
        resource_key="profile-reconcile-identity",
    )

    await service.cleanup_session(resource.session_id, reason="operator_requested")
    other = await service.reconcile_session_cleanup(
        resource.session_id, reason="session_completed",
    )
    assert other[0].status == "unknown"
    assert other[0].reason_code == "cleanup_other_operation"

    drifted = resource.model_copy(update={
        "metadata": {
            "cleanup_operation_id": "not-the-derived-operation",
            "cleanup_state": "completed",
            "cleanup_reason": "session_completed",
        },
        "status": SessionResourceStatus.released,
    })
    result = reconcile_session_resource_cleanup(drifted, "session_completed")
    assert result.status == "drifted"
    assert result.reason_code == "cleanup_operation_drift"


def test_session_resource_reconciliation_rejects_terminal_state_without_terminal_row():
    record = SessionResourceRecord(
        session_id="session-resource-reconcile-state-drift",
        kind=SessionResourceKind.browser_profile,
        resource_key="profile-reconcile-state-drift",
    )
    record = record.model_copy(update={
        "metadata": {
            "cleanup_operation_id": session_resource_cleanup_operation_id(
                record.session_id, record.id, "session_completed",
            ),
            "cleanup_state": "completed",
            "cleanup_reason": "session_completed",
        },
    })
    result = reconcile_session_resource_cleanup(record, "session_completed")

    assert result.status == "drifted"
    assert result.reason_code == "cleanup_state_drift"


def test_session_resource_reconciliation_handles_malformed_cleanup_state_as_drift():
    record = SessionResourceRecord(
        session_id="session-resource-reconcile-malformed",
        kind=SessionResourceKind.browser_profile,
        resource_key="profile-reconcile-malformed",
    )
    record = record.model_copy(update={
        "metadata": {
            "cleanup_operation_id": session_resource_cleanup_operation_id(
                record.session_id, record.id, "session_completed",
            ),
            "cleanup_state": [],
            "cleanup_reason": "session_completed",
        },
    })

    result = reconcile_session_resource_cleanup(record, "session_completed")

    assert result.status == "drifted"
    assert result.reason_code == "cleanup_state_drift"

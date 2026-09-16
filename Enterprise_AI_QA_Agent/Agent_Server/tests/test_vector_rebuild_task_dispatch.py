from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.application.context.memory_vector_rebuild_service import VectorRebuildResult
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.postgres_vector_rebuild_job_store import VectorRebuildJob
from src.runtime.vector_rebuild_task_handler import VectorRebuildTaskHandler


def _task(**updates) -> QueuedTask:
    payload = {
        "schema_version": 1,
        "task_id": "task-vector-1",
        "task_type": "vector_rebuild_task",
        "request_id": "req-1",
        "trace_id": "trace-1",
        "entity": "memory",
        "embedding_version": "emb_v1",
        "activate": True,
    }
    payload.update(updates)
    return QueuedTask("1-0", payload)


def _job(status: str) -> VectorRebuildJob:
    return VectorRebuildJob(
        task_id="task-vector-1",
        entity="memory",
        embedding_version="emb_v1",
        activate=True,
        status=status,
        dimension=4096,
        source_count=3,
        replicated=3,
        active_index="qa:vector:memory:emb_v1",
        attempts=1,
        worker_id=None,
        lease_expires_at=None,
        last_error_type=None,
        created_at=None,
        updated_at=None,
        completed_at=None,
    )


@pytest.mark.asyncio
async def test_vector_rebuild_handler_persists_progress_before_terminal_ack() -> None:
    async def rebuild(version, *, execute, activate, progress):
        assert version == "emb_v1" and execute and activate
        await progress(
            "dimension_detected",
            {"dimension": 4096, "source_count": 3, "replicated": 0},
        )
        await progress(
            "index_validating",
            {"dimension": 4096, "source_count": 3, "replicated": 3},
        )
        return VectorRebuildResult(
            "emb_v1",
            4096,
            3,
            3,
            "activated",
            "qa:vector:memory:emb_v1",
        )

    service = SimpleNamespace(rebuild=AsyncMock(side_effect=rebuild))
    jobs = SimpleNamespace(
        begin=AsyncMock(return_value=True),
        get=AsyncMock(),
        progress=AsyncMock(return_value=True),
        complete=AsyncMock(return_value=True),
        fail=AsyncMock(return_value=True),
    )
    handler = VectorRebuildTaskHandler(service, jobs, worker_id="worker-1")

    await handler(_task())

    assert [call.kwargs["status"] for call in jobs.progress.await_args_list] == [
        "dimension_detected",
        "index_validating",
    ]
    assert jobs.complete.await_args.kwargs["status"] == "index_activated"
    jobs.fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_rebuild_handler_acks_terminal_duplicate_without_rebuild() -> None:
    service = SimpleNamespace(rebuild=AsyncMock())
    jobs = SimpleNamespace(
        begin=AsyncMock(return_value=False),
        get=AsyncMock(return_value=_job("index_activated")),
    )
    handler = VectorRebuildTaskHandler(service, jobs, worker_id="worker-2")

    await handler(_task())

    service.rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_rebuild_handler_records_failure_for_worker_retry() -> None:
    service = SimpleNamespace(rebuild=AsyncMock(side_effect=ConnectionError("private")))
    jobs = SimpleNamespace(
        begin=AsyncMock(return_value=True),
        get=AsyncMock(),
        progress=AsyncMock(return_value=True),
        complete=AsyncMock(return_value=True),
        fail=AsyncMock(return_value=True),
    )
    handler = VectorRebuildTaskHandler(service, jobs, worker_id="worker-3")

    with pytest.raises(ConnectionError):
        await handler(_task())

    assert jobs.fail.await_args.kwargs == {
        "worker_id": "worker-3",
        "error_type": "ConnectionError",
    }
    jobs.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_rebuild_handler_rejects_message_without_durable_job() -> None:
    jobs = SimpleNamespace(
        begin=AsyncMock(return_value=False),
        get=AsyncMock(return_value=None),
    )
    handler = VectorRebuildTaskHandler(
        SimpleNamespace(rebuild=AsyncMock()), jobs, worker_id="worker-4",
    )

    with pytest.raises(ValueError, match="no durable job record"):
        await handler(_task())

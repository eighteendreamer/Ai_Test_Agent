"""Redis task handler for durable, versioned memory-vector rebuilds."""

from __future__ import annotations

import logging
from typing import Any

from src.application.context.memory_vector_rebuild_service import (
    MemoryVectorRebuildService,
)
from src.core.request_context import get_request_context
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.postgres_vector_rebuild_job_store import (
    TERMINAL_STATUSES,
    PostgresVectorRebuildJobStore,
)
from src.runtime.task_deferred import TaskDeferred


LOGGER = logging.getLogger(__name__)


class VectorRebuildTaskHandler:
    def __init__(
        self,
        service: MemoryVectorRebuildService,
        job_store: PostgresVectorRebuildJobStore,
        *,
        worker_id: str,
    ) -> None:
        self._service = service
        self._jobs = job_store
        self._worker_id = worker_id

    async def __call__(self, task: QueuedTask) -> None:
        payload = task.payload
        task_id = str(payload.get("task_id") or "").strip()
        task_type = str(payload.get("task_type") or "").strip()
        embedding_version = str(payload.get("embedding_version") or "").strip()
        entity = str(payload.get("entity") or "memory").strip()
        if not task_id:
            raise ValueError(f"Vector rebuild message {task.message_id} is missing task_id")
        if task_type != "vector_rebuild_task":
            raise ValueError(f"Task {task_id} is not a vector_rebuild_task")
        if entity != "memory":
            raise ValueError(f"Vector rebuild task {task_id} has unsupported entity")
        if not embedding_version:
            raise ValueError(f"Vector rebuild task {task_id} is missing embedding_version")
        context = get_request_context()
        worker_id = str(
            (context.worker_id if context is not None else None) or self._worker_id
        )
        claimed = await self._jobs.begin(task_id, worker_id=worker_id)
        if not claimed:
            current = await self._jobs.get(task_id)
            if current is None:
                raise ValueError(f"Vector rebuild task {task_id} has no durable job record")
            if current.status in TERMINAL_STATUSES:
                return
            raise TaskDeferred(f"Vector rebuild task {task_id} is owned by another worker")
        LOGGER.info(
            "vector_rebuild_task_claimed",
            extra={
                "task_id": task_id,
                "worker_id": worker_id,
                "embedding_version": embedding_version,
                "activate": bool(payload.get("activate", False)),
            },
        )

        async def progress(status: str, details: dict[str, Any]) -> None:
            updated = await self._jobs.progress(
                task_id,
                worker_id=worker_id,
                status=status,
                dimension=_optional_int(details.get("dimension")),
                source_count=int(details.get("source_count") or 0),
                replicated=int(details.get("replicated") or 0),
            )
            if not updated:
                raise RuntimeError(
                    f"Vector rebuild task {task_id} lost its PostgreSQL lease"
                )
            LOGGER.info(
                "vector_rebuild_task_progressed",
                extra={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "embedding_version": embedding_version,
                    "status": status,
                    "dimension": details.get("dimension"),
                    "source_count": details.get("source_count"),
                    "replicated": details.get("replicated"),
                },
            )

        try:
            result = await self._service.rebuild(
                embedding_version,
                execute=True,
                activate=bool(payload.get("activate", False)),
                progress=progress,
            )
            terminal_status = {
                "activated": "index_activated",
                "validated": "index_validated",
                "empty": "empty",
            }.get(result.status)
            if terminal_status is None:
                raise RuntimeError(
                    f"Vector rebuild task {task_id} returned non-terminal status"
                )
            completed = await self._jobs.complete(
                task_id,
                worker_id=worker_id,
                status=terminal_status,
                dimension=result.dimension,
                source_count=result.source_count,
                replicated=result.replicated,
                active_index=result.active_index,
            )
            if not completed:
                raise RuntimeError(
                    f"Vector rebuild task {task_id} lost completion ownership"
                )
            LOGGER.info(
                "vector_rebuild_task_completed",
                extra={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "embedding_version": embedding_version,
                    "status": terminal_status,
                    "active_index": result.active_index,
                },
            )
        except Exception as exc:
            try:
                await self._jobs.fail(
                    task_id,
                    worker_id=worker_id,
                    error_type=type(exc).__name__,
                )
            except Exception:
                LOGGER.exception(
                    "vector_rebuild_task_failure_record_failed",
                    extra={
                        "task_id": task_id,
                        "worker_id": worker_id,
                        "embedding_version": embedding_version,
                    },
                )
            LOGGER.error(
                "vector_rebuild_task_failed",
                extra={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "embedding_version": embedding_version,
                    "error_type": type(exc).__name__,
                },
            )
            raise


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None

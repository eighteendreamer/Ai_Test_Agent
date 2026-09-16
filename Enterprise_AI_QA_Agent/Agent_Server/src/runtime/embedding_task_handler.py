"""Redis task handler for durable test-case Embedding jobs."""

from __future__ import annotations

import logging
from typing import Any

from src.application.test_cases.embedding_service import TestCaseEmbeddingService
from src.core.request_context import get_request_context
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.postgres_embedding_job_store import (
    TERMINAL_STATUSES,
    PostgresEmbeddingJobStore,
)
from src.runtime.task_deferred import TaskDeferred
from src.schemas.embedding_task import TestCaseEmbeddingTask


LOGGER = logging.getLogger(__name__)


class EmbeddingTaskHandler:
    def __init__(
        self,
        service: TestCaseEmbeddingService,
        job_store: PostgresEmbeddingJobStore,
        *,
        worker_id: str,
    ) -> None:
        self._service = service
        self._jobs = job_store
        self._worker_id = worker_id

    async def __call__(self, task: QueuedTask) -> None:
        command = TestCaseEmbeddingTask.model_validate(task.payload)
        context = get_request_context()
        worker_id = str(
            (context.worker_id if context is not None else None) or self._worker_id
        )
        claimed = await self._jobs.begin(command.task_id, worker_id=worker_id)
        if not claimed:
            current = await self._jobs.get(command.task_id)
            if current is None:
                raise ValueError(
                    f"Embedding task {command.task_id} has no durable job record"
                )
            if current.status in TERMINAL_STATUSES:
                return
            raise TaskDeferred(
                f"Embedding task {command.task_id} is owned by another worker"
            )
        LOGGER.info(
            "embedding_task_claimed",
            extra={
                "task_id": command.task_id,
                "worker_id": worker_id,
                "source_type": command.source_type,
                "source_id": command.source_id,
                "project_id": command.project_id,
            },
        )

        async def progress(status: str, details: dict[str, Any]) -> None:
            updated = await self._jobs.progress(
                command.task_id,
                worker_id=worker_id,
                status=status,
                embedding_version=_optional_text(details.get("embedding_version")),
                embedding_dimension=_optional_int(details.get("dimension")),
            )
            if not updated:
                raise RuntimeError(
                    f"Embedding task {command.task_id} lost its PostgreSQL lease"
                )
            LOGGER.info(
                "embedding_task_progressed",
                extra={
                    "task_id": command.task_id,
                    "worker_id": worker_id,
                    "status": status,
                    "embedding_version": details.get("embedding_version"),
                    "embedding_dimension": details.get("dimension"),
                },
            )

        try:
            outcome = await self._service.index_active_version(
                case_id=command.case_id,
                case_version_id=command.case_version_id,
                expected_content_hash=command.content_hash,
                progress=progress,
            )
            completed = await self._jobs.complete(
                command.task_id,
                worker_id=worker_id,
                status=outcome.status,
                embedding_version=outcome.embedding_version,
                embedding_dimension=outcome.embedding_dimension,
                redis_key=outcome.redis_key,
            )
            if not completed:
                raise RuntimeError(
                    f"Embedding task {command.task_id} lost completion ownership"
                )
            LOGGER.info(
                "embedding_task_completed",
                extra={
                    "task_id": command.task_id,
                    "worker_id": worker_id,
                    "status": outcome.status,
                    "embedding_version": outcome.embedding_version,
                    "embedding_dimension": outcome.embedding_dimension,
                },
            )
        except Exception as exc:
            try:
                await self._jobs.fail(
                    command.task_id,
                    worker_id=worker_id,
                    error_type=type(exc).__name__,
                )
            except Exception:
                LOGGER.exception(
                    "embedding_task_failure_record_failed",
                    extra={
                        "task_id": command.task_id,
                        "worker_id": worker_id,
                    },
                )
            LOGGER.error(
                "embedding_task_failed",
                extra={
                    "task_id": command.task_id,
                    "worker_id": worker_id,
                    "source_type": command.source_type,
                    "source_id": command.source_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None

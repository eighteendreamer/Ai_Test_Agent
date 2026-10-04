"""Shared resources for independent document Workers; domain setup stays separate."""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from src.containers import AppContainer
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.sqlalchemy_runtime import sqlalchemy_engine_lifespan
from src.runtime.postgres_task_outbox import PostgresTaskOutbox

logger = logging.getLogger(__name__)


@asynccontextmanager
async def document_operation_worker_runtime(settings, *, stream, task_type, log_prefix, label, build_service):
    if stream not in settings.orchestration.task_stream_names:
        logger.error(f"{log_prefix}_stream_unregistered", extra={"queue": stream})
        raise ValueError(f"Register the {label} stream in REDIS_TASK_OUTBOX_STREAMS")
    container = AppContainer()
    container.settings.override(settings)

    async def initialize(resource_id, operation):
        logger.info(f"{log_prefix}_resource_initializing", extra={"resource_id": resource_id, "queue": stream})
        try:
            await operation
        except asyncio.CancelledError:
            logger.warning(f"{log_prefix}_startup_interrupted", extra={"resource_id": resource_id, "queue": stream})
            raise
        except Exception as exc:
            logger.error(f"{log_prefix}_resource_failed", extra={"resource_id": resource_id,
                "queue": stream, "error_type": type(exc).__name__})
            raise

    async with AsyncExitStack() as cleanup:
        await cleanup.enter_async_context(sqlalchemy_engine_lifespan(settings))
        objects = container.artifact_storage_service()
        cleanup.push_async_callback(objects.close)
        await initialize("projects", container.project_service().initialize())
        await initialize("application_config", asyncio.to_thread(container.application_config_store().initialize))
        await initialize("documents", container.api_docs_service().initialize())
        await initialize("outbox", PostgresTaskOutbox(settings).initialize())
        service, store = await build_service(container, initialize)
        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:{task_type}",
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            retention_seconds=settings.orchestration.redis_task_retention_seconds,
            retention_interval_seconds=settings.orchestration.redis_task_retention_interval_seconds,
            retention_batch_size=settings.orchestration.redis_task_retention_batch_size)
        cleanup.push_async_callback(queue.close)
        await initialize("task_queue", queue.connect())
        logger.info(f"{log_prefix}_runtime_started", extra={"queue": stream})
        try:
            yield service, store, queue
        finally:
            logger.info(f"{log_prefix}_runtime_stopping", extra={"queue": stream})

"""Minimal document Worker resources; no model, browser or tool execution boot."""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from src.application.documents.parsing_service import DocumentParsingService, DOCUMENT_PARSE_STREAM
from src.containers import AppContainer
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.sqlalchemy_runtime import sqlalchemy_engine_lifespan
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentParseWorkerRuntime:
    service: DocumentParsingService
    store: PostgresDocumentParseStore
    queue: RedisTaskQueue


@asynccontextmanager
async def document_parse_worker_runtime(settings, *, stream=DOCUMENT_PARSE_STREAM):
    if stream not in settings.orchestration.task_stream_names:
        logger.error("document_parse_worker_stream_unregistered", extra={"queue": stream})
        raise ValueError("Register the document parsing stream in REDIS_TASK_OUTBOX_STREAMS")
    container = AppContainer()
    container.settings.override(settings)
    async def initialize(resource_id, operation):
        logger.info("document_parse_worker_resource_initializing", extra={"resource_id": resource_id, "queue": stream})
        try:
            await operation
        except asyncio.CancelledError:
            logger.warning("document_parse_worker_startup_interrupted", extra={"resource_id": resource_id, "queue": stream})
            raise
        except Exception as exc:
            logger.error("document_parse_worker_resource_failed", extra={"resource_id": resource_id,
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
        store = PostgresDocumentParseStore(settings, lease_seconds=settings.orchestration.task_outbox_lease_seconds)
        service = DocumentParsingService(store=store, documents=container.api_docs_service(),
            projects=container.project_service(), configs=container.document_parser_config_service(),
            objects=objects, stream=stream)
        await initialize("document_parsing", service.initialize())
        container.api_docs_service().set_parsing_service(service)
        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:document_parsing",
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            retention_seconds=settings.orchestration.redis_task_retention_seconds,
            retention_interval_seconds=settings.orchestration.redis_task_retention_interval_seconds,
            retention_batch_size=settings.orchestration.redis_task_retention_batch_size)
        cleanup.push_async_callback(queue.close)
        await initialize("task_queue", queue.connect())
        logger.info("document_parse_worker_runtime_started", extra={"queue": stream})
        try:
            yield DocumentParseWorkerRuntime(service, store, queue)
        finally:
            logger.info("document_parse_worker_runtime_stopping", extra={"queue": stream})

"""Minimal document Worker resources; no model, browser or tool execution boot."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from src.application.documents.parsing_service import DocumentParsingService, DOCUMENT_PARSE_STREAM
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_operation_worker_runtime import document_operation_worker_runtime
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore


@dataclass(frozen=True)
class DocumentParseWorkerRuntime:
    service: DocumentParsingService
    store: PostgresDocumentParseStore
    queue: RedisTaskQueue


@asynccontextmanager
async def document_parse_worker_runtime(settings, *, stream=DOCUMENT_PARSE_STREAM):
    async def build_service(container, initialize):
        store = PostgresDocumentParseStore(settings, lease_seconds=settings.orchestration.task_outbox_lease_seconds)
        service = DocumentParsingService(store=store, documents=container.api_docs_service(),
            projects=container.project_service(), configs=container.document_parser_config_service(),
            objects=container.artifact_storage_service(), stream=stream)
        await initialize("document_parsing", service.initialize())
        container.api_docs_service().set_parsing_service(service)
        return service, store

    async with document_operation_worker_runtime(settings, stream=stream, task_type="document_parsing",
        log_prefix="document_parse_worker", label="document parsing", build_service=build_service) as runtime:
        yield DocumentParseWorkerRuntime(*runtime)

"""Independent comparison Worker with project-bound, read-only parsing sources."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from src.application.documents.difference_service import DocumentDifferenceService, DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DocumentParsingService
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_operation_worker_runtime import document_operation_worker_runtime
from src.runtime.postgres_document_difference_store import PostgresDocumentDifferenceStore
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore


@dataclass(frozen=True)
class DocumentDifferenceWorkerRuntime:
    service: DocumentDifferenceService
    store: PostgresDocumentDifferenceStore
    queue: RedisTaskQueue


@asynccontextmanager
async def document_difference_worker_runtime(settings, *, stream=DOCUMENT_DIFFERENCE_STREAM):
    async def build_service(container, initialize):
        parsing = DocumentParsingService(
            store=PostgresDocumentParseStore(settings, lease_seconds=settings.orchestration.task_outbox_lease_seconds),
            documents=container.api_docs_service(), projects=container.project_service(),
            configs=container.document_parser_config_service(), objects=container.artifact_storage_service(), stream=None)
        await initialize("document_parsing_sources", parsing.initialize())
        container.api_docs_service().set_parsing_service(parsing)
        store = PostgresDocumentDifferenceStore(settings, lease_seconds=settings.orchestration.task_outbox_lease_seconds)
        service = DocumentDifferenceService(store=store, documents=container.api_docs_service(),
            projects=container.project_service(), configs=container.document_difference_config_service(),
            objects=container.artifact_storage_service(), parsing=parsing, stream=stream)
        await initialize("document_difference", service.initialize())
        return service, store

    async with document_operation_worker_runtime(settings, stream=stream, task_type="document_difference",
        log_prefix="document_difference_worker", label="document difference", build_service=build_service) as runtime:
        yield DocumentDifferenceWorkerRuntime(*runtime)

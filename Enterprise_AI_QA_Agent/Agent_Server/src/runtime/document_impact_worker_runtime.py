"""Impact Worker owns only fixed document/case reads, model calls and task resources."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from src.application.documents.impact_workflow import DocumentImpactWorkflow
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_difference_worker_runtime import build_document_difference_sources
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM, build_document_impact_runtime
from src.runtime.document_operation_worker_runtime import document_operation_worker_runtime
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore


@dataclass(frozen=True)
class DocumentImpactWorkerRuntime:
    service: DocumentImpactWorkflow
    store: PostgresDocumentImpactStore
    queue: RedisTaskQueue


@asynccontextmanager
async def document_impact_worker_runtime(settings, *, stream=DOCUMENT_IMPACT_STREAM):
    async def build_service(container, initialize):
        await initialize("model_config", asyncio.to_thread(container.model_config_store().initialize))
        await initialize("cases", container.test_case_service().initialize())
        parsing, differences, _ = await build_document_difference_sources(settings, container, initialize)
        await initialize("impact_config", container.document_impact_config_service().initialize())
        runtime = build_document_impact_runtime(settings, container,
            parsing=parsing, differences=differences, stream=None)
        await initialize("document_impact", runtime.tasks.initialize())
        return runtime.workflow, runtime.store

    async with document_operation_worker_runtime(settings, stream=stream, task_type="document_impact",
        log_prefix="document_impact_worker", label="document impact", build_service=build_service) as runtime:
        yield DocumentImpactWorkerRuntime(*runtime)

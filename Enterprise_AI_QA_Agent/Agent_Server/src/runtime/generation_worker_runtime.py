"""Independent module Worker reuses the existing bounded resource lifecycle."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from src.application.test_cases.generation_task_service import GenerationTaskService
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_difference_worker_runtime import build_document_difference_sources
from src.runtime.document_impact_runtime import build_document_impact_runtime
from src.runtime.document_operation_worker_runtime import document_operation_worker_runtime
from src.runtime.generation_runtime import GENERATION_STREAM, build_generation_runtime
from src.runtime.postgres_generation_task_store import PostgresGenerationTaskStore


@dataclass(frozen=True)
class GenerationWorkerRuntime:
    service: GenerationTaskService
    store: PostgresGenerationTaskStore
    queue: RedisTaskQueue


@asynccontextmanager
async def generation_worker_runtime(settings, *, stream=GENERATION_STREAM):
    async def build_service(container, initialize):
        await initialize("model_config", asyncio.to_thread(container.model_config_store().initialize))
        await initialize("cases", container.test_case_service().initialize())
        await initialize("templates", container.test_case_template_service().initialize())
        parsing, differences, _ = await build_document_difference_sources(settings, container, initialize)
        await initialize("impact_config", container.document_impact_config_service().initialize())
        impact = build_document_impact_runtime(settings, container, parsing=parsing, differences=differences, stream=None)
        await initialize("impact_sources", impact.tasks.initialize())
        await initialize("requirements", impact.requirements.initialize())
        runtime = build_generation_runtime(settings, container, requirements=impact.requirements,
            impact_tasks=impact.tasks, stream=None, parsing=parsing)
        await initialize("generation", runtime.service.initialize())
        return runtime.service, runtime.store

    async with document_operation_worker_runtime(settings, stream=stream, task_type="case_generation",
        log_prefix="case_generation_worker", label="case generation", build_service=build_service) as runtime:
        yield GenerationWorkerRuntime(*runtime)

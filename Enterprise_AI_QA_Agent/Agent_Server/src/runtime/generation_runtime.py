"""One fixed generation dependency assembly for API and independent Workers."""
from dataclasses import dataclass

from src.application.test_cases.generation_task_config import GenerationTaskConfig
from src.application.test_cases.generation_task_service import GenerationTaskService
from src.application.test_cases.incremental_generation_sources import IncrementalCaseGenerationSources
from src.application.test_cases.document_generation_sources import DocumentCaseGenerationSources
from src.runtime.postgres_generation_task_store import PostgresGenerationTaskStore

GENERATION_STREAM = "qa:tasks:case_generation"


@dataclass(frozen=True)
class GenerationRuntime:
    service: GenerationTaskService
    store: PostgresGenerationTaskStore
    config: GenerationTaskConfig


def build_generation_runtime(settings, container, *, requirements, impact_tasks, stream, parsing=None):
    projects, cases = container.project_service(), container.test_case_service()
    config = GenerationTaskConfig(configs=container.application_config_store(), projects=projects)
    generator = container.test_case_generator()
    sources = IncrementalCaseGenerationSources(requirements=requirements, impact_tasks=impact_tasks,
        projects=projects, cases=cases, templates=container.test_case_template_service(), generator=generator)
    store = PostgresGenerationTaskStore(settings, container.test_case_store(),
        lease_seconds=settings.orchestration.task_outbox_lease_seconds)
    service = GenerationTaskService(store=store, sources=sources, config=config, generator=generator,
        cases=cases, projects=projects, documents=container.api_docs_service(),
        objects=container.artifact_storage_service(), stream=stream,
        document_sources=DocumentCaseGenerationSources(parsing=parsing, projects=projects,
            templates=container.test_case_template_service(), generator=generator) if parsing else None)
    return GenerationRuntime(service=service, store=store, config=config)

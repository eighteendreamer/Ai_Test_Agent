"""One impact dependency assembly for API and independent Worker processes."""
from __future__ import annotations

from dataclasses import dataclass

from src.application.documents.impact_analysis_context import DocumentImpactAnalysisContexts
from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_response_service import DocumentImpactResponseService
from src.application.documents.impact_sources import DocumentImpactSources
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.application.documents.impact_workflow import DocumentImpactWorkflow
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore

DOCUMENT_IMPACT_STREAM = "qa:tasks:document_impact"


@dataclass(frozen=True)
class DocumentImpactRuntime:
    tasks: DocumentImpactTaskService
    workflow: DocumentImpactWorkflow
    store: PostgresDocumentImpactStore


def build_document_impact_runtime(settings, container, *, parsing, differences, stream):
    projects, objects = container.project_service(), container.artifact_storage_service()
    documents, configs = container.api_docs_service(), container.document_impact_config_service()
    sources = DocumentImpactSources(documents=documents, parsing=parsing, differences=differences,
        cases=container.test_case_service(), configs=configs)
    contexts = DocumentImpactAnalysisContexts(models=container.model_runtime_service(),
        skills=container.skill_runtime_service(), configs=configs)
    store = PostgresDocumentImpactStore(settings, lease_seconds=settings.orchestration.task_outbox_lease_seconds)
    tasks = DocumentImpactTaskService(store=store, sources=sources, documents=documents,
        projects=projects, objects=objects, stream=stream, analysis_contexts=contexts)
    calls = DocumentImpactCallService(tasks=tasks, store=store, models=container.model_runtime_service(),
        objects=objects, projects=projects)
    responses = DocumentImpactResponseService(calls=calls, store=store, objects=objects)
    workflow = DocumentImpactWorkflow(tasks=tasks, store=store, calls=calls, responses=responses, objects=objects)
    return DocumentImpactRuntime(tasks=tasks, workflow=workflow, store=store)

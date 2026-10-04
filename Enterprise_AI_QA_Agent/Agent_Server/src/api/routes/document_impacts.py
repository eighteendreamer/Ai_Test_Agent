from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.api.document_operation_calls import call_document_operation
from src.schemas.document_impact import (
    DocumentImpactPolicy, DocumentImpactSettingsRecord, DocumentImpactSnapshotPage,
    DocumentImpactTaskPage, DocumentImpactTaskRecord, DocumentImpactTaskRequest,
)
from src.schemas.document_impact_analysis import DocumentImpactAnalysisPolicy, DocumentImpactAnalysisSettings
from src.schemas.document_impact_batches import DocumentImpactBatchPolicy, DocumentImpactBatchSettings
from src.schemas.document_impact_calls import DocumentImpactInvocationPolicy, DocumentImpactInvocationSettings
from src.schemas.document_impact_views import DocumentImpactSnapshotView

router = APIRouter(prefix="/projects/{project_id}", tags=["document-impacts"])


async def _call(request, project_id, service_name, operation, *args, **kwargs):
    return await call_document_operation(request, project_id, service_name, operation, *args,
        log_prefix="document_impact", label="影响分析", **kwargs)


@router.get("/document-impact-settings", response_model=DocumentImpactSettingsRecord)
async def get_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "get_settings")


@router.put("/document-impact-settings", response_model=DocumentImpactSettingsRecord)
async def save_settings(project_id: UUID, payload: DocumentImpactPolicy, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "save_settings", payload)


@router.get("/document-impact-analysis-settings", response_model=DocumentImpactAnalysisSettings)
async def get_analysis_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "get_analysis_settings")


@router.put("/document-impact-analysis-settings", response_model=DocumentImpactAnalysisSettings)
async def save_analysis_settings(project_id: UUID, payload: DocumentImpactAnalysisPolicy, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "save_analysis_settings", payload)


@router.get("/document-impact-batch-settings", response_model=DocumentImpactBatchSettings)
async def get_batch_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "get_batch_settings")


@router.put("/document-impact-batch-settings", response_model=DocumentImpactBatchSettings)
async def save_batch_settings(project_id: UUID, payload: DocumentImpactBatchPolicy, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "save_batch_settings", payload)


@router.get("/document-impact-invocation-settings", response_model=DocumentImpactInvocationSettings)
async def get_invocation_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "get_invocation_settings")


@router.put("/document-impact-invocation-settings", response_model=DocumentImpactInvocationSettings)
async def save_invocation_settings(project_id: UUID, payload: DocumentImpactInvocationPolicy, request: Request):
    return await _call(request, project_id, "document_impact_config_service", "save_invocation_settings", payload)


@router.post("/documents/{document_id}/impact-tasks", response_model=DocumentImpactTaskRecord, status_code=202)
async def submit(project_id: UUID, document_id: UUID, payload: DocumentImpactTaskRequest, request: Request):
    return await _call(request, project_id, "document_impact_task_service", "submit", str(document_id), payload)


@router.get("/documents/{document_id}/impact-tasks/by-request", response_model=DocumentImpactTaskRecord)
async def get_by_request(project_id: UUID, document_id: UUID, request: Request,
    creation_key: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, "document_impact_task_service", "get_by_request", str(document_id), creation_key)


@router.get("/documents/{document_id}/impact-tasks", response_model=DocumentImpactTaskPage)
async def list_tasks(project_id: UUID, document_id: UUID, request: Request,
    version_id: UUID | None = None, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "document_impact_task_service", "list_tasks", str(document_id),
        version_id=str(version_id) if version_id else None, limit=limit, offset=offset)


@router.get("/documents/{document_id}/impact-tasks/{task_id}", response_model=DocumentImpactTaskRecord)
async def get_task(project_id: UUID, document_id: UUID, task_id: UUID, request: Request):
    return await _call(request, project_id, "document_impact_task_service", "get", str(document_id), str(task_id))


@router.get("/documents/{document_id}/impact-tasks/{task_id}/result", response_model=DocumentImpactSnapshotView)
async def read_result(project_id: UUID, document_id: UUID, task_id: UUID, request: Request):
    snapshot, result = await _call(request, project_id, "document_impact_workflow", "read_result", str(document_id), str(task_id))
    return {"snapshot": snapshot, "result": result}


@router.get("/documents/{document_id}/impact-snapshots", response_model=DocumentImpactSnapshotPage)
async def list_snapshots(project_id: UUID, document_id: UUID, request: Request,
    version_id: UUID | None = None, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "document_impact_workflow", "list_snapshots", str(document_id),
        version_id=str(version_id) if version_id else None, limit=limit, offset=offset)


@router.get("/documents/{document_id}/impact-snapshots/{snapshot_id}", response_model=DocumentImpactSnapshotView)
async def read_snapshot(project_id: UUID, document_id: UUID, snapshot_id: UUID, request: Request):
    snapshot, result = await _call(request, project_id, "document_impact_workflow", "read_snapshot", str(document_id), str(snapshot_id))
    return {"snapshot": snapshot, "result": result}

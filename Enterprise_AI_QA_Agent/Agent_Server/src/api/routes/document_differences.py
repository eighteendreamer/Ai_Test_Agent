from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request
from src.api.document_operation_calls import call_document_operation
from src.schemas.document_differences import (
    DocumentDifferenceFailureView, DocumentDifferenceSettings, DocumentDifferenceSettingsRecord,
    DocumentDifferenceSnapshotPage, DocumentDifferenceSnapshotView, DocumentDifferenceTaskPage,
    DocumentDifferenceTaskRecord, DocumentDifferenceTaskRequest,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["document-differences"])


async def _call(request, project_id, service_name, operation, *args, **kwargs):
    return await call_document_operation(request, project_id, service_name, operation, *args,
        log_prefix="document_difference", label="比较", **kwargs)


@router.get("/document-difference-settings", response_model=DocumentDifferenceSettingsRecord)
async def get_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "document_difference_config_service", "get_settings")


@router.put("/document-difference-settings", response_model=DocumentDifferenceSettingsRecord)
async def save_settings(project_id: UUID, payload: DocumentDifferenceSettings, request: Request):
    return await _call(request, project_id, "document_difference_config_service", "save_settings", payload)


@router.post("/documents/{document_id}/difference-tasks", response_model=DocumentDifferenceTaskRecord, status_code=202)
async def submit(project_id: UUID, document_id: UUID, payload: DocumentDifferenceTaskRequest, request: Request):
    return await _call(request, project_id, "document_difference_service", "submit", str(document_id), payload)


@router.get("/documents/{document_id}/difference-tasks/by-request", response_model=DocumentDifferenceTaskRecord)
async def get_by_request(project_id: UUID, document_id: UUID, request: Request,
    creation_key: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, "document_difference_service", "get_by_request", str(document_id), creation_key)


@router.get("/documents/{document_id}/difference-tasks", response_model=DocumentDifferenceTaskPage)
async def list_tasks(project_id: UUID, document_id: UUID, request: Request,
    version_id: UUID | None = None, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "document_difference_service", "list_tasks", str(document_id),
        version_id=str(version_id) if version_id else None, limit=limit, offset=offset)


@router.get("/documents/{document_id}/difference-snapshots", response_model=DocumentDifferenceSnapshotPage)
async def list_snapshots(project_id: UUID, document_id: UUID, request: Request,
    version_id: UUID | None = None, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "document_difference_service", "list_snapshots", str(document_id),
        version_id=str(version_id) if version_id else None, limit=limit, offset=offset)


@router.get("/documents/{document_id}/difference-tasks/{task_id}", response_model=DocumentDifferenceTaskRecord)
async def get_task(project_id: UUID, document_id: UUID, task_id: UUID, request: Request):
    return await _call(request, project_id, "document_difference_service", "get", str(document_id), str(task_id))


@router.get("/documents/{document_id}/difference-tasks/{task_id}/failure", response_model=DocumentDifferenceFailureView)
async def read_failure(project_id: UUID, document_id: UUID, task_id: UUID, request: Request):
    return await _call(request, project_id, "document_difference_service", "read_failure", str(document_id), str(task_id))


@router.get("/documents/{document_id}/difference-snapshots/{snapshot_id}", response_model=DocumentDifferenceSnapshotView)
async def read_snapshot(project_id: UUID, document_id: UUID, snapshot_id: UUID, request: Request):
    snapshot, result = await _call(request, project_id, "document_difference_service", "read_snapshot", str(document_id), str(snapshot_id))
    return {"snapshot": snapshot.model_dump(mode="json"), "result": result.model_dump(mode="json")}

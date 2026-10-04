from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.api.document_operation_calls import call_document_operation
from src.schemas.api_docs import ApiDocVersionResult
from src.schemas.document_git import (
    GitDocumentImportRequest, GitDocumentResolution, GitDocumentResolveRequest,
    GitDocumentSettings, GitDocumentSettingsRecord,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["document-git"])


async def _call(request, project_id, operation, *args, **kwargs):
    return await call_document_operation(request, project_id, "document_git_service", operation,
        *args, log_prefix="document_git", label="Git 来源", **kwargs)


@router.get("/document-git-settings", response_model=GitDocumentSettingsRecord)
async def get_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "get_settings")


@router.put("/document-git-settings", response_model=GitDocumentSettingsRecord)
async def save_settings(project_id: UUID, payload: GitDocumentSettings, request: Request):
    return await _call(request, project_id, "save_settings", payload)


@router.post("/document-git/resolve", response_model=GitDocumentResolution)
async def resolve(project_id: UUID, payload: GitDocumentResolveRequest, request: Request):
    return await _call(request, project_id, "resolve", payload)


@router.post("/document-git/import", response_model=ApiDocVersionResult)
async def import_document(project_id: UUID, payload: GitDocumentImportRequest, request: Request):
    return await _call(request, project_id, "import_document", payload)


@router.get("/document-git/submissions", response_model=ApiDocVersionResult)
async def get_submission(project_id: UUID, request: Request,
    request_id: str = Query(min_length=1, max_length=120), document_id: UUID | None = None):
    return await _call(request, project_id, "get_submission", request_id,
        document_id=str(document_id) if document_id else None)

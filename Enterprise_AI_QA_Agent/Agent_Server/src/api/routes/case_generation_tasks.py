from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.api.document_operation_calls import call_document_operation
from src.schemas.case_generation import IncrementalGenerationScope
from src.schemas.case_generation_tasks import (
    GenerationGroup, GenerationGroupPage, GenerationGroupView, GenerationTaskPolicy,
    GenerationTaskRequest, GenerationTaskSettings, GenerationTaskView,
)
from src.schemas.model_config import ModelInvocationResult

router = APIRouter(prefix="/projects/{project_id}", tags=["case-generation"])


async def _call(request, project_id, operation, *args, service="generation_task_service", **kwargs):
    return await call_document_operation(request, project_id, service, operation, *args,
        log_prefix="case_generation", label="增量生成", **kwargs)


@router.get("/case-generation-settings", response_model=GenerationTaskSettings)
async def get_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "get", service="generation_task_config")


@router.put("/case-generation-settings", response_model=GenerationTaskSettings)
async def save_settings(project_id: UUID, payload: GenerationTaskPolicy, request: Request):
    return await _call(request, project_id, "save", payload, service="generation_task_config")


@router.post("/documents/{document_id}/case-generations", response_model=GenerationGroup, status_code=202)
async def submit(project_id: UUID, document_id: UUID, payload: GenerationTaskRequest, request: Request):
    return await _call(request, project_id, "submit", str(document_id), payload)


@router.get("/documents/{document_id}/case-generations/by-request", response_model=GenerationGroup)
async def get_by_request(project_id: UUID, document_id: UUID, request: Request,
    creation_key: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, "get_by_request", str(document_id), creation_key)


@router.get("/documents/{document_id}/case-generations", response_model=GenerationGroupPage)
async def list_groups(project_id: UUID, document_id: UUID, request: Request,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "list_groups", str(document_id), limit=limit, offset=offset)


@router.get("/documents/{document_id}/case-generations/{group_id}", response_model=GenerationGroupView)
async def read_group(project_id: UUID, document_id: UUID, group_id: UUID, request: Request):
    group, modules = await _call(request, project_id, "read_group", str(document_id), str(group_id))
    return {"group": group, "modules": modules}


@router.get("/documents/{document_id}/case-generations/{group_id}/scope", response_model=IncrementalGenerationScope)
async def read_scope(project_id: UUID, document_id: UUID, group_id: UUID, request: Request):
    return await _call(request, project_id, "read_scope_by_group", str(document_id), str(group_id))


@router.get("/documents/{document_id}/case-generation-tasks/{task_id}", response_model=GenerationTaskView)
async def get_task(project_id: UUID, document_id: UUID, task_id: UUID, request: Request):
    return await _call(request, project_id, "get_task", str(document_id), str(task_id))


@router.get("/documents/{document_id}/case-generation-tasks/{task_id}/response", response_model=ModelInvocationResult)
async def read_response(project_id: UUID, document_id: UUID, task_id: UUID, request: Request):
    return await _call(request, project_id, "read_response", str(document_id), str(task_id))

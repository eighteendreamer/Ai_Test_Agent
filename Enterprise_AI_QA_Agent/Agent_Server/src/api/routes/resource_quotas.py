from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from src.schemas.resource_quota import ResourceQuotaRecord, ResourceQuotaUpsertRequest

router = APIRouter(prefix="/projects", tags=["resource-quotas"])


@router.get("/{project_id}/resource-quotas", response_model=list[ResourceQuotaRecord])
async def list_resource_quotas(project_id: str, request: Request):
    try:
        await request.app.state.project_service.get(project_id)
        return await request.app.state.resource_quota_service.list_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{project_id}/resource-quotas", response_model=ResourceQuotaRecord)
async def upsert_resource_quota(project_id: str, payload: ResourceQuotaUpsertRequest, request: Request):
    try:
        await request.app.state.project_service.get(project_id)
        return await request.app.state.resource_quota_service.upsert(
            project_id, payload.resource_type, payload.limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

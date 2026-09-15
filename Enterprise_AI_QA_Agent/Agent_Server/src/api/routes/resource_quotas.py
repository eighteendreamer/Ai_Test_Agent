from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from src.schemas.resource_quota import ResourceQuotaRecord, ResourceQuotaUpsertRequest

router = APIRouter(prefix="/projects", tags=["resource-quotas"])
run_router = APIRouter(prefix="/runs", tags=["resource-quotas"])


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


@run_router.get("/{run_id}/resource-quotas", response_model=list[ResourceQuotaRecord])
async def list_run_resource_quotas(run_id: str, request: Request):
    try:
        await request.app.state.test_run_service.get(run_id)
        return await request.app.state.resource_quota_service.list_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@run_router.put("/{run_id}/resource-quotas", response_model=ResourceQuotaRecord)
async def upsert_run_resource_quota(run_id: str, payload: ResourceQuotaUpsertRequest, request: Request):
    try:
        await request.app.state.test_run_service.get(run_id)
        return await request.app.state.resource_quota_service.upsert_scope(
            "run", run_id, payload.resource_type, payload.limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

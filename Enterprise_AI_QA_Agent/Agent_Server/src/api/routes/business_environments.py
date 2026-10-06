"""Project targets, current settings and fixed capture recovery."""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from src.schemas.business_environment import (
    BusinessEnvironmentCreateRequest, BusinessEnvironmentRecord, BusinessEnvironmentPage,
    BusinessTargetConfiguration, BusinessTargetConfigurationRecord,
    BusinessEnvironmentCaptureRequest, BusinessEnvironmentSnapshot, BusinessEnvironmentSnapshotPage,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects/{project_id}/business-environments", tags=["business-environments"])


async def _call(request, project_id, operation, *args, **kwargs):
    try:
        return await getattr(request.app.state.business_environment_service, operation)(str(project_id), *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning("business_environment_request_rejected", extra={"project_id": str(project_id), "operation": operation})
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.error("business_environment_service_unavailable", extra={"project_id": str(project_id),
            "operation": operation, "error_type": type(exc).__name__})
        recovery = ("配置保存结果未知时，请GET核对当前配置；并发更新后不能恢复旧配置回执" if
            operation == "save_configuration" else "提交结果未知时，请按原请求编号查询，不更换参数盲目重试")
        raise HTTPException(status_code=503, detail=f"项目环境服务暂时不可用；{recovery}") from None


@router.post("", response_model=BusinessEnvironmentRecord, status_code=201)
async def register(project_id: UUID, payload: BusinessEnvironmentCreateRequest, request: Request):
    return await _call(request, project_id, "register", payload)


@router.get("", response_model=BusinessEnvironmentPage)
async def list_environments(project_id: UUID, request: Request,
    status: str | None = Query(default=None, pattern=r"^(active|archived)$"),
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "list", status=status, limit=limit, offset=offset)


@router.get("/by-request", response_model=BusinessEnvironmentRecord)
async def get_registration(project_id: UUID, request: Request, request_id: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, "get_registration", request_id)


@router.get("/{environment_id}", response_model=BusinessEnvironmentRecord)
async def get_environment(project_id: UUID, environment_id: UUID, request: Request):
    return await _call(request, project_id, "get", str(environment_id))


@router.post("/{environment_id}/archive", response_model=BusinessEnvironmentRecord)
async def archive(project_id: UUID, environment_id: UUID, request: Request):
    return await _call(request, project_id, "archive", str(environment_id))


@router.get("/{environment_id}/configuration", response_model=BusinessTargetConfigurationRecord)
async def read_configuration(project_id: UUID, environment_id: UUID, request: Request):
    return await _call(request, project_id, "read_configuration", str(environment_id))


@router.put("/{environment_id}/configuration", response_model=BusinessTargetConfigurationRecord)
async def save_configuration(project_id: UUID, environment_id: UUID, payload: BusinessTargetConfiguration, request: Request):
    return await _call(request, project_id, "save_configuration", str(environment_id), payload)


@router.post("/{environment_id}/snapshots", response_model=BusinessEnvironmentSnapshot, status_code=201)
async def capture(project_id: UUID, environment_id: UUID, payload: BusinessEnvironmentCaptureRequest, request: Request):
    return await _call(request, project_id, "capture", str(environment_id), payload)


@router.get("/{environment_id}/snapshots", response_model=BusinessEnvironmentSnapshotPage)
async def list_snapshots(project_id: UUID, environment_id: UUID, request: Request,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "list_snapshots", str(environment_id), limit=limit, offset=offset)


@router.get("/{environment_id}/snapshots/by-request", response_model=BusinessEnvironmentSnapshot)
async def get_capture(project_id: UUID, environment_id: UUID, request: Request,
    request_id: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, "get_capture", str(environment_id), request_id)


@router.get("/{environment_id}/snapshots/{snapshot_id}", response_model=BusinessEnvironmentSnapshot)
async def get_snapshot(project_id: UUID, environment_id: UUID, snapshot_id: UUID, request: Request):
    return await _call(request, project_id, "get_snapshot", str(environment_id), str(snapshot_id))

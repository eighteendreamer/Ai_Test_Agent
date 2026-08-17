from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.application.projects.project_store import (
    DuplicateGraphScopeError,
    DuplicateProjectKeyError,
)
from src.schemas.project import (
    ProjectCreateRequest,
    ProjectOverview,
    ProjectPage,
    ProjectRecord,
    ProjectStatus,
    ProjectUpdateRequest,
)
from src.schemas.legacy_smoke_history import (
    LegacySmokeRunPage,
    LegacySmokeScopeBinding,
    LegacySmokeScopeBindingRequest,
)


router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRecord, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreateRequest, request: Request):
    try:
        return await request.app.state.project_service.create(payload)
    except (DuplicateProjectKeyError, DuplicateGraphScopeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=ProjectPage)
async def list_projects(
    request: Request,
    project_status: ProjectStatus | None = Query(default=None, alias="status"),
    query: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await request.app.state.project_service.list(
        status=project_status,
        query=query,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectRecord)
async def get_project(project_id: str, request: Request):
    try:
        return await request.app.state.project_service.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/overview", response_model=ProjectOverview)
async def get_project_overview(project_id: str, request: Request):
    try:
        return await request.app.state.project_overview_service.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/legacy-smoke-runs", response_model=LegacySmokeRunPage)
async def list_legacy_smoke_runs(
    project_id: str,
    request: Request,
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        return await request.app.state.legacy_smoke_history_service.list_runs(
            project_id,
            cursor=cursor,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put(
    "/{project_id}/legacy-smoke-bindings",
    response_model=LegacySmokeScopeBinding,
)
async def bind_legacy_smoke_scope(
    project_id: str,
    payload: LegacySmokeScopeBindingRequest,
    request: Request,
):
    try:
        return await request.app.state.legacy_smoke_history_service.bind_scope(
            project_id,
            payload.project_scope,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/{project_id}/legacy-smoke-bindings/{project_scope}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_legacy_smoke_scope(
    project_id: str,
    project_scope: str,
    request: Request,
):
    try:
        await request.app.state.legacy_smoke_history_service.unbind_scope(
            project_id,
            project_scope,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{project_id}", response_model=ProjectRecord)
async def update_project(project_id: str, payload: ProjectUpdateRequest, request: Request):
    try:
        return await request.app.state.project_service.update(project_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, DuplicateGraphScopeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{project_id}/archive", response_model=ProjectRecord)
async def archive_project(project_id: str, request: Request):
    try:
        return await request.app.state.project_service.archive(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

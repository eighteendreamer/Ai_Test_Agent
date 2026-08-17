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

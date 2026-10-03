from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from src.schemas.case_planning_task import (
    CasePlanningTaskPage, CasePlanningTaskRecord, CasePlanningTaskRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects/{project_id}/case-planning-tasks", tags=["case-planning-tasks"])


async def _call(request, project_id, operation, *args, **kwargs):
    try:
        return await getattr(request.app.state.case_planning_task_service, operation)(str(project_id), *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning("case_planning_task_rejected", extra={"project_id": str(project_id), "operation": operation})
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.error("case_planning_task_unavailable", extra={"project_id": str(project_id),
            "operation": operation, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503,
            detail="规划任务服务暂时不可用；提交结果尚不能确认时，请按原请求身份查询或重试") from None


@router.post("", response_model=CasePlanningTaskRecord, status_code=202)
async def submit(project_id: UUID, payload: CasePlanningTaskRequest, request: Request):
    return await _call(request, project_id, "submit", payload)


@router.get("/by-request", response_model=CasePlanningTaskRecord)
async def get_by_request(project_id: UUID, request: Request, creation_key: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, "get_by_request", creation_key)


@router.get("", response_model=CasePlanningTaskPage)
async def list_tasks(project_id: UUID, request: Request, limit: int = Query(default=24, ge=1, le=100),
                     offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, "list", limit=limit, offset=offset)


@router.get("/{task_id}", response_model=CasePlanningTaskRecord)
async def get(project_id: UUID, task_id: UUID, request: Request):
    return await _call(request, project_id, "get", str(task_id))

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from src.schemas.task_pool import TaskPoolPage
from src.schemas.task_pool import TaskPoolEntriesPage, TaskPoolKind, TaskPoolState


router = APIRouter(prefix="/task-pool", tags=["task-pool"])
logger = logging.getLogger(__name__)


@router.get("/entries", response_model=TaskPoolEntriesPage)
async def list_task_pool_entries(request: Request, limit: int = Query(default=24, ge=1, le=100),
                                offset: int = Query(default=0, ge=0), kind: TaskPoolKind = "all",
                                state: TaskPoolState = "all", query: str = Query(default="", max_length=500),
                                project_id: UUID | None = None):
    try:
        page = await request.app.state.task_pool_service.list_entries_page(limit=limit, offset=offset,
            kind=kind, state=state, query=query, project_id=str(project_id) if project_id else None)
    except Exception as exc:
        logger.error("task_pool_entries_unavailable", extra={"project_id": str(project_id) if project_id else None,
            "kind": kind, "state": state, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="任务池暂时不可用，请刷新重试；已有任务不会重新提交") from None
    logger.debug("task_pool_entries_listed", extra={"project_id": str(project_id) if project_id else None,
        "kind": kind, "state": state, "offset": offset, "item_count": len(page.items), "has_more": page.has_more})
    return page


@router.get("", response_model=TaskPoolPage)
async def list_task_pool(request: Request, limit: int = 24, offset: int = 0):
    return await request.app.state.task_pool_service.list_tasks_page(
        limit=limit,
        offset=offset,
    )

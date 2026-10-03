from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from src.schemas.case_planning import CasePlanRecord, CasePlanRunRequest, CasePlanningConfig, CasePlanningPolicy, CasePlanningRequest
from src.schemas.run_management import TestRunDetail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects/{project_id}/case-plans", tags=["case-plans"])


async def _call(request, project_id, operation, *args):
    try:
        return await getattr(request.app.state.case_planning_service, operation)(str(project_id), *args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning("case_plan_request_rejected", extra={"project_id": str(project_id), "operation": operation})
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.error("case_plan_request_failed", extra={"project_id": str(project_id), "operation": operation, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="用例规划服务暂时不可用，未创建测试运行") from None


@router.get("/policy", response_model=CasePlanningConfig)
async def get_policy(project_id: UUID, request: Request):
    return await _call(request, project_id, "get_policy")


@router.put("/policy", response_model=CasePlanningConfig)
async def save_policy(project_id: UUID, payload: CasePlanningPolicy, request: Request):
    return await _call(request, project_id, "save_policy", payload)


@router.post("", response_model=CasePlanRecord, status_code=201)
async def create(project_id: UUID, payload: CasePlanningRequest, request: Request):
    return await _call(request, project_id, "create", payload)


@router.get("/{plan_id}", response_model=CasePlanRecord)
async def get(project_id: UUID, plan_id: UUID, request: Request):
    return await _call(request, project_id, "get", str(plan_id))


@router.post("/{plan_id}/runs", response_model=TestRunDetail, status_code=201)
async def launch(project_id: UUID, plan_id: UUID, payload: CasePlanRunRequest, request: Request):
    context = {"project_id": str(project_id), "plan_id": str(plan_id), "operation": "launch",
        "creation_key": payload.creation_key, "mode_key": payload.mode_key}
    try:
        return await request.app.state.case_plan_run_service.create(
            str(project_id), str(plan_id), payload, require_dispatch=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning("case_plan_launch_rejected", extra=context)
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.error("case_plan_launch_failed", extra={**context, "error_type": type(exc).__name__})
        # A failed acknowledgement does not prove the transaction rolled back.
        raise HTTPException(status_code=503,
            detail="启动结果暂时无法确认，请保留相同创建请求重试或查询运行记录") from None

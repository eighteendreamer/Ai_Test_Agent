"""Scoped append-only applicability decisions, not deployment probes."""
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from src.schemas.business_applicability import BusinessApplicabilityRequest, BusinessApplicabilityReview, BusinessApplicabilityPage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects/{project_id}/business-environments/{environment_id}/snapshots/{snapshot_id}/applicability-reviews",
    tags=["business-environments"])


async def _call(request, project_id, environment_id, snapshot_id, operation, *args, **kwargs):
    try:
        return await getattr(request.app.state.business_applicability_service, operation)(
            str(project_id), str(environment_id), str(snapshot_id), *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning("business_applicability_request_rejected", extra={"project_id": str(project_id), "operation": operation})
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.error("business_applicability_service_unavailable", extra={"project_id": str(project_id),
            "operation": operation, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="环境适用确认暂不可用；提交回执未知时请按原请求编号查询，保留原参数") from None


@router.post("", response_model=BusinessApplicabilityReview, status_code=201)
async def submit(project_id: UUID, environment_id: UUID, snapshot_id: UUID,
    payload: BusinessApplicabilityRequest, request: Request):
    return await _call(request, project_id, environment_id, snapshot_id, "submit", payload)


@router.get("", response_model=BusinessApplicabilityPage)
async def list_reviews(project_id: UUID, environment_id: UUID, snapshot_id: UUID, requirement_version_id: UUID, request: Request,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, environment_id, snapshot_id, "list",
        requirement_version_id=str(requirement_version_id), limit=limit, offset=offset)


@router.get("/by-request", response_model=BusinessApplicabilityReview)
async def by_request(project_id: UUID, environment_id: UUID, snapshot_id: UUID, request: Request,
    request_id: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, environment_id, snapshot_id, "by_request", request_id)


@router.get("/{review_id}", response_model=BusinessApplicabilityReview)
async def get_review(project_id: UUID, environment_id: UUID, snapshot_id: UUID, review_id: UUID, request: Request):
    return await _call(request, project_id, environment_id, snapshot_id, "get", str(review_id))

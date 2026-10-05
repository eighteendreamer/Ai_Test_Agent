from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.api.document_operation_calls import call_document_operation
from src.schemas.requirements import (
    RequirementMatrix, RequirementPage, RequirementReview, RequirementReviewPage,
    RequirementReviewRequest, RequirementVersion,
    RequirementCoverageRequest, RequirementCoverageReview, RequirementCoveragePage,
)

router = APIRouter(prefix="/projects/{project_id}/documents/{document_id}", tags=["requirements"])


async def _call(request, project_id, document_id, operation, *args, **kwargs):
    return await call_document_operation(request, project_id, "requirement_service", operation, str(document_id),
        *args, log_prefix="requirement", label="需求评审", **kwargs)


@router.post("/impact-snapshots/{snapshot_id}/reviews", response_model=RequirementReview, status_code=201)
async def submit_review(project_id: UUID, document_id: UUID, snapshot_id: UUID,
    payload: RequirementReviewRequest, request: Request):
    return await _call(request, project_id, document_id, "submit_review", str(snapshot_id), payload)


@router.get("/requirement-reviews/by-request", response_model=RequirementReview)
async def get_by_request(project_id: UUID, document_id: UUID, request: Request,
    request_id: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, document_id, "get_by_request", request_id)


@router.get("/impact-snapshots/{snapshot_id}/reviews", response_model=RequirementReviewPage)
async def list_reviews(project_id: UUID, document_id: UUID, snapshot_id: UUID, request: Request,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, document_id, "list_reviews", str(snapshot_id), limit=limit, offset=offset)


@router.get("/requirements", response_model=RequirementPage)
async def list_requirements(project_id: UUID, document_id: UUID, request: Request,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, document_id, "list_requirements", limit=limit, offset=offset)


@router.get("/requirements/{requirement_id}/versions", response_model=list[RequirementVersion])
async def list_versions(project_id: UUID, document_id: UUID, requirement_id: UUID, request: Request):
    return await _call(request, project_id, document_id, "list_versions", str(requirement_id))


@router.get("/requirements/{requirement_id}/matrix", response_model=RequirementMatrix)
async def read_matrix(project_id: UUID, document_id: UUID, requirement_id: UUID, request: Request,
    version_id: UUID | None = None,
    coverage_id: str | None = Query(default=None, pattern=r"^(initial|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")):
    return await _call(request, project_id, document_id, "read_matrix", str(requirement_id),
        version_id=str(version_id) if version_id else None, coverage_id=coverage_id)


@router.post("/requirements/{requirement_id}/versions/{version_id}/coverage-reviews",
    response_model=RequirementCoverageReview, status_code=201)
async def submit_coverage(project_id: UUID, document_id: UUID, requirement_id: UUID, version_id: UUID,
    payload: RequirementCoverageRequest, request: Request):
    return await _call(request, project_id, document_id, "submit_coverage", str(requirement_id), str(version_id), payload)


@router.get("/requirements/{requirement_id}/versions/{version_id}/coverage-reviews", response_model=RequirementCoveragePage)
async def list_coverage(project_id: UUID, document_id: UUID, requirement_id: UUID, version_id: UUID, request: Request,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return await _call(request, project_id, document_id, "list_coverage", str(requirement_id), str(version_id), limit=limit, offset=offset)


@router.get("/requirement-coverage-reviews/by-request", response_model=RequirementCoverageReview)
async def get_coverage_by_request(project_id: UUID, document_id: UUID, request: Request,
    request_id: str = Query(min_length=1, max_length=160)):
    return await _call(request, project_id, document_id, "get_coverage_by_request", request_id)

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.application.test_cases.case_store import DuplicateCaseKeyError
from src.schemas.case_management import (
    TestCaseActivateRequest,
    TestCaseGenerateRequest,
    TestCaseGenerationResponse,
    TestCaseLifecycleStatus,
    TestCasePage,
    TestCasePriority,
    TestCaseRecord,
    TestCaseVersionCreateRequest,
    TestCaseVersionRecord,
)


router = APIRouter(tags=["test-cases"])


@router.get("/projects/{project_id}/test-cases", response_model=TestCasePage)
async def list_test_cases(
    project_id: str,
    request: Request,
    lifecycle_status: TestCaseLifecycleStatus | None = Query(default=None, alias="status"),
    mode_key: str | None = Query(default=None, max_length=80),
    priority: TestCasePriority | None = None,
    query: str | None = Query(default=None, max_length=240),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await request.app.state.test_case_service.list_cases(
        project_id=project_id,
        status=lifecycle_status,
        mode_key=mode_key,
        priority=priority,
        query=query,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/projects/{project_id}/test-cases/generate",
    response_model=TestCaseGenerationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_test_cases(
    project_id: str,
    payload: TestCaseGenerateRequest,
    request: Request,
):
    try:
        return await request.app.state.test_case_service.generate(project_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, DuplicateCaseKeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/test-cases/{case_id}", response_model=TestCaseRecord)
async def get_test_case(case_id: str, request: Request):
    try:
        return await request.app.state.test_case_service.get_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/test-cases/{case_id}/versions", response_model=list[TestCaseVersionRecord])
async def list_test_case_versions(case_id: str, request: Request):
    try:
        return await request.app.state.test_case_service.list_versions(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/test-cases/{case_id}/versions",
    response_model=TestCaseVersionRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_case_version(
    case_id: str,
    payload: TestCaseVersionCreateRequest,
    request: Request,
):
    try:
        return await request.app.state.test_case_service.create_version(case_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/test-cases/{case_id}/submit-review", response_model=TestCaseRecord)
async def submit_test_case_review(case_id: str, request: Request):
    try:
        return await request.app.state.test_case_service.submit_review(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/test-cases/{case_id}/activate", response_model=TestCaseRecord)
async def activate_test_case(
    case_id: str,
    payload: TestCaseActivateRequest,
    request: Request,
):
    try:
        return await request.app.state.test_case_service.activate(case_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/test-cases/{case_id}/archive", response_model=TestCaseRecord)
async def archive_test_case(case_id: str, request: Request):
    try:
        return await request.app.state.test_case_service.archive(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

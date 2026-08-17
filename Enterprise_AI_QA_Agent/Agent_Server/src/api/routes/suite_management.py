from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.schemas.suite_management import (
    TestSuiteBundle,
    TestSuiteCreateRequest,
    TestSuitePage,
    TestSuiteRecord,
)


router = APIRouter(tags=["test-suites"])


@router.get("/projects/{project_id}/suites", response_model=TestSuitePage)
async def list_test_suites(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await request.app.state.test_suite_service.list(
            project_id,
            limit=limit,
            offset=offset,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/suites",
    response_model=TestSuiteBundle,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_suite(
    project_id: str,
    payload: TestSuiteCreateRequest,
    request: Request,
):
    try:
        return await request.app.state.test_suite_service.create(project_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/suites/{suite_id}", response_model=TestSuiteBundle)
async def get_test_suite(suite_id: str, request: Request):
    try:
        return await request.app.state.test_suite_service.get(suite_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/suites/{suite_id}/archive", response_model=TestSuiteRecord)
async def archive_test_suite(suite_id: str, request: Request):
    try:
        return await request.app.state.test_suite_service.archive(suite_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

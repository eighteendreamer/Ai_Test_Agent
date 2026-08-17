from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.schemas.run_management import (
    LeaseRecoveryResponse,
    RunCancelRequest,
    RunClaimRequest,
    RunClaimResponse,
    RunItemCompleteRequest,
    RunItemExecuteRequest,
    RunItemHeartbeatRequest,
    RunItemLeaseRequest,
    TestCaseResultRecord,
    TestRunCreateRequest,
    TestRunDetail,
    TestRunItemRecord,
    TestRunPage,
    TestRunStatus,
)


router = APIRouter(tags=["test-runs"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/suites/{suite_id}/runs",
    response_model=TestRunDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_run(
    suite_id: str,
    payload: TestRunCreateRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_service.create_run(suite_id, payload)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _http_error(exc) from exc


@router.get("/projects/{project_id}/runs", response_model=TestRunPage)
async def list_test_runs(
    project_id: str,
    request: Request,
    run_status: TestRunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await request.app.state.test_run_service.list(
            project_id,
            status=run_status,
            limit=limit,
            offset=offset,
        )
    except KeyError as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}", response_model=TestRunDetail)
async def get_test_run(run_id: str, request: Request):
    try:
        return await request.app.state.test_run_service.get(run_id)
    except KeyError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/claim", response_model=RunClaimResponse)
async def claim_test_run_items(
    run_id: str,
    payload: RunClaimRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_service.claim(run_id, payload)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/run-items/{item_id}/start", response_model=TestRunItemRecord)
async def start_test_run_item(
    item_id: str,
    payload: RunItemLeaseRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_service.start_item(item_id, payload)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/run-items/{item_id}/heartbeat", response_model=TestRunItemRecord)
async def heartbeat_test_run_item(
    item_id: str,
    payload: RunItemHeartbeatRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_service.heartbeat_item(item_id, payload)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/run-items/{item_id}/complete", response_model=TestCaseResultRecord)
async def complete_test_run_item(
    item_id: str,
    payload: RunItemCompleteRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_service.complete_item(item_id, payload)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/run-items/{item_id}/execute", response_model=TestCaseResultRecord)
async def execute_test_run_item(
    item_id: str,
    payload: RunItemExecuteRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_execution_service.execute_item(
            item_id,
            payload,
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/recover-expired", response_model=LeaseRecoveryResponse)
async def recover_expired_test_run_leases(run_id: str, request: Request):
    try:
        return await request.app.state.test_run_service.recover_expired(run_id)
    except KeyError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/cancel", response_model=TestRunDetail)
async def cancel_test_run(
    run_id: str,
    payload: RunCancelRequest,
    request: Request,
):
    try:
        return await request.app.state.test_run_service.cancel(run_id, payload.reason)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.application.integrations.integration_catalog_service import (
    IntegrationNotFoundError,
    IntegrationValidationError,
)
from src.schemas.integration import IntegrationCreateRequest, IntegrationUpdateRequest

router = APIRouter(prefix="/registry/integrations", tags=["integrations"])
logger = logging.getLogger(__name__)


def _raise_integration_error(exc: Exception, *, operation: str) -> None:
    details = {"operation": operation, "error_type": type(exc).__name__}
    if isinstance(exc, IntegrationNotFoundError):
        logger.info("integration_not_found", extra=details)
        raise HTTPException(status_code=404, detail="Integration not found.") from exc
    if isinstance(exc, IntegrationValidationError):
        logger.info("integration_input_invalid", extra=details)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.error("integration_catalog_operation_failed", extra=details)
    raise HTTPException(status_code=500, detail="Integration operation failed.") from exc


@router.get("")
async def list_integrations(request: Request):
    try:
        return await request.app.state.integration_catalog_service.list_integrations()
    except Exception as exc:  # noqa: BLE001 - classify at the HTTP boundary without leaking credentials.
        _raise_integration_error(exc, operation="list")


@router.get("/{integration_id}")
async def get_integration(integration_id: str, request: Request):
    try:
        return await request.app.state.integration_catalog_service.get_integration(integration_id)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected catalog errors.
        _raise_integration_error(exc, operation="get")


@router.post("")
async def create_integration(payload: IntegrationCreateRequest, request: Request):
    try:
        return await request.app.state.integration_catalog_service.create_integration(payload)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected catalog errors.
        _raise_integration_error(exc, operation="create")


@router.patch("/{integration_id}")
async def update_integration(integration_id: str, payload: IntegrationUpdateRequest, request: Request):
    try:
        return await request.app.state.integration_catalog_service.update_integration(integration_id, payload)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected catalog errors.
        _raise_integration_error(exc, operation="update")


@router.delete("/{integration_id}")
async def delete_integration(integration_id: str, request: Request):
    try:
        return await request.app.state.integration_catalog_service.delete_integration(integration_id)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected catalog errors.
        _raise_integration_error(exc, operation="delete")


@router.post("/{integration_id}/test")
async def test_integration(integration_id: str, request: Request):
    try:
        return await request.app.state.integration_catalog_service.test_integration(integration_id)
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected catalog errors.
        _raise_integration_error(exc, operation="test")


@router.get("/{integration_id}/import-sources")
async def list_integration_import_sources(
    integration_id: str,
    request: Request,
    workspace_id: str | None = None,
):
    try:
        return await request.app.state.integration_catalog_service.list_import_sources(
            integration_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected catalog errors.
        _raise_integration_error(exc, operation="import_sources")

from __future__ import annotations

from fastapi import APIRouter, Request

from src.schemas.email_config import EmailConfigUpdateRequest
from src.schemas.settings import ModelConfigUpdateRequest


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/models")
async def list_model_configs(request: Request):
    return request.app.state.settings_service.list_model_configs()


@router.put("/models")
async def update_model_config(payload: ModelConfigUpdateRequest, request: Request):
    return request.app.state.settings_service.update_model_config(payload)


@router.get("/email")
async def list_email_configs(request: Request):
    return request.app.state.settings_service.list_email_configs()


@router.put("/email")
async def update_email_config(payload: EmailConfigUpdateRequest, request: Request):
    return request.app.state.settings_service.update_email_config(payload)

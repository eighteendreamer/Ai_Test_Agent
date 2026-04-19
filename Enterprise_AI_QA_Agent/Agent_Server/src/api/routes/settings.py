from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from src.schemas.email_config import EmailConfigUpdateRequest
from src.schemas.settings import ModelConfigUpdateRequest


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/models")
async def list_model_configs(request: Request):
    return request.app.state.settings_service.list_model_configs()


@router.put("/models")
async def update_model_config(payload: ModelConfigUpdateRequest, request: Request):
    return request.app.state.settings_service.update_model_config(payload)


@router.patch("/models/{model_name}")
async def edit_model_config(model_name: str, payload: ModelConfigUpdateRequest, request: Request):
    try:
        return request.app.state.settings_service.edit_model_config(model_name, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/models/{model_name}/activate")
async def activate_model_config(model_name: str, request: Request):
    try:
        return request.app.state.settings_service.activate_model_config(model_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc


@router.post("/models/{model_name}/test-connection")
async def test_model_config_connection(model_name: str, request: Request):
    try:
        return request.app.state.settings_service.test_model_config_connection(model_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc


@router.delete("/models/{model_name}")
async def delete_model_config(model_name: str, request: Request):
    try:
        return request.app.state.settings_service.delete_model_config(model_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc


@router.get("/email")
async def list_email_configs(request: Request):
    return request.app.state.settings_service.list_email_configs()


@router.put("/email")
async def update_email_config(payload: EmailConfigUpdateRequest, request: Request):
    return request.app.state.settings_service.update_email_config(payload)

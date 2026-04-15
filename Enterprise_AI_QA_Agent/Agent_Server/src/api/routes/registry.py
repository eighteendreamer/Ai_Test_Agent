from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter(prefix="/registry", tags=["registry"])


@router.get("/framework")
async def framework_summary(request: Request):
    return request.app.state.registry_service.framework_summary()


@router.get("/agents")
async def list_agents(request: Request):
    return request.app.state.registry_service.list_agents()


@router.get("/tools")
async def list_tools(request: Request):
    return request.app.state.registry_service.list_tools()


@router.get("/models")
async def list_models(request: Request):
    return request.app.state.registry_service.list_models()


@router.get("/models/configs")
async def list_model_configs(request: Request):
    return request.app.state.registry_service.list_model_configs()


@router.get("/skills")
async def list_skills(request: Request):
    return request.app.state.registry_service.list_skills()


@router.get("/mcp")
async def list_mcp_servers(request: Request):
    return request.app.state.registry_service.list_mcp_servers()

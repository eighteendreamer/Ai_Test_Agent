from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from src.schemas.mcp_management import (
    MCPServerCreateRequest,
    MCPServerImportRequest,
    MCPServerUpdateRequest,
)

router = APIRouter(prefix="/registry", tags=["registry"])


class SkillUpsertRequest(BaseModel):
    content: str = Field(..., description="Full SKILL.md content.")


class SkillInstallRequest(BaseModel):
    source_path: str | None = Field(default=None, description="Local skill directory, SKILL.md, or zip path.")
    url: str | None = Field(default=None, description="HTTP(S) URL to a SKILL.md file or zip archive.")
    key: str | None = Field(default=None, description="Optional installed skill folder name.")
    overwrite: bool = False


class SkillUploadRequest(BaseModel):
    filename: str = Field(..., description="Original uploaded filename.")
    content_base64: str = Field(..., description="Base64 encoded SKILL.md or zip bytes.")
    key: str | None = Field(default=None, description="Optional installed skill folder name.")
    overwrite: bool = False


class SkillMarketplaceInstallRequest(BaseModel):
    source: str = Field(..., description="Marketplace source: anthropic or skillsmp.")
    skill_id: str = Field(..., description="Marketplace skill id/slug.")
    url: str | None = Field(default=None, description="Optional downloadable URL for SkillsMP results.")
    key: str | None = Field(default=None, description="Optional installed skill folder name.")
    overwrite: bool = False


class MCPToolCallRequest(BaseModel):
    arguments: dict[str, object] = Field(default_factory=dict, description="Arguments passed to the MCP tool.")


@router.get("/framework")
async def framework_summary(request: Request):
    return request.app.state.registry_service.framework_summary()


@router.get("/agents")
async def list_agents(request: Request):
    return request.app.state.registry_service.list_agents()


@router.get("/tools")
async def list_tools(request: Request):
    return request.app.state.registry_service.list_tools()


@router.get("/tools/summary")
async def list_tool_summaries(request: Request):
    return request.app.state.registry_service.list_tool_summaries()


@router.get("/tools/search")
async def search_tools(
    request: Request,
    q: str = "",
    limit: int = Query(10, ge=1, le=50),
    include_schema: bool = False,
):
    return request.app.state.registry_service.search_tools(
        query=q,
        limit=limit,
        include_schema=include_schema,
    )


@router.get("/tools/{tool_key}")
async def get_tool(tool_key: str, request: Request):
    try:
        return request.app.state.registry_service.get_tool(tool_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Tool not found") from exc


@router.get("/modes")
async def list_modes(request: Request):
    return request.app.state.registry_service.list_modes()


@router.get("/models")
async def list_models(request: Request):
    return request.app.state.registry_service.list_models()


@router.get("/models/configs")
async def list_model_configs(request: Request):
    return request.app.state.registry_service.list_model_configs()


@router.get("/security-profiles")
async def list_security_profiles(request: Request):
    return request.app.state.registry_service.list_security_profiles()


@router.get("/skills")
async def list_skills(request: Request):
    return request.app.state.skill_management_service.list_skills()


@router.get("/skills/marketplaces")
async def list_skill_marketplaces(request: Request):
    return request.app.state.skill_marketplace_service.list_marketplaces()


@router.get("/skills/marketplaces/search")
async def search_skill_marketplace(
    source: str,
    request: Request,
    q: str = "",
    limit: int = 20,
):
    try:
        return await asyncio.to_thread(
            request.app.state.skill_marketplace_service.search,
            source=source,
            query=q,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skills/marketplaces/preview")
async def preview_skill_marketplace(
    source: str,
    skill_id: str,
    request: Request,
    url: str | None = None,
):
    try:
        return await asyncio.to_thread(
            request.app.state.skill_marketplace_service.preview,
            source=source,
            skill_id=skill_id,
            url=url,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/marketplaces/install")
async def install_skill_marketplace(payload: SkillMarketplaceInstallRequest, request: Request):
    try:
        return await asyncio.to_thread(
            request.app.state.skill_marketplace_service.install,
            source=payload.source,
            skill_id=payload.skill_id,
            url=payload.url,
            key=payload.key,
            overwrite=payload.overwrite,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skills/{skill_key}")
async def get_skill(skill_key: str, request: Request):
    try:
        return request.app.state.skill_management_service.get_skill(skill_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/skills/{skill_key}")
async def upsert_skill(skill_key: str, payload: SkillUpsertRequest, request: Request):
    try:
        return request.app.state.skill_management_service.upsert_skill(skill_key, payload.content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/skills/{skill_key}")
async def delete_skill(skill_key: str, request: Request):
    try:
        return request.app.state.skill_management_service.delete_skill(skill_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/install")
async def install_skill(payload: SkillInstallRequest, request: Request):
    try:
        if payload.source_path:
            return await asyncio.to_thread(
                request.app.state.skill_management_service.install_from_path,
                source_path=payload.source_path,
                key=payload.key,
                overwrite=payload.overwrite,
            )
        if payload.url:
            return await asyncio.to_thread(
                request.app.state.skill_management_service.install_from_url,
                url=payload.url,
                key=payload.key,
                overwrite=payload.overwrite,
            )
        raise ValueError("Either source_path or url is required.")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/upload")
async def upload_skill(payload: SkillUploadRequest, request: Request):
    try:
        return await request.app.state.skill_management_service.install_from_upload(
            filename=payload.filename,
            content_base64=payload.content_base64,
            key=payload.key,
            overwrite=payload.overwrite,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/mcp")
async def list_mcp_servers(request: Request):
    return request.app.state.registry_service.list_mcp_servers()


@router.get("/mcp/managed")
async def list_managed_mcp_servers(request: Request):
    return await request.app.state.registry_service.list_managed_mcp_servers()


@router.post("/mcp/managed")
async def create_managed_mcp_server(payload: MCPServerCreateRequest, request: Request):
    try:
        return await request.app.state.mcp_manager_service.create_server(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mcp/managed/import")
async def import_managed_mcp_servers(payload: MCPServerImportRequest, request: Request):
    try:
        return await request.app.state.mcp_manager_service.import_servers(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/mcp/providers")
async def list_mcp_providers(request: Request):
    return request.app.state.registry_service.list_mcp_providers()


@router.get("/mcp/managed/{server_key}/tools")
async def list_managed_mcp_tools(server_key: str, request: Request):
    try:
        return await request.app.state.registry_service.list_managed_mcp_tools(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/mcp/managed/{server_key}/resources")
async def list_managed_mcp_resources(server_key: str, request: Request):
    try:
        return await request.app.state.registry_service.list_managed_mcp_resources(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/mcp/managed/{server_key}/prompts")
async def list_managed_mcp_prompts(server_key: str, request: Request):
    try:
        return await request.app.state.registry_service.list_managed_mcp_prompts(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mcp/managed/{server_key}/test")
async def test_managed_mcp_server(server_key: str, request: Request):
    try:
        return await request.app.state.registry_service.test_managed_mcp_server(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/mcp/managed/{server_key}")
async def update_managed_mcp_server(server_key: str, payload: MCPServerUpdateRequest, request: Request):
    try:
        return await request.app.state.mcp_manager_service.update_server(server_key, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/mcp/managed/{server_key}")
async def delete_managed_mcp_server(server_key: str, request: Request):
    try:
        return await request.app.state.mcp_manager_service.delete_server(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mcp/managed/{server_key}/confirm-stdio")
async def confirm_managed_mcp_stdio_server(server_key: str, request: Request):
    try:
        return await request.app.state.mcp_manager_service.confirm_stdio_server(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mcp/managed/{server_key}/reconnect")
async def reconnect_managed_mcp_server(server_key: str, request: Request):
    try:
        return await request.app.state.mcp_manager_service.reconnect_server(server_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mcp/managed/{server_key}/tools/{tool_name}/call")
async def call_managed_mcp_tool(
    server_key: str,
    tool_name: str,
    payload: MCPToolCallRequest,
    request: Request,
):
    try:
        return await request.app.state.registry_service.call_managed_mcp_tool(
            server_key,
            tool_name,
            payload.arguments,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

from __future__ import annotations
from fastapi import APIRouter, Request


router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    memory_runtime_service = getattr(request.app.state, "memory_runtime_service", None)
    memory_backend = getattr(request.app.state, "memory_backend", "uninitialized")
    if memory_runtime_service is not None:
        memory_backend = await memory_runtime_service.refresh_backend_status()

    return {
        "status": "ok",
        "name": settings.app_name,
        "environment": settings.app_env,
        "memory_backend": memory_backend,
        "knowledge_enabled": bool(getattr(settings, "qdrant_enabled", False)),
        "knowledge_target": getattr(settings, "qdrant_url", ""),
    }


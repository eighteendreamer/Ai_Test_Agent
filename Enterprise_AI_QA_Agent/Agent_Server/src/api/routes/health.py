from __future__ import annotations
from fastapi import APIRouter, Request


router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    return {
        "status": "ok",
        "name": settings.app_name,
        "environment": settings.app_env,
        "memory_backend": getattr(
            getattr(request.app.state, "memory_runtime_service", None),
            "backend",
            getattr(request.app.state, "memory_backend", "uninitialized"),
        ),
        "knowledge_enabled": bool(getattr(settings, "qdrant_enabled", False)),
        "knowledge_target": getattr(settings, "qdrant_url", ""),
    }


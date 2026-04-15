from __future__ import annotations
from fastapi import APIRouter, Request


router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "name": request.app.state.settings.app_name,
        "environment": request.app.state.settings.app_env,
        "memory_backend": getattr(
            getattr(request.app.state, "memory_runtime_service", None),
            "backend",
            getattr(request.app.state, "memory_backend", "uninitialized"),
        ),
    }


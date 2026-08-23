from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request


router = APIRouter(prefix="/sponsors", tags=["sponsors"])


@router.get("")
async def list_sponsors(request: Request):
    return await asyncio.to_thread(
        request.app.state.sponsor_config_store.list_enabled
    )

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from src.api.routes.resource_occupancy import router


class Manager:
    async def list_active(self, *, project_id=None):
        return [{"resource_type": "browser", "project_id": project_id, "external_resource_id": "browser-session"}]
    async def usage_snapshot(self): return {"global:all": 1}


def test_occupancy_route_groups_active_leases():
    async def run():
        app = FastAPI(); app.state.resource_lease_manager = Manager(); app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/resource-occupancy?project_id=p1")
            assert response.status_code == 200
            body = response.json()
            assert body["active_count"] == 1 and body["by_type"]["browser"] == 1
    asyncio.run(run())

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

from src.api.routes.resource_quotas import router
from src.schemas.resource_quota import ResourceQuotaRecord


class Projects:
    async def get(self, project_id):
        return project_id


class Quotas:
    def __init__(self):
        self.value = None

    async def list_project(self, project_id):
        return [self.value] if self.value else []

    async def upsert(self, project_id, resource_type, limit):
        self.value = ResourceQuotaRecord(
            scope_id=project_id, resource_type=resource_type, limit=limit,
            updated_at=datetime.now(timezone.utc),
        )
        return self.value


def test_project_quota_routes_persist_and_read_contract():
    async def run():
        app = FastAPI()
        quotas = Quotas()
        app.state.project_service = Projects()
        app.state.resource_quota_service = quotas
        app.include_router(router, prefix="/api/v1")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.put(
                "/api/v1/projects/project-1/resource-quotas",
                json={"resource_type": "docker", "limit": 3},
            )
            assert response.status_code == 200 and response.json()["limit"] == 3
            response = await client.get("/api/v1/projects/project-1/resource-quotas")
            assert response.status_code == 200 and response.json()[0]["resource_type"] == "docker"

    asyncio.run(run())

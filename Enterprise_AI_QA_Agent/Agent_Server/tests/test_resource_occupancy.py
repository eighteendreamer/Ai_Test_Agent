from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.api.routes.resource_occupancy import router


class Manager:
    async def list_active(self, *, project_id=None):
        return [{"resource_type": "browser", "project_id": project_id, "external_resource_id": "browser-session",
                 "lease_token": "test-only-ownership-token", "lease_key": "internal-key", "cleanup_stream": "internal-stream"}]
    async def usage_snapshot(self): return {"global:all": 1}
    async def list_cleanup_pending(self, *, project_id=None):
        return [{"resource_type": "docker", "project_id": project_id, "status": "cleanup_pending"}]


def test_occupancy_route_groups_active_leases():
    async def run():
        app = FastAPI(); app.state.resource_lease_manager = Manager(); app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/resource-occupancy?project_id=p1")
            assert response.status_code == 200
            body = response.json()
            assert body["active_count"] == 1 and body["by_type"]["browser"] == 1
            assert body["cleanup_pending_count"] == 1
            assert body["cleanup_pending"][0]["project_id"] == "p1"
            assert not {"lease_token", "lease_key", "cleanup_stream"}.intersection(body["leases"][0])
    asyncio.run(run())


@pytest.mark.asyncio
async def test_occupancy_failure_is_not_reported_as_empty():
    class OfflineManager(Manager):
        async def list_active(self, **kwargs):
            raise RedisConnectionError("injected offline")
    app = FastAPI()
    app.state.resource_lease_manager = OfflineManager()
    app.include_router(router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/v1/resource-occupancy?project_id=p1")
        assert response.status_code == 503
        assert "active_count" not in response.json()


@pytest.mark.asyncio
async def test_live_occupancy_tracks_project_cleanup_without_exposing_tokens(monkeypatch):
    from tests.live_postgres_config import LivePostgresTestConfig
    from src.core.config import get_settings
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager

    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for real Redis occupancy API")
    prefix = "qa:test:occupancy:" + uuid4().hex
    manager = RedisResourceLeaseManager(get_settings().database.redis_url, key_prefix=prefix,
                                       cleanup_stream=prefix + ":cleanup")
    await manager.connect()
    app = FastAPI()
    app.state.resource_lease_manager = manager
    app.include_router(router, prefix="/api/v1")
    try:
        first = await manager.acquire(resource_type="browser", resource_id="first", worker_id="w1",
                                      project_id="p1", session_id="s1", lease_seconds=60)
        await manager.acquire(resource_type="browser", resource_id="other", worker_id="w2",
                              project_id="p2", session_id="s2", lease_seconds=60)
        # Only a registry allocation: this test creates no external process.
        await manager.bind(first, "test-browser-allocation")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            active = (await client.get("/api/v1/resource-occupancy?project_id=p1")).json()
            assert active["active_count"] == 1
            assert active["quota_usage"]["project:p1"] == 1
            assert first.lease_token not in str(active)
            await manager.retire(first)
            retired = (await client.get("/api/v1/resource-occupancy?project_id=p1")).json()
            assert retired["active_count"] == 0
            assert retired["cleanup_pending_count"] == 1
            assert retired["quota_usage"]["project:p1"] == 1
            assert retired["cleanup_pending"][0]["session_id"] == "s1"
            assert first.lease_token not in str(retired)

            async def fail_read(*args, **kwargs):
                raise RedisConnectionError("injected lease read failure")

            monkeypatch.setattr(manager._client, "get", fail_read)
            assert (await client.get("/api/v1/resource-occupancy?project_id=p1")).status_code == 503
    finally:
        keys = [key async for key in manager._client.scan_iter(match=prefix + ":*")]
        if keys:
            await manager._client.delete(*keys)
        await manager.close()

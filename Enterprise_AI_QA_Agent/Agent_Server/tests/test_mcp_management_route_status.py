from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.api.routes.registry import router
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.mcp.manager_service import MCPManagerService
from src.application.mcp.runtime_manager import MCPRuntimeManager
from src.application.mcp.server_store import MCPServerStore, PostgresMCPServerStore
from src.application.registries.registry_service import RegistryService
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.agents import AgentRegistry
from src.registry.mcp import MCPRegistry
from src.registry.models import ModelRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from tests.live_postgres_config import LivePostgresTestConfig


@pytest_asyncio.fixture(params=["json", "postgres"])
async def app_and_store(request, tmp_path):
    table = f"test_mcp_status_{uuid4().hex}"
    original = get_settings()
    settings = original.model_copy(update={
        "data_dir": str(tmp_path),
        "storage": original.storage.model_copy(update={"artifact_root_dir": str(tmp_path / "artifacts")}),
        "database": original.database.model_copy(update={"postgres_mcp_server_table": table}),
    })
    if request.param == "postgres":
        if not LivePostgresTestConfig().run_live_postgres_tests:
            pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for the formal PostgreSQL MCP store")
        store = PostgresMCPServerStore(settings=settings)
        await store.initialize()
    else:
        store = MCPServerStore(settings=SimpleNamespace(data_dir=str(tmp_path)))
    builtin = MCPRegistry()
    runtime = MCPRuntimeService(builtin, settings)
    manager = MCPManagerService(
        builtin_registry=builtin, mcp_server_store=store,
        runtime_manager=MCPRuntimeManager(builtin_registry=builtin, mcp_runtime_service=runtime),
    )
    app = FastAPI()
    app.state.mcp_manager_service = manager
    app.state.registry_service = RegistryService(
        AgentRegistry(), ToolRegistry(), ModelRegistry(MySQLModelConfigStore(settings)),
        SkillRegistry(tmp_path / "skills"), builtin, ModeRegistry(), mcp_manager_service=manager,
    )
    app.include_router(router, prefix="/api/v1")
    try:
        yield app, store
    finally:
        await runtime.close()
        if request.param == "postgres":
            # Only the UUID table created by this fixture is removed.
            with postgres_connect(settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"DROP TABLE {table}")
                connection.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "payload"), [
    ("PATCH", "", {"name": "renamed"}), ("DELETE", "", None),
    ("POST", "/confirm-stdio", None), ("POST", "/reconnect", None),
])
async def test_missing_external_mcp_returns_not_found(app_and_store, method, suffix, payload):
    app, _ = app_and_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method, f"/api/v1/registry/mcp/managed/mcp:nonexistent{suffix}", json=payload,
        )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_mcp_management_crud_and_validation(app_and_store):
    app, _ = app_and_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post("/api/v1/registry/mcp/managed", json={"name": "", "command": "sample"})
        assert invalid.status_code == 400
        created = await client.post("/api/v1/registry/mcp/managed", json={
            "name": "isolated fixture", "transport": "stdio", "command": "sample", "enabled": False,
        })
        assert created.status_code == 200
        path = f"/api/v1/registry/mcp/managed/{created.json()['key']}"
        assert (await client.patch(path, json={"name": "updated"})).json()["name"] == "updated"
        assert (await client.post(f"{path}/confirm-stdio")).status_code == 200
        assert (await client.post(f"{path}/reconnect")).status_code == 400
        assert (await client.delete(path)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "payload"), [
    ("GET", "/tools", None), ("GET", "/resources", None), ("GET", "/prompts", None),
    ("POST", "/test", None), ("POST", "/tools/sample/call", {"arguments": {}}),
])
async def test_missing_mcp_registry_consumers_return_not_found(app_and_store, method, suffix, payload):
    app, _ = app_and_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method, f"/api/v1/registry/mcp/managed/mcp:nonexistent{suffix}", json=payload,
        )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_missing_host_component_is_unavailable(app_and_store):
    app, _ = app_and_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/registry/mcp/managed", json={
            "name": "configured without host", "transport": "stdio", "command": "sample",
        })
        assert created.status_code == 200
        response = await client.post(f"/api/v1/registry/mcp/managed/{created.json()['key']}/reconnect")
    assert response.status_code == 503, response.text


@pytest.mark.asyncio
async def test_invalid_compatibility_catalog_is_not_overwritten(app_and_store):
    app, store = app_and_store
    if isinstance(store, PostgresMCPServerStore):
        pytest.skip("JSON compatibility input only; the formal store has no catalog file")
    original = '{"wrong_shape": true}'
    store._catalog_path.write_text(original, encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/registry/mcp/managed")
        created = await client.post("/api/v1/registry/mcp/managed", json={
            "name": "sample", "command": "sample", "enabled": False,
        })
    assert listed.status_code == created.status_code == 500
    assert listed.json()["detail"] == "MCP management operation failed."
    assert store._catalog_path.read_text(encoding="utf-8") == original

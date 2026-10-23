from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.api.routes.registry import router
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.mcp.host.connection_manager import McpConnectionManager
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
from src.schemas.integration import IntegrationRecord
from src.schemas.mcp_management import MCPServerCreateRequest
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
@pytest.mark.parametrize("app_and_store", ["postgres"], indirect=True)
async def test_legacy_mcp_integration_migration_is_idempotent(app_and_store):
    _, store = app_and_store
    now = datetime.now(timezone.utc)
    integration = IntegrationRecord(
        id=f"legacy-{uuid4()}",
        name="legacy HTTP integration",
        kind="mcp",
        transport="http",
        endpoint_url="http://127.0.0.1:8765/mcp",
        headers={"X-Fixture": "migration"},
        capabilities=["tools"],
        created_at=now,
        updated_at=now,
    )

    first_count = await store.migrate_legacy_integrations([integration])
    second_count = await store.migrate_legacy_integrations([integration])
    migrated = [
        item for item in await store.list_servers()
        if item.metadata.get("legacy_integration_id") == integration.id
    ]

    assert first_count == 1
    assert second_count == 0
    assert len(migrated) == 1
    assert migrated[0].transport == "streamable_http"
    assert migrated[0].config["url"] == integration.endpoint_url
    assert migrated[0].config["headers"] == integration.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("app_and_store", ["postgres"], indirect=True)
async def test_legacy_mcp_schema_migration_preserves_record_fields(app_and_store):
    _, store = app_and_store
    table = store._settings.database.postgres_mcp_server_table
    record_id = f"legacy-schema-{uuid4()}"
    now = datetime.now(timezone.utc)
    legacy_columns = {
        "transport": "TEXT",
        "description": "TEXT",
        "project_name": "TEXT",
        "document_url": "TEXT",
        "endpoint_url": "TEXT",
        "command": "TEXT",
        "args": "JSONB",
        "headers": "JSONB",
        "env": "JSONB",
        "cwd": "TEXT",
        "provider_key": "TEXT",
        "capabilities": "JSONB",
    }
    with postgres_connect(store._settings) as connection, connection.cursor() as cursor:
        for name, data_type in legacy_columns.items():
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {data_type}")
        cursor.execute(
            f"""INSERT INTO {table} (
                id, name, enabled, purpose, config, supported_protocols, confirmed_at,
                metadata, created_at, updated_at, transport, description, project_name,
                document_url, endpoint_url, command, args, headers, env, cwd,
                provider_key, capabilities
            ) VALUES (
                %s, %s, TRUE, NULL, '{{}}'::jsonb, '[]'::jsonb, NULL, '{{}}'::jsonb,
                %s, %s, 'streamable_http', %s, %s, %s, %s, NULL, '[]'::jsonb,
                %s::jsonb, '{{}}'::jsonb, NULL, %s, %s::jsonb
            )""",
            (record_id, "legacy schema fixture", now, now, "legacy description",
             "legacy project", "https://docs.invalid/mcp", "https://mcp.invalid/api",
             '{"X-Legacy":"kept"}', "legacy-provider", '["legacy-capability"]'),
        )

    await store.initialize()
    record = await store.get_server(record_id)

    assert record.transport == "streamable_http"
    assert record.purpose == "legacy description"
    assert record.project_name == "legacy project"
    assert record.document_url == "https://docs.invalid/mcp"
    assert record.endpoint_url == "https://mcp.invalid/api"
    assert record.headers == {"X-Legacy": "kept"}
    assert record.provider_key == "legacy-provider"
    assert record.capabilities == ["legacy-capability"]


@pytest.mark.asyncio
@pytest.mark.parametrize("app_and_store", ["postgres"], indirect=True)
async def test_postgres_mcp_capabilities_round_trip(app_and_store):
    _, store = app_and_store
    created = await store.create_server(MCPServerCreateRequest(
        name="capability round trip",
        transport="stdio",
        command="fixture",
        capabilities=["tools", "resources"],
    ))

    loaded = await store.get_server(created.id)

    assert loaded.capabilities == ["tools", "resources"]


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
@pytest.mark.parametrize("app_and_store", ["json"], indirect=True)
async def test_invalid_compatibility_catalog_is_not_overwritten(app_and_store):
    app, store = app_and_store
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


def wire_native_host(app, store):
    manager = app.state.mcp_manager_service
    settings = manager._runtime_manager._mcp_runtime_service._settings
    host = McpConnectionManager(settings=settings, mcp_server_store=store)
    manager._connection_manager = host
    manager._runtime_manager._connection_manager = host
    return host, settings


@pytest.mark.asyncio
async def test_unconfirmed_native_stdio_reconnect_returns_validation_error(app_and_store, caplog):
    """Ordinary first-use request: the real guard rejects it before any spawn."""
    app, store = app_and_store
    host, settings = wire_native_host(app, store)
    caplog.set_level(logging.INFO, logger="src.api.routes.registry")
    command = settings.orchestration.mcp_stdio_command_allowlist[0]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/v1/registry/mcp/managed", json={
                "name": "first-use validation", "transport": "stdio", "command": command,
            })
            assert created.status_code == 200, created.text
            key = created.json()["key"]
            response = await client.post(f"/api/v1/registry/mcp/managed/{key}/reconnect")
            assert host.get_state(key).status == "failed"
            assert not host._transports and not host._sessions
            assert response.status_code == 400, response.text
            assert "first-use confirmation" in response.json()["detail"]
            assert any(record.getMessage() == "mcp_management_input_invalid" and record.operation == "reconnect"
                       for record in caplog.records)
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_confirmed_native_stdio_reconnect_discovers_and_calls_real_tool(app_and_store, tmp_path):
    """A temporary local MCP SDK server; no mocked connection or external service."""
    app, store = app_and_store
    host, _ = wire_native_host(app, store)
    script = tmp_path / "normal_mcp_server.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('normal-local-fixture')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        "server.run(transport='stdio')\n", encoding="utf-8",
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/v1/registry/mcp/managed", json={
                "name": "normal local server", "transport": "stdio",
                "command": f'"{sys.executable}"', "args": [f'"{script}"'],
            })
            assert created.status_code == 200, created.text
            path = f"/api/v1/registry/mcp/managed/{created.json()['key']}"
            confirmed = await client.post(f"{path}/confirm-stdio")
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] != "connected"
            reconnected = await client.post(f"{path}/reconnect")
            assert reconnected.status_code == 200, reconnected.text
            assert reconnected.json()["status"] == "connected"
            tools = await client.get(f"{path}/tools")
            assert tools.status_code == 200
            assert [item["name"] for item in tools.json()["tools"]] == ["echo"]
            result = await client.post(f"{path}/tools/echo/call", json={"arguments": {"text": "normal request"}})
            assert result.status_code == 200, result.text
            assert result.json()["ok"] is True
            assert "normal request" in str(result.json()["result"])
    finally:
        await host.shutdown()

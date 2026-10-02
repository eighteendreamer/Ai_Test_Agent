from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.exploration.ui_graph_store import UIGraphStore
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.core.config import get_settings
from src.modes.security_testing_mode.runtime import SecurityTestingModeRuntime
from src.modes.security_testing_mode.security_graph_store import SecurityGraphStore


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_RECORDING_MEMGRAPH") != "1" or not os.getenv("MEMGRAPH_CLEANUP_TEST_PORT"),
    reason="set RUN_LIVE_RECORDING_MEMGRAPH=1 and MEMGRAPH_CLEANUP_TEST_PORT for an isolated instance",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [
    "tool_runtime", "tool_security_graph", "security_runtime", "knowledge", "recording", "ui_graph",
])
async def test_normal_owner_shutdown_releases_real_memgraph_connection(owner):
    """Read-only query against an explicitly selected isolated Memgraph instance."""
    port = int(os.environ["MEMGRAPH_CLEANUP_TEST_PORT"])
    settings = get_settings().model_copy(deep=True)
    settings.database.memgraph_host = "127.0.0.1"
    settings.database.memgraph_port = port
    settings.database.memgraph_user = ""
    settings.database.memgraph_password = ""
    settings.security.security_graph_memory_enabled = owner in {"tool_security_graph", "security_runtime"}
    if owner in {"tool_runtime", "tool_security_graph"}:
        service = ToolRuntimeService(settings=settings)
        provider = (
            service._ui_graph_store._provider if owner == "tool_runtime"
            else service._security_testing_mode_runtime._security_graph_store._provider
        )
    elif owner == "security_runtime":
        service = SecurityTestingModeRuntime(settings=settings)
        provider = service._security_graph_store._provider
    else:
        factory = {
            "knowledge": KnowledgeGraphService,
            "recording": RecordingGraphStore,
            "ui_graph": UIGraphStore,
        }[owner]
        service = factory(settings)
        provider = service._provider

    def connection_count():
        return sum(
            connection.status == psutil.CONN_ESTABLISHED
            and bool(connection.raddr) and connection.raddr.port == port
            for connection in psutil.Process().net_connections(kind="tcp")
        )

    try:
        assert await asyncio.to_thread(provider.execute, "RETURN 1 AS value") == [{"value": 1}]
        assert connection_count() == 1
        await service.close()
        for _ in range(20):
            if connection_count() == 0:
                break
            await asyncio.sleep(0.05)
        assert connection_count() == 0, f"{owner} retained its Bolt connection after close"
        assert provider._driver is None
        await service.close()
        assert connection_count() == 0
    finally:
        # Also release resources when running this regression on the old implementation.
        await asyncio.to_thread(provider.close)


@pytest.mark.asyncio
async def test_tool_runtime_shutdown_releases_owned_callback_listener():
    service = ToolRuntimeService()
    broker = service._security_testing_mode_runtime._callback_broker
    lease = await broker.lease(
        campaign_id="local-cleanup-test", target="http://127.0.0.1",
        approval_scope_hash="local-cleanup-test-scope",
    )
    try:
        await service.close()
        observed = broker.get(lease.lease_id)
        assert observed.status == "released"
        assert observed.cleanup_complete
        assert lease.lease_id not in broker._servers
        await service.close()
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_security_shutdown_preserves_borrowed_resources():
    broker = SimpleNamespace(close=AsyncMock())
    graph_store = SimpleNamespace(close=AsyncMock())
    service = SecurityTestingModeRuntime(callback_broker=broker, security_graph_store=graph_store)
    await service.close()
    broker.close.assert_not_awaited()
    graph_store.close.assert_not_awaited()

    provider = SimpleNamespace(close=Mock())
    store = SecurityGraphStore(object(), provider=provider)
    await store.close()
    provider.close.assert_not_called()

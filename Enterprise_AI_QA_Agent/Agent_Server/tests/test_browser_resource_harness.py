from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.runtime.resource_lease_manager import ResourceUnavailable


class Registry:
    def get(self, key):
        return SimpleNamespace(enabled=True, capabilities=["browser-automation"], name="browser")


class Manager:
    def __init__(self, fail=False): self.fail = fail; self.acquired = []; self.released = []
    async def acquire(self, **kwargs):
        if self.fail: raise ResourceUnavailable("global:all")
        self.acquired.append(kwargs)
        return SimpleNamespace(resource_id=kwargs["resource_id"], fencing_token=9)
    async def release(self, lease): self.released.append(lease); return True


def service(manager):
    value = MCPRuntimeService.__new__(MCPRuntimeService)
    value._mcp_registry = Registry()
    value._resource_lease_manager = manager
    value._settings = SimpleNamespace(orchestration=SimpleNamespace(resource_default_lease_seconds=30))
    value._run_browser_automation = lambda payload, context: asyncio.sleep(0, result={"status": "success"})
    return value


def test_browser_capability_claims_session_lease_and_releases():
    async def run():
        manager = Manager(); runtime = service(manager)
        result = await runtime.call("browser-mcp", "browser-automation", {}, {
            "session_id": "s1", "turn_id": "t1", "context_bundle": {
                "project_id": "p1", "test_run_id": "r1", "run_item_id": "i1", "attempt_id": "a1",
            },
        })
        assert result["status"] == "success"
        assert manager.acquired[0]["resource_id"] == "browser:s1"
        assert manager.acquired[0]["project_id"] == "p1"
        assert len(manager.released) == 1
    asyncio.run(run())


def test_browser_capacity_is_reported_without_running_tool():
    async def run():
        manager = Manager(fail=True); runtime = service(manager)
        result = await runtime.call("browser-mcp", "browser-automation", {}, {"session_id": "s1"})
        assert result == {"status": "waiting_resource", "waiting_reason": "global:all"}
    asyncio.run(run())

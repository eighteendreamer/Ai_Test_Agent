from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.core.request_context import get_request_context
from src.runtime.resource_lease_manager import ResourceUnavailable


def service():
    value = MCPRuntimeService.__new__(MCPRuntimeService)
    value._mcp_registry = SimpleNamespace(get=lambda key: SimpleNamespace(
        enabled=True, capabilities=["browser-automation"], name="browser"))
    return value


def test_browser_capability_propagates_owner_context_to_lifetime_manager():
    async def run():
        runtime = service()
        observed = []

        async def execute(payload, context):
            observed.append(get_request_context())
            return {"status": "success"}

        runtime._run_browser_automation = execute
        result = await runtime.call("browser-mcp", "browser-automation", {}, {
            "session_id": "s1", "turn_id": "t1", "context_bundle": {
                "project_id": "p1", "test_run_id": "r1", "run_item_id": "i1", "attempt_id": "a1",
            },
        })
        assert result["status"] == "success"
        owner = observed[0]
        assert (owner.project_id, owner.session_id, owner.turn_id, owner.run_id, owner.run_item_id, owner.attempt_id) == (
            "p1", "s1", "t1", "r1", "i1", "a1")
    asyncio.run(run())


def test_browser_capacity_is_reported_as_waiting_resource():
    async def run():
        runtime = service()

        async def unavailable(payload, context):
            raise ResourceUnavailable("global:all")

        runtime._run_browser_automation = unavailable
        result = await runtime.call("browser-mcp", "browser-automation", {}, {"session_id": "s1"})
        assert result == {"status": "waiting_resource", "waiting_reason": "global:all"}
    asyncio.run(run())


def test_memory_resource_projection_rejects_old_and_terminal_registrations():
    from src.runtime.session_resource_store import InMemorySessionResourceStore
    from src.schemas.session_resource import SessionResourceRecord, SessionResourceKind, SessionResourceStatus

    async def run():
        store = InMemorySessionResourceStore()
        old = SessionResourceRecord(session_id="s1", kind=SessionResourceKind.browser_session,
            resource_key="browser-scope", metadata={"allocation_id": "old", "allocation_generation": 1})
        current = old.model_copy(update={"metadata": {"allocation_id": "new", "allocation_generation": 2}})
        await store.save(old)
        await store.save(current)
        assert (await store.save(old)).metadata["allocation_id"] == "new"
        await store.mark_status(current.id, SessionResourceStatus.released)
        assert (await store.save(current)).status == SessionResourceStatus.released
        assert not await store.list("s1", active_only=True)
    asyncio.run(run())

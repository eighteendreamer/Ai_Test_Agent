from __future__ import annotations

import asyncio
import multiprocessing
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
import psutil
from psycopg import sql

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.runtime.python_playwright_cli import PythonPlaywrightCliRuntime
from src.core.config import get_settings
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.postgres_browser_allocation_store import PostgresBrowserAllocationStore
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_cleanup_worker import ResourceCleanupTaskHandler
from src.runtime.browser_resource_cleanup import BrowserResourceCleanup
from src.runtime.session_resource_store import PostgresSessionResourceStore
from src.application.resources.session_resource_service import SessionResourceService

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_BROWSER_TESTS") != "1",
                              reason="requires real Redis/PostgreSQL and Chromium")


def configured_settings(prefix):
    settings = get_settings().model_copy(deep=True)
    settings.orchestration.browser_headless = True
    settings.orchestration.browser_default_name = "chromium"
    settings.orchestration.resource_default_lease_seconds = 2
    settings.orchestration.redis_task_outbox_streams = prefix + ":cleanup"
    return settings


def make_service(settings, manager, root):
    registry = SimpleNamespace(get=lambda key: SimpleNamespace(
        enabled=True, capabilities=["browser-control"], name="browser"))
    service = MCPRuntimeService(registry, settings)
    service._artifact_root = root
    service.set_resource_lease_manager(manager)
    return service


def context(attempt="a1"):
    return {"session_id": "s-live-browser", "turn_id": "t-live", "context_bundle": {
        "project_id": "p-live-browser", "test_run_id": "r-live-browser",
        "run_item_id": "i-live-browser", "attempt_id": attempt,
    }}


async def command(service, args=None, attempt="a1"):
    return await service.call("browser", "browser-control", {
        "action": "command", "args": args or ["eval", "1"], "raw": True,
    }, context(attempt))


async def wait_until(predicate, timeout=10):
    async def wait():
        while not predicate():
            await asyncio.sleep(.05)
    await asyncio.wait_for(wait(), timeout)


class Harness:
    def __init__(self, settings, prefix, root):
        self.settings, self.prefix, self.root = settings, prefix, root
        self.manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix,
                                                cleanup_stream=prefix + ":cleanup")
        self.allocations = PostgresBrowserAllocationStore(settings)
        self.receipts = PostgresResourceCleanupStore(settings)
        self.cleanup = BrowserResourceCleanup(settings, allocations=self.allocations, namespace=prefix)
        self.services = []
        self.queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.browser_cleanup_task_stream,
                                    group="browser-test-cleanup")

    def service(self):
        value = make_service(self.settings, self.manager, self.root / str(len(self.services)))
        self.services.append(value)
        return value

    async def initialize(self):
        await self.manager.connect()
        await self.manager.configure_quota(scope="resource_type", identifier="browser", limit=1)
        await self.allocations.initialize()
        await self.receipts.initialize()
        await PostgresSessionResourceStore(self.settings).initialize()
        await self.queue.connect()

    async def cleanup_all(self):
        await self.manager.reap_expired()
        worker = RedisTaskWorker(self.queue, consumer="browser-test", block_ms=0, batch_size=100,
            handler=ResourceCleanupTaskHandler(self.receipts, {"browser": self.cleanup}, manager=self.manager))
        await worker.run_once()
        assert not await self.queue.pending()

    async def close(self):
        for service in self.services:
            await service.close()
        await self.cleanup_all()
        assert not await self.manager.usage_snapshot()
        await self.queue.close()
        with postgres_connect(self.settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE lease_namespace = %s RETURNING allocation_id").format(
                sql.Identifier(self.settings.database.postgres_browser_allocation_table)), (self.prefix,))
            ids = [row["allocation_id"] for row in cur.fetchall()]
            cur.execute(sql.SQL("DELETE FROM {} WHERE record->>'allocation_id' = ANY(%s)").format(
                sql.Identifier(self.settings.database.postgres_resource_cleanup_table)), (ids,))
        keys = [key async for key in self.manager._client.scan_iter(match=self.prefix + ":*")]
        if keys:
            await self.manager._client.delete(*keys)
        await self.manager.close()


@pytest_asyncio.fixture
async def harness(tmp_path):
    prefix = "qa:test:browser:" + uuid4().hex
    value = Harness(configured_settings(prefix), prefix, tmp_path)
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_persistent_browser_keeps_redis_capacity_between_commands(harness):
    service = harness.service()
    assert (await command(service))["status"] == "success"
    assert len(service._playwright_cli._sessions) == 1
    assert (await harness.manager.usage_snapshot()).get("resource_type:browser") == 1
    assert (await command(service, ["eval", "2"]))["stdout"].strip() == "2"
    blocked = await command(service, attempt="a2")
    assert blocked == {"status": "waiting_resource", "waiting_reason": "resource_type:browser"}
    assert len(service._playwright_cli._sessions) == 1
    await command(service, ["close"])
    assert (await harness.manager.usage_snapshot())["resource_type:browser"] == 1
    await harness.cleanup_all()
    assert not await harness.manager.usage_snapshot()
    assert (await command(service, attempt="a2"))["status"] == "success"


@pytest.mark.asyncio
async def test_idle_browser_renews_lease_and_remote_api_can_close(harness):
    first, other = harness.service(), harness.service()
    assert (await command(first))["status"] == "success"
    scope = first._playwright_session_name(context(), "browser-control-command")
    browser = first._playwright_cli._sessions[scope].browser
    await asyncio.sleep(2.5)
    assert (await harness.manager.list_active())[0]["ttl_ms"] > 0
    assert (await command(other))["status"] == "waiting_resource"
    await other.close_browser_session(scope)
    await wait_until(lambda: not browser.is_connected())
    await harness.cleanup_all()
    assert not await harness.manager.usage_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["lease_lost", "lifetime"])
async def test_active_command_stops_when_ownership_or_lifetime_ends(harness, reason):
    if reason == "lifetime":
        harness.settings.orchestration.resource_browser_max_lifetime_seconds = 2
    service = harness.service()
    await command(service)
    session = next(iter(service._playwright_cli._sessions.values()))
    entered = asyncio.Event()
    await session.page.expose_function("entered", entered.set)
    running = asyncio.create_task(command(service, ["eval", "new Promise(() => { window.entered(); })"]))
    await asyncio.wait_for(entered.wait(), 5)
    if reason == "lease_lost":
        await harness.manager._client.delete(harness.manager._lease_key("browser", session.allocation.lease.resource_id))
    result = await asyncio.wait_for(running, 10)
    assert result["status"] == "waiting_resource"
    assert result["waiting_reason"] == ("browser_lease_lost" if reason == "lease_lost" else "browser_lifetime_expired")
    assert not session.browser.is_connected()


@pytest.mark.asyncio
async def test_startup_binding_failure_closes_real_browser_and_defers_quota(harness, monkeypatch):
    service = harness.service()
    started = []
    from playwright.async_api import Browser
    original = Browser.new_context

    async def capture(browser, **kwargs):
        started.append(browser)
        return await original(browser, **kwargs)

    async def fail_binding(*args):
        raise RuntimeError("injected durable process binding failure")

    monkeypatch.setattr(Browser, "new_context", capture)
    monkeypatch.setattr(service.browser_resources._allocations, "bind", fail_binding)
    with pytest.raises(RuntimeError, match="injected durable process binding failure"):
        await command(service)
    assert started and not started[0].is_connected()
    assert (await harness.manager.usage_snapshot())["resource_type:browser"] == 1


@pytest.mark.asyncio
async def test_external_cleanup_kills_only_owned_browser(harness):
    service = harness.service()
    await command(service)
    session = next(iter(service._playwright_cli._sessions.values()))
    # Simulate loss of the owner supervisor while its browser remains running.
    session.lease_watch.cancel()
    await session.lease_watch
    unrelated = PythonPlaywrightCliRuntime(harness.settings)
    try:
        assert (await unrelated.run("unrelated", ["eval", "1"], harness.root))["exit_code"] == 0
        other_browser = unrelated._sessions["unrelated"].browser
        await harness.manager.retire(session.allocation.lease)
        assert session.browser.is_connected()
        await harness.cleanup_all()
        assert not session.browser.is_connected()
        assert other_browser.is_connected()
        assert not await harness.manager.usage_snapshot()
    finally:
        await unrelated.close()


def child_browser(prefix, root, ready, before_binding):
    from pathlib import Path

    async def run():
        settings = configured_settings(prefix)
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix,
                                            cleanup_stream=prefix + ":cleanup")
        await manager.connect()
        service = make_service(settings, manager, Path(root))
        if before_binding:
            async def wait_before_binding(*args):
                ready.set()
                await asyncio.Event().wait()
            service.browser_resources._allocations.bind = wait_before_binding
        assert (await command(service))["status"] == "success"
        ready.set()
        await asyncio.Event().wait()
    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("before_binding", [False, True])
async def test_killed_worker_recovered_from_durable_browser_intent(harness, before_binding):
    spawn = multiprocessing.get_context("spawn")
    ready = spawn.Event()
    child = spawn.Process(target=child_browser, args=(harness.prefix, str(harness.root), ready, before_binding))
    try:
        child.start()
        assert await asyncio.to_thread(ready.wait, 20)
        owned_processes = psutil.Process(child.pid).children(recursive=True)
        assert (await harness.manager.usage_snapshot())["resource_type:browser"] == 1
        child.kill()
        await asyncio.to_thread(child.join, 10)
        assert not child.is_alive()
        await asyncio.sleep(2.2)
        await harness.cleanup_all()
        assert not await harness.manager.usage_snapshot()
        await wait_until(lambda: all(not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
                                    for process in owned_processes))
        with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT processes FROM {} WHERE lease_namespace = %s").format(
                sql.Identifier(harness.settings.database.postgres_browser_allocation_table)), (harness.prefix,))
            row = cur.fetchone()
            assert bool(row["processes"]) is not before_binding
    finally:
        if child.is_alive():
            child.kill()
            await asyncio.to_thread(child.join, 10)
        child.close()


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_capacity_until_retry_commits(harness, monkeypatch):
    service = harness.service()
    await command(service)
    await command(service, ["close"])
    original = harness.cleanup._cleanup_sync

    def fail_cleanup(allocation):
        raise RuntimeError("injected process cleanup failure")

    monkeypatch.setattr(harness.cleanup, "_cleanup_sync", fail_cleanup)
    await harness.cleanup_all()
    assert (await harness.manager.usage_snapshot())["resource_type:browser"] == 1
    with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT status FROM {} WHERE record->>'session_id' = %s AND record->>'allocation_id' IN "
                            "(SELECT allocation_id FROM {} WHERE lease_namespace = %s)").format(
            sql.Identifier(harness.settings.database.postgres_resource_cleanup_table),
            sql.Identifier(harness.settings.database.postgres_browser_allocation_table)),
            (context()["session_id"], harness.prefix))
        assert cur.fetchone()["status"] == "failed"
    monkeypatch.setattr(harness.cleanup, "_cleanup_sync", original)
    await asyncio.sleep(1.1)
    await harness.cleanup_all()
    assert not await harness.manager.usage_snapshot()


@pytest.mark.asyncio
async def test_cleanup_receipt_updates_only_matching_session_allocation(harness):
    store = PostgresSessionResourceStore(harness.settings)
    service = harness.service()
    service._session_resource_service = SessionResourceService(store)
    # Unique session ID ensures this test never touches a real Session resource.
    owner = {**context(), "session_id": "s-browser-projection-" + uuid4().hex}
    try:
        await service.call("browser", "browser-control", {"action": "command", "args": ["eval", "1"]}, owner)
        active = await store.list(owner["session_id"], active_only=True)
        assert len(active) == 1
        assert active[0].metadata["allocation_id"]
        await service.close()
        await harness.cleanup_all()
        assert not await store.list(owner["session_id"], active_only=True)
        assert (await store.list(owner["session_id"]))[0].status.value == "released"
    finally:
        with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE session_id = %s").format(
                sql.Identifier(harness.settings.database.postgres_session_resource_table)), (owner["session_id"],))


@pytest.mark.asyncio
async def test_application_lifespan_wires_native_browser_harness():
    import httpx
    from src.main import app

    async with app.router.lifespan_context(app):
        mcp = app.state.mcp_runtime_service
        explorer = app.state.tool_runtime_service._ui_exploration_service
        assert mcp.browser_resources is not None
        assert explorer._playwright_runtime._resource_manager is mcp.browser_resources
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/v1/health")
            assert response.status_code == 200
            postgres_ok = response.json().get("postgres_ok")
            assert postgres_ok is True
    assert mcp._playwright_cli._closed
    assert explorer._playwright_runtime._closed


@pytest.mark.asyncio
async def test_inventory_recovers_browser_when_redis_ownership_is_lost(harness):
    service = harness.service()
    await command(service)
    session = next(iter(service._playwright_cli._sessions.values()))
    assert await harness.cleanup.reconcile(harness.manager) == 0
    session.lease_watch.cancel()
    await session.lease_watch
    await harness.manager._client.delete(
        harness.manager._lease_key("browser", session.allocation.lease.resource_id),
        harness.manager._key("lease:registry"), harness.manager._key("lease:metadata"),
        harness.manager._key("quota:usage"),
    )
    assert await harness.cleanup.reconcile(harness.manager) > 0
    await harness.cleanup_all()
    assert not session.browser.is_connected()


@pytest.mark.asyncio
async def test_old_session_cleanup_does_not_close_replacement_browser(harness):
    store = PostgresSessionResourceStore(harness.settings)
    service = harness.service()
    resources = SessionResourceService(store, browser_cleanup=service.close_browser_session)
    service._session_resource_service = resources
    owner = {**context(), "session_id": "s-browser-fence-" + uuid4().hex}
    payload = {"action": "command", "args": ["eval", "1"]}
    try:
        await service.call("browser", "browser-control", payload, owner)
        old = (await resources.list_active(owner["session_id"]))[0]
        await service.close_browser_session(old.resource_key)
        await harness.cleanup_all()
        await service.call("browser", "browser-control", payload, owner)
        replacement = next(iter(service._playwright_cli._sessions.values()))
        assert replacement.allocation.allocation_id != old.metadata["allocation_id"]
        stale_registration = await store.save(old)
        assert stale_registration.metadata["allocation_id"] == replacement.allocation.allocation_id
        await resources._cleanup_one(old, "delayed_old_cleanup")
        assert replacement.browser.is_connected()
        current = await resources.list_active(owner["session_id"])
        assert len(current) == 1
        assert current[0].metadata["allocation_id"] == replacement.allocation.allocation_id
        await service.close_browser_session(old.resource_key)
        await harness.cleanup_all()
        late_registration = await store.save(current[0])
        assert late_registration.status.value == "released"
    finally:
        await service.close()
        await harness.cleanup_all()
        with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE session_id = %s").format(
                sql.Identifier(harness.settings.database.postgres_session_resource_table)), (owner["session_id"],))


@pytest.mark.asyncio
async def test_other_host_close_waits_for_owner_queue_receipt(harness, monkeypatch):
    service = harness.service()
    await command(service)
    scope = service._playwright_session_name(context(), "browser-control-command")
    remote_settings = harness.settings.model_copy(deep=True)
    remote_settings.orchestration.resource_browser_host_id = "different-test-host"
    remote = make_service(remote_settings, harness.manager, harness.root / "remote")
    retired = asyncio.Event()
    original = harness.manager.retire

    async def retire(lease):
        result = await original(lease)
        retired.set()
        return result

    monkeypatch.setattr(harness.manager, "retire", retire)
    closing = asyncio.create_task(remote.close_browser_session(scope))
    try:
        await asyncio.wait_for(retired.wait(), 5)
        assert not closing.done()
        assert await harness.manager._client.xlen(remote_settings.orchestration.browser_cleanup_task_stream) == 0
        assert await harness.manager._client.xlen(harness.settings.orchestration.browser_cleanup_task_stream) > 0
        await harness.cleanup_all()
        await asyncio.wait_for(closing, 5)
    finally:
        if not closing.done():
            closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)
        await remote.close()

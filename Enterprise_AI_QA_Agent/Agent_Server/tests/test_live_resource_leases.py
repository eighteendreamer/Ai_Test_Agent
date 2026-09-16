from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from src.core.config import get_settings
from src.runtime.resource_lease_manager import RedisResourceLeaseManager, ResourceUnavailable


@pytest_asyncio.fixture
async def manager():
    manager = RedisResourceLeaseManager(get_settings().database.redis_url)
    await manager.connect()
    suffix = uuid4().hex
    resource_id = f"browser-{suffix}"
    await manager.configure_quota(scope="global", identifier="all", limit=1)
    await manager.configure_quota(scope="resource_type", identifier="browser", limit=1)
    try:
        yield manager, resource_id
    finally:
        await manager._client.delete(manager._lease_key("browser", resource_id))
        await manager._client.hdel("qa:quota:limits", "global:all", "resource_type:browser")
        await manager._client.hdel("qa:quota:usage", "global:all", "resource_type:browser")
        await manager.close()


@pytest.mark.asyncio
async def test_only_one_concurrent_worker_claims_resource(manager):
    lease_manager, resource_id = manager
    async def claim(worker):
        try:
            return await lease_manager.acquire(resource_type="browser", resource_id=resource_id,
                                               worker_id=worker, lease_seconds=10, project_id="p1", run_id="r1")
        except ResourceUnavailable as exc:
            return exc.reason
    results = await asyncio.gather(*(claim(f"worker-{i}") for i in range(12)))
    leases = [result for result in results if not isinstance(result, str)]
    assert len(leases) == 1 and sum(result == "resource_busy" for result in results) == 11
    assert await lease_manager.release(leases[0])


@pytest.mark.asyncio
async def test_fencing_and_owner_release_are_monotonic(manager):
    lease_manager, resource_id = manager
    first = await lease_manager.acquire(resource_type="docker", resource_id=resource_id,
                                        worker_id="w1", lease_seconds=10)
    assert not await lease_manager.release(first.__class__(**{**first.__dict__, "lease_token": "wrong"}))
    assert await lease_manager.release(first)
    second = await lease_manager.acquire(resource_type="docker", resource_id=resource_id,
                                         worker_id="w2", lease_seconds=10)
    assert second.fencing_token > first.fencing_token
    assert await lease_manager.release(second)


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(manager):
    lease_manager, resource_id = manager
    first = await lease_manager.acquire(resource_type="browser", resource_id=resource_id,
                                        worker_id="w1", lease_seconds=1)
    await asyncio.sleep(1.1)
    second = await lease_manager.acquire(resource_type="browser", resource_id=resource_id,
                                         worker_id="w2", lease_seconds=10)
    assert second.fencing_token > first.fencing_token
    assert await lease_manager.release(second)


@pytest.mark.asyncio
async def test_renew_extends_live_resource_ttl_and_rejects_stale_token(manager):
    lease_manager, resource_id = manager
    lease = await lease_manager.acquire(
        resource_type="browser",
        resource_id=resource_id,
        worker_id="w1",
        lease_seconds=1,
    )
    assert await lease_manager.renew(lease, lease_seconds=3)
    assert await lease_manager._client.pttl(
        lease_manager._lease_key("browser", resource_id)
    ) > 1500

    stale = lease.__class__(**{**lease.__dict__, "lease_token": "stale-token"})
    assert not await lease_manager.renew(stale, lease_seconds=3)
    assert await lease_manager.release(lease)

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.runtime.resource_lease_manager import RedisResourceLeaseManager, ResourceUnavailable


class FakeRedis:
    def __init__(self): self.calls = []
    async def ping(self): return True
    async def hset(self, *args): self.calls.append(("hset", args))
    async def eval(self, *args):
        self.calls.append(("eval", args))
        if "EXISTS" in args[0]:
            return [7, "ok"]
        return 1
    async def get(self, key): return None


def test_claim_release_contract_carries_all_scope_identifiers():
    async def run():
        redis = FakeRedis()
        manager = RedisResourceLeaseManager("unused", client=redis)
        await manager.configure_quota(scope="global", identifier="all", limit=20)
        lease = await manager.acquire(
            resource_type="browser", resource_id="browser-1", worker_id="worker-1",
            lease_seconds=30, project_id="project-1", session_id="session-1",
            run_id="run-1", run_item_id="item-1", attempt_id="attempt-1",
        )
        assert lease.fencing_token == 7 and lease.resource_id == "browser-1"
        assert lease.lease_expire_at > datetime.now(timezone.utc)
        assert await manager.renew(lease, lease_seconds=30)
        assert await manager.release(lease)
        claim = next(call for call in redis.calls if call[0] == "eval")
        assert "project:project-1" in claim[1]
        assert "run:run-1" in claim[1]
    asyncio.run(run())


def test_invalid_resource_parts_are_rejected():
    async def run():
        manager = RedisResourceLeaseManager("unused", client=FakeRedis())
        with pytest.raises(ValueError):
            await manager.acquire(resource_type="browser", resource_id="bad/key", worker_id="w", lease_seconds=1)
    asyncio.run(run())

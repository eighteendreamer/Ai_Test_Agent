from __future__ import annotations

import asyncio
import json
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
        assert claim[1][1] == 7
        assert "qa:tasks:cleanup" in claim[1]
    asyncio.run(run())


def test_reap_decodes_complete_expired_lease_contract():
    class ReapRedis(FakeRedis):
        async def eval(self, *args):
            payload = {
                "resource_id": "docker-slot-0",
                "resource_type": "docker",
                "external_resource_id": "container-real-id",
                "project_id": "project-1",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "run_id": "run-1",
                "run_item_id": "item-1",
                "attempt_id": "attempt-1",
                "worker_id": "worker-1",
                "lease_token": "lease-1",
                "fencing_token": 7,
                "request_id": "request-1",
                "trace_id": "trace-1",
            }
            return [json.dumps(payload)]

    async def run():
        manager = RedisResourceLeaseManager("unused", client=ReapRedis())
        expired = await manager.reap_expired()
        assert len(expired) == 1
        assert expired[0].resource_id == "docker-slot-0"
        assert expired[0].external_resource_id == "container-real-id"
        assert expired[0].fencing_token == 7

    asyncio.run(run())


def test_invalid_resource_parts_are_rejected():
    async def run():
        manager = RedisResourceLeaseManager("unused", client=FakeRedis())
        with pytest.raises(ValueError):
            await manager.acquire(resource_type="browser", resource_id="bad/key", worker_id="w", lease_seconds=1)
    asyncio.run(run())

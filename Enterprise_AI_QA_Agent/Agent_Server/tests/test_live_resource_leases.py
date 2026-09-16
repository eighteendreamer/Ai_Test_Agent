from __future__ import annotations

import asyncio
import os
import json
import shutil
import subprocess
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import sql
from redis.exceptions import ResponseError

from src.core.config import get_settings
from src.cli.run_resource_cleanup_worker import build_cleanup_callbacks
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_cleanup_worker import (
    ResourceCleanupTaskHandler,
    ResourceCleanupWorker,
)
from src.runtime.resource_lease_manager import RedisResourceLeaseManager, ResourceUnavailable
from tests.test_resource_cleanup_worker import ReceiptStore


@pytest_asyncio.fixture
async def manager():
    suffix = uuid4().hex
    cleanup_stream = f"qa:test:cleanup:{suffix}"
    manager = RedisResourceLeaseManager(
        get_settings().database.redis_url,
        cleanup_stream=cleanup_stream,
        key_prefix=f"qa:test:lease:{suffix}",
    )
    await manager.connect()
    resource_id = f"browser-{suffix}"
    await manager.configure_quota(scope="global", identifier="all", limit=1)
    await manager.configure_quota(scope="resource_type", identifier="browser", limit=1)
    try:
        yield manager, resource_id
    finally:
        await _clear_test_namespace(manager, cleanup_stream)
        await manager.close()


async def _clear_test_namespace(manager, stream):
    assert manager._prefix.startswith("qa:test:lease:")
    keys = [key async for key in manager._client.scan_iter(match=manager._key("*"))]
    await manager._client.delete(stream, f"{stream}:dead_letter", *keys)


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


async def _expire(manager, lease):
    # Deterministic fault injection: reproduce Redis TTL removal and a due registry entry.
    key = manager._lease_key(lease.resource_type, lease.resource_id)
    await manager._client.delete(key)
    await manager._client.zadd(manager._key("lease:registry"), {key: 0})


@pytest.mark.asyncio
async def test_reap_publish_failure_preserves_binding_and_quota(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="worker-1", lease_seconds=10)
    assert await m.bind(lease, "a" * 64)
    await _expire(m, lease)
    client = m._client
    await client.set(m._cleanup_stream, "wrong-type")
    before = await m.usage_snapshot()
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await m.reap_expired()
    key = m._lease_key("docker", resource_id)
    assert await client.hget(m._key("lease:metadata"), key)
    assert await m.usage_snapshot() == before
    await client.delete(m._cleanup_stream)
    expired = await m.reap_expired()
    assert expired[0].external_resource_id == "a" * 64
    assert expired[0].fencing_token == lease.fencing_token
    assert not await m.reap_expired()
    assert await client.xlen(m._cleanup_stream) == 1
    assert not await m.usage_snapshot()


@pytest.mark.asyncio
async def test_claim_reap_preserves_old_binding_and_per_lease_fencing(manager):
    m, resource_id = manager
    first = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="old", lease_seconds=10)
    await m.bind(first, "a" * 64)
    await _expire(m, first)
    second = await m.acquire(resource_type="docker", resource_id=resource_id,
                             worker_id="new", lease_seconds=10)
    messages = await m._client.xrange(m._cleanup_stream)
    payload = json.loads(messages[0][1]["payload"])
    assert payload["external_resource_id"] == "a" * 64
    assert payload["fencing_token"] == first.fencing_token
    assert not await m.bind(first, "b" * 64)
    assert (await m.list_active())[0]["fencing_token"] == second.fencing_token
    assert await m.release(second)


@pytest.mark.asyncio
async def test_quota_reconciliation_counts_active_and_unreaped_owners(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="browser", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10, project_id="p1")
    await m._client.hset(m._key("quota:usage"), mapping={"global:all": 99, "project:orphan": 9})
    await m.reconcile_quota_usage()
    usage = await m.usage_snapshot()
    assert usage["global:all"] == 1 and "project:orphan" not in usage
    await _expire(m, lease)
    await m.reconcile_quota_usage()
    assert (await m.usage_snapshot())["global:all"] == 1
    await m.reap_expired()
    await m.reconcile_quota_usage()
    assert not await m.usage_snapshot()


@pytest.mark.asyncio
async def test_cleanup_pending_recovery_retry_and_dead_letter(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="creator", lease_seconds=10)
    await m.bind(lease, "a" * 64)
    await _expire(m, lease)
    await m.reap_expired()
    queue = RedisTaskQueue(get_settings().database.redis_url,
                           stream=m._cleanup_stream, group="cleanup-test")
    await queue.connect()
    try:
        # Delivery disappears with its consumer before ACK; another worker must reclaim it.
        assert len(await queue.consume(consumer="crashed", block_ms=0)) == 1
        store = ReceiptStore()
        async def unavailable(_lease):
            raise ConnectionError("Docker daemon unavailable")
        worker = RedisTaskWorker(queue, consumer="replacement", block_ms=0,
            handler=ResourceCleanupTaskHandler(store, {"docker": unavailable}),
            reclaim_idle_ms=10, retry_base_ms=1, retry_max_ms=10, max_retries=1)
        await asyncio.sleep(.03)
        await worker.run_once()
        for _ in range(10):
            await asyncio.sleep(.03)
            await worker.run_once()
            if await queue._client.xlen(f"{queue.stream}:dead_letter"):
                break
        assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1
        assert not await queue.pending()
        assert store.statuses == ["failed", "failed"]
    finally:
        await queue.close()


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1",
    reason="set RUN_LIVE_DOCKER_TESTS=1 to exercise real Redis and Docker cleanup",
)
@pytest.mark.asyncio
async def test_expired_docker_slot_removes_bound_real_container() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    settings = get_settings()
    suffix = uuid4().hex[:12]
    container_name = f"qa-cleanup-{suffix}"
    cleanup_stream = f"qa:test:cleanup:{suffix}"
    group = f"qa:test:cleanup-workers:{suffix}"
    container_id = ""
    task_id = None
    store = PostgresResourceCleanupStore(settings)
    lease_manager = RedisResourceLeaseManager(
        settings.database.redis_url,
        cleanup_stream=cleanup_stream,
        key_prefix=f"qa:test:lease:{suffix}",
    )
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=cleanup_stream,
        group=group,
    )
    try:
        await store.initialize()
        created = subprocess.run(
            [
                "docker", "run", "--detach", "--name", container_name,
                "--label", "enterprise-ai-qa-agent=managed",
                "busybox", "sleep", "300",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=60,
        )
        container_id = created.stdout.strip()
        assert container_id

        await lease_manager.connect()
        await queue.connect()
        lease = await lease_manager.acquire(
            resource_type="docker",
            resource_id=f"docker-slot-{suffix}",
            worker_id=f"worker-{suffix}",
            lease_seconds=1,
            project_id=f"project-{suffix}",
            session_id=f"session-{suffix}",
            run_id=f"run-{suffix}",
            run_item_id=f"item-{suffix}",
            attempt_id=f"attempt-{suffix}",
        )
        assert await lease_manager.bind(lease, container_id)
        task_id = f"task_cleanup_{lease.lease_token}"
        await asyncio.sleep(1.2)

        reaper = ResourceCleanupWorker(lease_manager, interval_seconds=1)
        assert await reaper.run_once() == 1
        delivery = RedisTaskWorker(
            queue,
            consumer=f"cleanup-{suffix}",
            handler=ResourceCleanupTaskHandler(store, build_cleanup_callbacks(settings)),
            block_ms=0,
            reclaim_idle_ms=1000,
        )
        assert await delivery.run_once() == 1
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT status FROM {} WHERE task_id = %s").format(
                sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_id,))
            assert cur.fetchone()["status"] == "completed"

        inspected = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        assert inspected.returncode != 0
        container_id = ""
        usage = await lease_manager.usage_snapshot()
        assert not any(suffix in key for key in usage)
    finally:
        if container_id:
            subprocess.run(
                ["docker", "rm", "--force", container_id],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
        if lease_manager._client is not None:
            await _clear_test_namespace(lease_manager, cleanup_stream)
        await queue.close()
        await lease_manager.close()
        if task_id:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE task_id = %s").format(
                    sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_id,))

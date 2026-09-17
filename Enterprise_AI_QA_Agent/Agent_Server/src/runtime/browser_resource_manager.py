"""Native browser ownership from pre-launch intent to external cleanup receipt."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from uuid import uuid4

from src.core.request_context import get_request_context
from src.runtime.browser_resource_cleanup import ALLOCATION_ENV, HOST_ENV, NAMESPACE_ENV, BrowserResourceCleanup, browser_processes, process_records
from src.runtime.postgres_browser_allocation_store import PostgresBrowserAllocationStore
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from src.runtime.resource_lease_manager import ExpiredResourceLease, RedisResourceLeaseManager, ResourceLease, ResourceUnavailable

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserAllocation:
    allocation_id: str
    generation: int
    lease: ResourceLease
    environment: dict[str, str]


class BrowserResourceManager:
    def __init__(self, settings, *, lease_manager=None, allocations=None):
        self._settings = settings
        self._manager = lease_manager or RedisResourceLeaseManager(settings.database.redis_url,
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            cleanup_stream=settings.orchestration.browser_cleanup_task_stream)
        self._owns_manager = lease_manager is None
        self._allocations = allocations or PostgresBrowserAllocationStore(settings)
        self._worker_id = f"browser-{os.getpid()}-{uuid4().hex[:12]}"

    async def acquire(self, session_name, *, recording_id=None):
        config = self._settings.orchestration
        context = get_request_context()
        fields = {key: getattr(context, key, None) for key in (
            "project_id", "session_id", "run_id", "run_item_id", "attempt_id")}
        if not fields["session_id"]:
            raise ResourceUnavailable("browser_session_context_required")
        lease = None
        try:
            await self._manager.connect()
            lease = await self._manager.acquire(resource_type="browser", resource_id=f"browser:{session_name}",
                worker_id=self._worker_id, lease_seconds=config.resource_default_lease_seconds,
                cleanup_stream=config.browser_cleanup_task_stream, **fields)
            allocation_id = uuid4().hex
            generation = await self._allocations.prepare(allocation_id, lease, namespace=self._manager.namespace,
                host_id=config.resource_browser_host_id, session_name=session_name, recording_id=recording_id)
            if not await self._manager.prepare_allocation(lease, allocation_id):
                raise RuntimeError("Browser lease lost before allocation")
            if not await self._manager.bind(lease, allocation_id):
                raise RuntimeError("Browser lease lost before startup")
            LOGGER.info("browser_allocation_prepared", extra={**fields, "allocation_id": allocation_id,
                "worker_id": lease.worker_id, "resource_id": lease.resource_id, "fencing_token": lease.fencing_token})
            return BrowserAllocation(allocation_id, generation, lease, {
                ALLOCATION_ENV: allocation_id, NAMESPACE_ENV: self._manager.namespace, HOST_ENV: config.resource_browser_host_id,
            })
        except BaseException as exc:
            if lease is not None:
                await self.retire(lease)
            if isinstance(exc, (asyncio.CancelledError, ResourceUnavailable)):
                raise
            LOGGER.error("browser_resource_acquire_failed", extra={**fields, "error_type": type(exc).__name__})
            raise ResourceUnavailable("resource_harness_unavailable") from exc

    async def started(self, allocation):
        processes = await asyncio.to_thread(browser_processes, namespace=self._manager.namespace,
            host_id=self._settings.orchestration.resource_browser_host_id)
        records = process_records([(marker, process) for marker, process in processes if marker == allocation.allocation_id])
        if not records:
            raise RuntimeError("Browser started without an inspectable allocation marker")
        await self._allocations.bind(allocation.allocation_id, records)
        await self.validate(allocation)

    async def validate(self, allocation):
        try:
            if not await self._manager.renew(allocation.lease,
                    lease_seconds=self._settings.orchestration.resource_default_lease_seconds):
                raise ResourceUnavailable("browser_lease_lost")
        except ResourceUnavailable:
            raise
        except Exception as exc:
            raise ResourceUnavailable("resource_harness_unavailable") from exc

    async def watch(self, allocation):
        config = self._settings.orchestration
        deadline = asyncio.get_running_loop().time() + config.resource_browser_max_lifetime_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ResourceUnavailable("browser_lifetime_expired")
            await asyncio.sleep(min(config.resource_default_lease_seconds / 3, remaining))
            await self.validate(allocation)

    async def retire(self, lease):
        try:
            if not await self._manager.retire(lease):
                LOGGER.warning("browser_resource_retire_rejected", extra={"resource_id": lease.resource_id})
        except Exception as exc:
            LOGGER.error("browser_resource_retire_deferred", extra={"resource_id": lease.resource_id,
                "error_type": type(exc).__name__})

    async def request_close(self, session_name, *, allocation_id=None):
        """Revoke the durable owner even when this API process has no local handle."""
        if allocation_id:
            rows = await self._allocations.get_many([allocation_id], namespace=self._manager.namespace)
            allocation = rows.get(allocation_id)
        else:
            allocation = await self._allocations.get_for_scope(session_name, namespace=self._manager.namespace)
        if allocation is None:
            return
        if allocation["session_name"] != session_name:
            raise ValueError("Browser scope does not match allocation")
        await self._manager.connect()
        lease = ExpiredResourceLease.from_payload(allocation["lease_record"])
        if not await self._manager.retire(lease):
            LOGGER.info("browser_remote_close_already_retired", extra={"resource_id": lease.resource_id})
        if allocation["host_id"] == self._settings.orchestration.resource_browser_host_id:
            await BrowserResourceCleanup(self._settings, allocations=self._allocations,
                                         namespace=self._manager.namespace)(lease)
            return
        receipts = PostgresResourceCleanupStore(self._settings)
        deadline = asyncio.get_running_loop().time() + self._settings.orchestration.resource_browser_cleanup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await receipts.completed(f"task_cleanup_{lease.lease_token}", allocation_id=allocation["allocation_id"]):
                return
            await asyncio.sleep(.25)
        raise ResourceUnavailable("browser_cleanup_pending_on_owner_host")

    async def close(self):
        if self._owns_manager:
            await self._manager.close()

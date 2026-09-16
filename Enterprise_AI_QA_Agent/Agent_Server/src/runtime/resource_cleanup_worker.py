"""Reaps expired resource leases and invokes type-specific external cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from src.infrastructure.redis_task_queue import QueuedTask
from src.core.request_context import get_request_context
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from redis.exceptions import RedisError
from src.runtime.resource_lease_manager import (
    ExpiredResourceLease,
    RedisResourceLeaseManager,
)

LOGGER = logging.getLogger(__name__)
CleanupCallback = Callable[[ExpiredResourceLease], Awaitable[None]]


class ResourceCleanupWorker:
    def __init__(
        self,
        manager: RedisResourceLeaseManager,
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Resource cleanup interval must be positive")
        self._manager = manager
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self, *, limit: int = 100) -> int:
        expired = await self._manager.reap_expired(limit=limit)
        await self._manager.reconcile_quota_usage()
        for lease in expired:
            LOGGER.info("resource_cleanup_task_enqueued", extra={
                "resource_type": lease.resource_type,
                "resource_id": lease.resource_id,
                "external_resource_id": lease.external_resource_id,
                "run_id": lease.run_id,
                "run_item_id": lease.run_item_id,
                "attempt_id": lease.attempt_id,
            })
        return len(expired)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except RedisError as exc:
                LOGGER.warning("resource_cleanup_redis_unavailable", extra={
                    "error_type": type(exc).__name__,
                })
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass


class ResourceCleanupTaskHandler:
    """Execute external cleanup from an at-least-once Redis delivery."""

    def __init__(self, store: PostgresResourceCleanupStore,
                 callbacks: dict[str, CleanupCallback] | None = None) -> None:
        self._store = store
        self._callbacks = callbacks or {}

    async def __call__(self, task: QueuedTask) -> None:
        payload = task.payload
        task_type = str(payload.get("task_type") or "")
        if task_type != "cleanup_task":
            raise ValueError(
                f"Cleanup stream message {task.message_id} has task_type={task_type!r}"
            )
        lease = ExpiredResourceLease.from_payload(payload)
        task_id = str(payload.get("task_id") or f"task_cleanup_{lease.lease_token}")
        context = get_request_context()
        worker_id = str(context.worker_id if context else "cleanup")
        if not await self._store.begin(task_id, lease, worker_id=worker_id):
            return
        try:
            if not lease.external_resource_id:
                await self._store.finish(task_id, worker_id=worker_id, status="skipped")
                LOGGER.info("resource_cleanup_skipped", extra={
                    "task_id": task_id, "resource_id": lease.resource_id,
                    "reason": "no_external_resource_bound",
                })
                return
            callback = self._callbacks.get(lease.resource_type)
            if callback is None:
                raise NotImplementedError(f"No cleanup adapter for {lease.resource_type}")
            await callback(lease)
            await self._store.finish(task_id, worker_id=worker_id, status="completed")
        except Exception as exc:
            try:
                await self._store.finish(task_id, worker_id=worker_id, status="failed",
                                         error_type=type(exc).__name__)
            except Exception:
                LOGGER.exception("resource_cleanup_failure_record_failed", extra={"task_id": task_id})
            LOGGER.exception("resource_lease_external_cleanup_failed", extra={
                "task_id": payload.get("task_id"),
                "resource_type": lease.resource_type,
                "resource_id": lease.resource_id,
                "external_resource_id": lease.external_resource_id,
                "error_type": type(exc).__name__,
            })
            raise
        LOGGER.info("resource_lease_external_cleanup_completed", extra={
            "task_id": payload.get("task_id"),
            "resource_type": lease.resource_type,
            "resource_id": lease.resource_id,
            "external_resource_id": lease.external_resource_id,
        })

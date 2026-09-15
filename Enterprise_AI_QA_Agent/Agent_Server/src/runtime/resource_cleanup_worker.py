"""Reaps expired resource leases and invokes type-specific external cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from src.runtime.resource_lease_manager import RedisResourceLeaseManager

LOGGER = logging.getLogger(__name__)
CleanupCallback = Callable[[str, str], Awaitable[None]]


class ResourceCleanupWorker:
    def __init__(
        self,
        manager: RedisResourceLeaseManager,
        *,
        interval_seconds: float = 30.0,
        callbacks: dict[str, CleanupCallback] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Resource cleanup interval must be positive")
        self._manager = manager
        self._interval_seconds = interval_seconds
        self._callbacks = callbacks or {}
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self, *, limit: int = 100) -> int:
        expired = await self._manager.reap_expired(limit=limit)
        for resource_type, resource_id in expired:
            callback = self._callbacks.get(resource_type)
            if callback is None:
                LOGGER.info("resource_lease_reaped", extra={
                    "resource_type": resource_type, "resource_id": resource_id,
                    "cleanup": "no_callback",
                })
                continue
            try:
                await callback(resource_type, resource_id)
                LOGGER.info("resource_lease_external_cleanup_completed", extra={
                    "resource_type": resource_type, "resource_id": resource_id,
                })
            except Exception as exc:
                # Lease ownership is already gone; leave an auditable failure and
                # let the external cleanup callback's own retry policy handle it.
                LOGGER.exception("resource_lease_external_cleanup_failed", extra={
                    "resource_type": resource_type, "resource_id": resource_id,
                    "error_type": type(exc).__name__,
                })
        return len(expired)

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass

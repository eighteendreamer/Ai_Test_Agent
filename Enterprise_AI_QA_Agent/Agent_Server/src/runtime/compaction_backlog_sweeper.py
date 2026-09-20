"""Background sweep that queues compaction for Sessions whose backlog stopped draining.

No upstream counterpart (checked: src-wide grep for an existing idle-session sweep,
``ResourceCleanupWorker`` is the closest precedent and only reaps leases): a Session
that stopped receiving events has no turn-completion left to trigger compaction, so
its unfilled watermark would stay forever.

Idleness is used instead of a "Session switched" event on purpose: switching is a
frontend navigation, and the server has no such concept to hook.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


class CompactionBacklogSweeper:
    def __init__(
        self,
        store: Any,
        *,
        idle_seconds: int,
        interval_seconds: float,
        batch_size: int = 50,
    ) -> None:
        if idle_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("Compaction sweep timing must be positive")
        self._store = store
        self._idle_seconds = int(idle_seconds)
        self._interval_seconds = float(interval_seconds)
        self._batch_size = int(batch_size)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> int:
        queued = await self._store.queue_idle_compaction_tasks(
            idle_seconds=self._idle_seconds, limit=self._batch_size
        )
        if queued:
            LOGGER.info(
                "compaction_backlog_queued",
                extra={"queued_tasks": queued, "idle_seconds": self._idle_seconds},
            )
        return int(queued)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                # A dead PostgreSQL must not kill the process: the backlog stays
                # queryable, so the next pass retries the same candidates.
                LOGGER.warning(
                    "compaction_backlog_sweep_failed",
                    extra={"error_type": type(exc).__name__},
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

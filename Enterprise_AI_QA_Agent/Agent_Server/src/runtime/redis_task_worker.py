"""Generic Redis Streams worker for Harness task execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue

LOGGER = logging.getLogger(__name__)
TaskHandler = Callable[[QueuedTask], Awaitable[None]]


class RedisTaskWorker:
    """Consume tasks and ACK only after the handler completes successfully."""

    def __init__(
        self,
        queue: RedisTaskQueue,
        *,
        consumer: str,
        handler: TaskHandler,
        batch_size: int = 1,
        block_ms: int = 1000,
    ) -> None:
        self._queue = queue
        self._consumer = consumer
        self._handler = handler
        self._batch_size = max(1, batch_size)
        self._block_ms = max(0, block_ms)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> int:
        tasks = await self._queue.consume(
            consumer=self._consumer,
            count=self._batch_size,
            block_ms=self._block_ms,
        )
        processed = 0
        for task in tasks:
            try:
                await self._handler(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "redis_task_handler_failed",
                    extra={"task_id": task.payload.get("task_id"), "message_id": task.message_id},
                )
                continue
            await self._queue.ack(task.message_id)
            processed += 1
        return processed

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()

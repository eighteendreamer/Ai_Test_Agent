"""Generic Redis Streams worker for Harness task execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.core.request_context import reset_request_context, set_request_context
from src.runtime.task_deferred import TaskDeferred

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
        max_retries: int = 3,
    ) -> None:
        self._queue = queue
        self._consumer = consumer
        self._handler = handler
        self._batch_size = max(1, batch_size)
        self._block_ms = max(0, block_ms)
        self._max_retries = max(0, max_retries)
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
            payload = task.payload
            context_token = set_request_context(
                request_id=str(payload.get("request_id") or ""),
                trace_id=str(payload.get("trace_id") or ""),
                session_id=payload.get("session_id"),
                turn_id=payload.get("turn_id"),
                run_id=payload.get("run_id"),
                run_item_id=payload.get("run_item_id"),
                attempt_id=payload.get("attempt_id"),
                worker_id=self._consumer,
                resource_id=payload.get("resource_id"),
            )
            try:
                LOGGER.info(
                    "redis_task_started",
                    extra={
                        "request_id": payload.get("request_id"),
                        "trace_id": payload.get("trace_id"),
                        "task_id": payload.get("task_id"),
                        "message_id": task.message_id,
                        "worker_id": self._consumer,
                    },
                )
                await self._handler(task)
            except asyncio.CancelledError:
                raise
            except TaskDeferred:
                LOGGER.info(
                    "redis_task_deferred",
                    extra={"task_id": payload.get("task_id"), "message_id": task.message_id},
                )
                continue
            except Exception as exc:
                LOGGER.exception(
                    "redis_task_handler_failed",
                    extra={"task_id": task.payload.get("task_id"), "message_id": task.message_id},
                )
                retry_count = int(payload.get("retry_count") or 0)
                if hasattr(self._queue, "ack") and hasattr(self._queue, "enqueue"):
                    await self._queue.ack(task.message_id)
                    if retry_count < self._max_retries:
                        await self._queue.enqueue({**payload, "retry_count": retry_count + 1})
                        LOGGER.warning("redis_task_requeued", extra={"task_id": payload.get("task_id"), "retry_count": retry_count + 1})
                    elif hasattr(self._queue, "dead_letter"):
                        await self._queue.dead_letter(payload, reason=type(exc).__name__)
                        LOGGER.error("redis_task_dead_lettered", extra={"task_id": payload.get("task_id"), "retry_count": retry_count})
                continue
            else:
                await self._queue.ack(task.message_id)
                processed += 1
            finally:
                reset_request_context(context_token)
        return processed

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()

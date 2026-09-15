"""Generic Redis Streams worker for Harness task execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from redis.exceptions import RedisError

from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.core.request_context import reset_request_context, set_request_context
from src.runtime.task_deferred import TaskDeferred

LOGGER = logging.getLogger(__name__)
TaskHandler = Callable[[QueuedTask], Awaitable[None]]


class DeliveryOwnershipLost(RuntimeError):
    pass


class RedisTaskWorker:
    """At-least-once delivery: ACK committed results or an atomic successor.

    Handlers must commit PostgreSQL facts before returning and enforce business
    idempotency/fencing there. Stream ownership is not a substitute for that.
    """

    def __init__(
        self,
        queue: RedisTaskQueue,
        *,
        consumer: str,
        handler: TaskHandler,
        batch_size: int = 1,
        block_ms: int = 1000,
        max_retries: int = 3,
        reclaim_idle_ms: int = 120000,
        retry_base_ms: int = 1000,
        retry_max_ms: int = 60000,
        task_timeout_seconds: float = 900,
    ) -> None:
        self._queue = queue
        self._consumer = consumer
        self._handler = handler
        self._batch_size = max(1, batch_size)
        self._block_ms = max(0, block_ms)
        self._max_retries = max(0, max_retries)
        if reclaim_idle_ms <= 0 or retry_base_ms <= 0 or retry_max_ms < retry_base_ms or task_timeout_seconds <= 0:
            raise ValueError("Invalid Redis worker timing configuration")
        self._reclaim_idle_ms = reclaim_idle_ms
        self._retry_base_ms = retry_base_ms
        self._retry_max_ms = retry_max_ms
        self._task_timeout_seconds = task_timeout_seconds
        self._stop = asyncio.Event()

    @classmethod
    def from_settings(cls, queue, *, settings, consumer: str, handler: TaskHandler):
        config = settings.orchestration
        return cls(
            queue, consumer=consumer, handler=handler,
            block_ms=config.redis_task_block_ms,
            max_retries=config.redis_task_max_retries,
            reclaim_idle_ms=config.redis_task_reclaim_idle_ms,
            retry_base_ms=config.redis_task_retry_base_ms,
            retry_max_ms=config.redis_task_retry_max_ms,
            task_timeout_seconds=config.redis_task_timeout_seconds,
        )

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> int:
        if self._stop.is_set():
            return 0
        tasks = await self._queue.reclaim(
            consumer=self._consumer, min_idle_ms=self._reclaim_idle_ms,
            count=self._batch_size,
        )
        tasks += await self._queue.consume(
            consumer=self._consumer,
            count=self._batch_size,
            block_ms=0 if tasks else self._block_ms,
        )
        processed = 0
        for task in tasks:
            if self._stop.is_set():
                break  # Unstarted deliveries stay pending for recovery.
            if not await self._queue.ready(task, consumer=self._consumer):
                continue
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
                await self._run_handler(task)
            except asyncio.CancelledError:
                raise
            except DeliveryOwnershipLost as exc:
                LOGGER.warning("redis_task_ownership_uncertain", extra={
                    "task_id": payload.get("task_id"), "message_id": task.message_id,
                    "worker_id": self._consumer, "error_type": type(exc).__name__,
                })
                # Never ACK/requeue when heartbeat/ownership cannot be confirmed.
                continue
            except TaskDeferred:
                LOGGER.info(
                    "redis_task_deferred",
                    extra={"task_id": payload.get("task_id"), "message_id": task.message_id},
                )
                continue
            except Exception as exc:
                LOGGER.error(
                    "redis_task_handler_failed",
                    extra={"task_id": task.payload.get("task_id"), "message_id": task.message_id,
                           "error_type": type(exc).__name__, "retry_count": task.retry_count},
                )
                exhausted = task.retry_count >= self._max_retries
                delay = min(self._retry_max_ms, self._retry_base_ms * 2 ** min(task.retry_count, 30))
                successor = await self._queue.transfer(
                    task, consumer=self._consumer,
                    retry_count=task.retry_count if exhausted else task.retry_count + 1,
                    delay_ms=0 if exhausted else delay,
                    dead_letter_reason=type(exc).__name__ if exhausted else None,
                )
                LOGGER.warning("redis_task_delivery_transferred" if successor else "redis_task_transfer_owner_lost", extra={
                    "task_id": payload.get("task_id"), "message_id": task.message_id,
                    "successor_id": successor, "dead_letter": exhausted,
                })
                continue
            else:
                processed += int(bool(await self._queue.ack_owned(task, consumer=self._consumer)))
            finally:
                reset_request_context(context_token)
        return processed

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except RedisError as exc:
                LOGGER.warning("redis_worker_transport_unavailable", extra={
                    "worker_id": self._consumer, "error_type": type(exc).__name__,
                })
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._retry_base_ms / 1000)
                except asyncio.TimeoutError:
                    pass

    async def _run_handler(self, task: QueuedTask) -> None:
        heartbeat_stop = asyncio.Event()
        async def heartbeat():
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=self._reclaim_idle_ms / 3000)
                except asyncio.TimeoutError:
                    pass
                if heartbeat_stop.is_set():
                    return
                if not await self._queue.touch(task, consumer=self._consumer):
                    raise DeliveryOwnershipLost("Pending delivery belongs to another consumer")

        execution = asyncio.ensure_future(self._handler(task))
        renewal = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait(
                {execution, renewal}, timeout=self._task_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError("Redis task execution deadline exceeded")
            if renewal in done:
                try:
                    await renewal
                except RedisError as exc:
                    raise DeliveryOwnershipLost("Cannot confirm pending ownership heartbeat") from exc
            await execution
        finally:
            # Stop renewal cooperatively: cancelling a Redis command mid-response
            # can leave its client cleanup waiting. Transport waits are bounded.
            heartbeat_stop.set()
            execution.cancel()
            await asyncio.gather(execution, renewal, return_exceptions=True)

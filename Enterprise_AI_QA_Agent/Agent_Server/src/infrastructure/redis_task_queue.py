"""Redis Streams task queue used by Harness workers.

The queue is deliberately infrastructure-only: PostgreSQL remains the source
of truth for task state, while Redis provides delivery, consumer groups and
recovery of unacknowledged messages.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.application.observability.business_metrics import (
    redis_errors_total,
    task_claimed_total,
    task_dead_letter_total,
    task_reclaimed_total,
)
from src.infrastructure.redis_task_scripts import CONTROL, QUARANTINE, TRANSFER

LOGGER = logging.getLogger(__name__)


class InvalidTaskMessage(ValueError):
    """A deterministic wire-format error, carrying no private payload text."""


@dataclass(frozen=True)
class QueuedTask:
    message_id: str
    payload: dict[str, Any]
    retry_count: int = 0
    not_before_ms: int = 0


class RedisTaskQueue:
    def __init__(
        self,
        redis_url: str,
        *,
        stream: str,
        group: str,
        client: Redis | Any | None = None,
        socket_timeout_seconds: float = 5,
    ) -> None:
        self.stream = stream
        self.group = group
        self._redis_url = redis_url
        self._client = client
        if socket_timeout_seconds <= 0:
            raise ValueError("Redis task socket timeout must be positive")
        self._socket_timeout_seconds = socket_timeout_seconds
        self._reclaim_cursor = "0-0"

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(
                self._redis_url, decode_responses=True,
                socket_timeout=self._socket_timeout_seconds,
                socket_connect_timeout=self._socket_timeout_seconds,
            )
        try:
            await self._client.xgroup_create(
                name=self.stream,
                groupname=self.group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            # BUSYGROUP means the durable group already exists. Other errors
            # must remain visible to the caller; silently running without the
            # queue would violate the task delivery contract.
            if "BUSYGROUP" not in str(exc):
                redis_errors_total().labels(operation="connect").inc()
                raise

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()

    async def enqueue(self, payload: dict[str, Any], *, maxlen: int | None = None) -> str:
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        if maxlen is not None:
            raise ValueError("Task streams must not use MAXLEN: it can delete pending deliveries")
        fields = {"payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
        try:
            message_id = await self._client.xadd(self.stream, fields)
        except RedisError:
            redis_errors_total().labels(operation="enqueue").inc()
            raise
        return str(message_id)

    async def transfer(
        self, task: QueuedTask, *, consumer: str, retry_count: int,
        delay_ms: int = 0, dead_letter_reason: str | None = None,
    ) -> str | None:
        """Publish successor then ACK original, only while consumer owns it."""
        destination = f"{self.stream}:dead_letter" if dead_letter_reason else self.stream
        try:
            message_id = await self._client.eval(
                TRANSFER, 2, self.stream, destination,
                self.group, consumer, task.message_id,
                json.dumps(task.payload, ensure_ascii=False, separators=(",", ":")),
                max(0, retry_count), max(0, delay_ms), dead_letter_reason or "",
            )
        except RedisError:
            redis_errors_total().labels(operation="transfer").inc()
            raise
        if not message_id:
            return None
        if dead_letter_reason:
            task_dead_letter_total().labels(stream=self.stream).inc()
        else:
            task_reclaimed_total().labels(stream=self.stream).inc()
        return str(message_id)

    async def ready(self, task: QueuedTask, *, consumer: str) -> bool:
        return bool(await self._control(task, consumer, "ready"))

    async def touch(self, task: QueuedTask, *, consumer: str) -> bool:
        return bool(await self._control(task, consumer, "touch"))

    async def ack_owned(self, task: QueuedTask, *, consumer: str) -> int:
        return int(await self._control(task, consumer, "ack") or 0)

    async def _control(self, task: QueuedTask, consumer: str, action: str):
        try:
            return await self._client.eval(
                CONTROL, 1, self.stream, self.group, consumer, task.message_id,
                action, task.not_before_ms,
            )
        except RedisError:
            redis_errors_total().labels(operation=f"control_{action}").inc()
            raise

    async def dead_letter(self, payload: dict[str, Any], *, reason: str) -> str:
        """Persist a failed envelope in a separate stream for operator recovery."""
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        stream = f"{self.stream}:dead_letter"
        body = {**payload, "dead_letter_reason": reason}
        try:
            message_id = await self._client.xadd(
                stream,
                {"payload": json.dumps(body, ensure_ascii=False, separators=(",", ":"))},
            )
        except RedisError:
            redis_errors_total().labels(operation="dead_letter").inc()
            raise
        task_dead_letter_total().labels(stream=self.stream).inc()
        return str(message_id)

    async def consume(
        self,
        *,
        consumer: str,
        count: int = 1,
        block_ms: int = 1000,
        stream_id: str = ">",
    ) -> list[QueuedTask]:
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        try:
            rows = await self._client.xreadgroup(
                groupname=self.group,
                consumername=consumer,
                streams={self.stream: stream_id},
                count=max(1, count),
                block=block_ms if block_ms > 0 else None,
            )
        except RedisError:
            redis_errors_total().labels(operation="consume").inc()
            raise
        tasks = await self._decode_deliveries(rows, consumer=consumer)
        if tasks:
            task_claimed_total().labels(stream=self.stream).inc(len(tasks))
        return tasks

    async def ack(self, message_id: str) -> int:
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        return int(await self._client.xack(self.stream, self.group, message_id))

    async def pending(self, *, count: int = 100) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        rows = await self._client.xpending_range(
            self.stream,
            self.group,
            min="-",
            max="+",
            count=max(1, count),
        )
        return [dict(row) for row in rows]

    async def reclaim(
        self,
        *,
        consumer: str,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[QueuedTask]:
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        try:
            rows = await self._client.xautoclaim(
                self.stream,
                self.group,
                consumer,
                min_idle_time=max(1, min_idle_ms),
                start_id=self._reclaim_cursor,
                count=max(1, count),
            )
        except RedisError:
            redis_errors_total().labels(operation="reclaim").inc()
            raise
        if isinstance(rows, (list, tuple)) and rows and rows[0]:
            self._reclaim_cursor = str(rows[0])
        messages = rows[1] if isinstance(rows, (list, tuple)) and len(rows) > 1 else []
        tasks = await self._decode_deliveries([(self.stream, messages)], consumer=consumer)
        if tasks:
            task_reclaimed_total().labels(stream=self.stream).inc(len(tasks))
        return tasks

    async def _decode_deliveries(self, rows: Any, *, consumer: str) -> list[QueuedTask]:
        decoded: list[QueuedTask] = []
        for _stream_name, messages in rows or []:
            for message_id, fields in messages or []:
                try:
                    task = self._decode_message(message_id, fields)
                except InvalidTaskMessage as exc:
                    await self._quarantine(str(message_id), consumer=consumer, reason=str(exc))
                else:
                    decoded.append(task)
        return decoded

    async def _quarantine(self, message_id: str, *, consumer: str, reason: str) -> str | None:
        context = {"stream": self.stream, "group": self.group, "worker_id": consumer,
                   "message_id": message_id, "failure_type": reason}
        try:
            successor = await self._client.eval(
                QUARANTINE, 2, self.stream, f"{self.stream}:dead_letter",
                self.group, consumer, message_id, reason,
            )
        except RedisError:
            redis_errors_total().labels(operation="quarantine").inc()
            LOGGER.warning("redis_task_quarantine_failed", extra=context)
            raise  # Keep the original pending when the archive cannot be committed.
        if not successor:
            LOGGER.warning("redis_task_quarantine_owner_lost", extra=context)
            return None
        task_dead_letter_total().labels(stream=self.stream).inc()
        LOGGER.warning("redis_task_message_quarantined", extra={**context, "successor_id": str(successor)})
        return str(successor)

    @staticmethod
    def _decode_rows(rows: Any) -> list[QueuedTask]:
        return [RedisTaskQueue._decode_message(message_id, fields)
                for _stream_name, messages in rows or [] for message_id, fields in messages or []]

    @staticmethod
    def _decode_message(message_id: Any, fields: Any) -> QueuedTask:
        raw = fields.get("payload") if isinstance(fields, dict) else None
        if raw is None:
            raise InvalidTaskMessage("missing_payload")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, RecursionError) as exc:
            raise InvalidTaskMessage("invalid_json") from exc
        if not isinstance(payload, dict):
            raise InvalidTaskMessage("payload_not_object")
        # Retry state belongs to delivery, not the strict business envelope.
        # Drain messages produced by the previous payload-based retry writer.
        payload = dict(payload)
        legacy_count = payload.pop("retry_count", 0)
        try:
            retry_count = max(0, int(fields.get("retry_count", legacy_count)))
            not_before_ms = max(0, int(fields.get("not_before_ms", 0)))
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidTaskMessage("invalid_delivery_metadata") from exc
        return QueuedTask(str(message_id), payload, retry_count, not_before_ms)

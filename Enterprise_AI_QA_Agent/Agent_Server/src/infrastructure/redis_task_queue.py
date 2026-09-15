"""Redis Streams task queue used by Harness workers.

The queue is deliberately infrastructure-only: PostgreSQL remains the source
of truth for task state, while Redis provides delivery, consumer groups and
recovery of unacknowledged messages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis


@dataclass(frozen=True)
class QueuedTask:
    message_id: str
    payload: dict[str, Any]


class RedisTaskQueue:
    def __init__(
        self,
        redis_url: str,
        *,
        stream: str,
        group: str,
        client: Redis | Any | None = None,
    ) -> None:
        self.stream = stream
        self.group = group
        self._redis_url = redis_url
        self._client = client
        self._reclaim_cursor = "0-0"

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(self._redis_url, decode_responses=True)
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
                raise

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()

    async def enqueue(self, payload: dict[str, Any], *, maxlen: int | None = None) -> str:
        if self._client is None:
            raise RuntimeError("RedisTaskQueue is not connected")
        fields = {"payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
        message_id = await self._client.xadd(self.stream, fields, maxlen=maxlen, approximate=True)
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
        rows = await self._client.xreadgroup(
            groupname=self.group,
            consumername=consumer,
            streams={self.stream: stream_id},
            count=max(1, count),
            block=max(0, block_ms),
        )
        return self._decode_rows(rows)

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
        rows = await self._client.xautoclaim(
            self.stream,
            self.group,
            consumer,
            min_idle_time=max(1, min_idle_ms),
            start_id=self._reclaim_cursor,
            count=max(1, count),
        )
        if isinstance(rows, (list, tuple)) and rows and rows[0]:
            self._reclaim_cursor = str(rows[0])
        messages = rows[1] if isinstance(rows, (list, tuple)) and len(rows) > 1 else []
        return self._decode_rows([(self.stream, messages)])

    @staticmethod
    def _decode_rows(rows: Any) -> list[QueuedTask]:
        decoded: list[QueuedTask] = []
        for _stream_name, messages in rows or []:
            for message_id, fields in messages or []:
                raw = fields.get("payload") if isinstance(fields, dict) else None
                if raw is None:
                    raise ValueError(f"Redis task message {message_id} has no payload")
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(payload, dict):
                    raise ValueError(f"Redis task message {message_id} payload is not an object")
                decoded.append(QueuedTask(message_id=str(message_id), payload=payload))
        return decoded

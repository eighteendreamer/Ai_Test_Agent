from __future__ import annotations

import asyncio
import json
import re
import zlib
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

_TASK_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_TASK_TTL_SECONDS = 24 * 60 * 60
_WRITE_BATCH_SIZE = 100
_READ_BATCH_SIZE = 64


class RedisExportTaskStore:
    """Share short-lived export task state and compressed session rows via Redis."""

    def __init__(
        self,
        redis_url: str,
        *,
        client: Redis | Any | None = None,
        ttl_seconds: int = _TASK_TTL_SECONDS,
    ) -> None:
        self._redis_url = redis_url
        self._client = client
        self._ttl_seconds = max(1, int(ttl_seconds))

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(self._redis_url, decode_responses=False)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()

    async def create_task(self, task_id: str, *, total: int) -> None:
        meta_key = self._meta_key(task_id)
        created = await self._client.hsetnx(meta_key, "task_id", task_id)
        if not created:
            raise ValueError(f"Export task '{task_id}' already exists.")
        await self._client.hset(
            meta_key,
            mapping={"progress": 0, "total": max(0, int(total)), "status": "running", "error": ""},
        )
        await self._client.expire(meta_key, self._ttl_seconds)

    async def update_progress(self, task_id: str, progress: int) -> None:
        meta_key = self._meta_key(task_id)
        await self._client.hset(meta_key, mapping={"progress": max(0, int(progress))})
        await self._client.expire(meta_key, self._ttl_seconds)

    async def complete_task(self, task_id: str, sessions: list[dict[str, Any]]) -> None:
        meta_key = self._meta_key(task_id)
        result_key = self._result_key(task_id)
        existing = await self._client.hgetall(meta_key)
        if not existing:
            raise RuntimeError(f"Export task '{task_id}' expired before completion.")

        await self._client.delete(result_key)
        for offset in range(0, len(sessions), _WRITE_BATCH_SIZE):
            batch = sessions[offset : offset + _WRITE_BATCH_SIZE]
            encoded = await self._encode_sessions(batch)
            await self._client.rpush(result_key, *encoded)
            await self._client.expire(result_key, self._ttl_seconds)

        fields = self._decode_hash(existing)
        total = int(fields.get("total", 0))
        await self._client.hset(
            meta_key,
            mapping={
                "progress": total,
                "status": "done",
                "error": "",
                "session_count": len(sessions),
                "exported_at": datetime.now(UTC).isoformat(),
            },
        )
        await self._client.expire(meta_key, self._ttl_seconds)

    async def fail_task(self, task_id: str, *, message: str) -> None:
        meta_key = self._meta_key(task_id)
        await self._client.delete(self._result_key(task_id))
        if not await self._client.hgetall(meta_key):
            return
        await self._client.hset(
            meta_key,
            mapping={"status": "error", "error": str(message)[:500]},
        )
        await self._client.expire(meta_key, self._ttl_seconds)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        raw = await self._client.hgetall(self._meta_key(task_id))
        if not raw:
            return None
        fields = self._decode_hash(raw)
        return {
            "task_id": fields.get("task_id", task_id),
            "progress": int(fields.get("progress", 0)),
            "total": int(fields.get("total", 0)),
            "status": fields.get("status", "error"),
            "error": fields.get("error") or None,
            "session_count": int(fields.get("session_count", 0)),
            "exported_at": fields.get("exported_at"),
        }

    async def result_count(self, task_id: str) -> int:
        return int(await self._client.llen(self._result_key(task_id)))

    async def read_result_batch(self, task_id: str, *, start: int, count: int) -> list[bytes]:
        if count <= 0:
            return []
        stop = start + min(count, _READ_BATCH_SIZE) - 1
        values = await self._client.lrange(self._result_key(task_id), start, stop)
        return [zlib.decompress(self._to_bytes(value)) for value in values]

    async def _encode_sessions(self, sessions: list[dict[str, Any]]) -> list[bytes]:
        def _encode() -> list[bytes]:
            return [
                zlib.compress(
                    json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                )
                for session in sessions
            ]

        return await asyncio.to_thread(_encode)

    def _meta_key(self, task_id: str) -> str:
        return f"qa-agent:settings-export:{self._normalize_task_id(task_id)}:meta"

    def _result_key(self, task_id: str) -> str:
        return f"qa-agent:settings-export:{self._normalize_task_id(task_id)}:sessions"

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        normalized = str(task_id).lower()
        if not _TASK_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Invalid export task ID.")
        return normalized

    @classmethod
    def _decode_hash(cls, value: dict[Any, Any]) -> dict[str, str]:
        return {
            cls._to_bytes(key).decode("utf-8"): cls._to_bytes(item).decode("utf-8")
            for key, item in value.items()
        }

    @staticmethod
    def _to_bytes(value: bytes | str) -> bytes:
        return value if isinstance(value, bytes) else str(value).encode("utf-8")

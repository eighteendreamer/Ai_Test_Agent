"""Redis-backed short-lived Session/Turn memory.

Redis is an acceleration and compaction staging area here; PostgreSQL remains
the durable source of truth.  Keys are session-scoped to prevent cross-session
memory or trajectory mixing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis


class RedisHotMemoryStore:
    def __init__(
        self,
        redis_url: str,
        *,
        ttl_seconds: int = 86400,
        max_events: int = 2000,
        client: Redis | Any | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._client = client
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._max_events = max(1, int(max_events))

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(self._redis_url, decode_responses=True)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()

    async def append_event(self, session_id: str, event: dict[str, Any]) -> str:
        self._require_session(session_id)
        event_id = str(event.get("id") or await self._client.incr(self._key(session_id, "event_seq")))
        body = {**event, "id": event_id, "hot_stored_at": datetime.now(timezone.utc).isoformat()}
        key = self._key(session_id, "events")
        await self._client.rpush(key, json.dumps(body, ensure_ascii=False, separators=(",", ":")))
        await self._client.ltrim(key, -self._max_events, -1)
        await self._client.expire(key, self._ttl_seconds)
        await self._client.expire(self._key(session_id, "event_seq"), self._ttl_seconds)
        return event_id

    async def list_events(self, session_id: str, *, after_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        self._require_session(session_id)
        rows = await self._client.lrange(self._key(session_id, "events"), 0, -1)
        events = [json.loads(row) for row in rows]
        if after_id:
            positions = [index for index, item in enumerate(events) if str(item.get("id")) == after_id]
            if not positions:
                return []
            events = events[positions[-1] + 1 :]
        return events[: max(1, int(limit))]

    async def put_turn_state(self, turn_id: str, state: dict[str, Any]) -> None:
        self._require_turn(turn_id)
        key = self._key(turn_id, "state", prefix="turn")
        await self._client.set(key, json.dumps(state, ensure_ascii=False, separators=(",", ":")), ex=self._ttl_seconds)

    async def get_turn_state(self, turn_id: str) -> dict[str, Any] | None:
        self._require_turn(turn_id)
        raw = await self._client.get(self._key(turn_id, "state", prefix="turn"))
        return json.loads(raw) if raw else None

    async def mark_compaction_pending(self, session_id: str, *, last_event_id: str | None = None) -> None:
        self._require_session(session_id)
        payload = {"status": "pending", "last_event_id": last_event_id}
        await self._client.set(
            self._key(session_id, "compaction"),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ex=self._ttl_seconds,
        )

    async def clear_session(self, session_id: str) -> None:
        self._require_session(session_id)
        await self._client.delete(
            self._key(session_id, "events"),
            self._key(session_id, "event_seq"),
            self._key(session_id, "compaction"),
        )

    async def acquire_compaction(self, session_id: str, key: str) -> bool:
        self._require_session(session_id)
        marker = self._key(session_id, f"compaction:{key}")
        acquired = await self._client.set(marker, "running", ex=self._ttl_seconds, nx=True)
        return bool(acquired)

    async def complete_compaction(self, session_id: str, key: str) -> None:
        self._require_session(session_id)
        marker = self._key(session_id, f"compaction:{key}")
        await self._client.set(marker, "completed", ex=self._ttl_seconds)

    @staticmethod
    def _require_session(session_id: str) -> None:
        if not str(session_id).strip():
            raise ValueError("session_id is required for hot memory isolation")

    @staticmethod
    def _require_turn(turn_id: str) -> None:
        if not str(turn_id).strip():
            raise ValueError("turn_id is required for hot memory isolation")

    @staticmethod
    def _key(identifier: str, suffix: str, *, prefix: str = "session") -> str:
        return f"qa:{prefix}:{identifier}:{suffix}"

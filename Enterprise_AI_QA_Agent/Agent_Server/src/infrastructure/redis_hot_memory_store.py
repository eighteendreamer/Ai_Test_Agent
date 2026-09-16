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


_COMMIT_COMPACTION_SCRIPT = """
local rows = redis.call('LRANGE', KEYS[1], 0, -1)
local target_index = -1
for index, row in ipairs(rows) do
  local ok, payload = pcall(cjson.decode, row)
  if ok and tostring(payload['id'] or '') == ARGV[1] then
    target_index = index - 1
    break
  end
end
if target_index < 0 then
  if redis.call('GET', KEYS[2]) == ARGV[2] then return 1 end
  return 0
end
redis.call('LTRIM', KEYS[1], target_index + 1, -1)
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SET', KEYS[3], ARGV[4], 'EX', ARGV[3])
return 1
"""

_ACQUIRE_COMPACTION_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  redis.call('SET', KEYS[1], 'running', 'EX', ARGV[1], 'NX')
  return 1
end
if current == 'running' or current == 'completed' then return 0 end
local ok, payload = pcall(cjson.decode, current)
if ok and type(payload) == 'table' and payload['status'] == 'failed' then
  redis.call('SET', KEYS[1], 'running', 'EX', ARGV[1])
  return 1
end
return 0
"""


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

    async def mark_compaction_pending(
        self,
        session_id: str,
        *,
        last_event_id: str | None = None,
        key: str | None = None,
    ) -> None:
        self._require_session(session_id)
        payload = {"status": "pending", "last_event_id": last_event_id, "key": key}
        await self._client.set(
            self._key(session_id, "compaction_state"),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ex=self._ttl_seconds,
        )

    async def get_compaction_state(self, session_id: str) -> dict[str, Any] | None:
        self._require_session(session_id)
        raw = await self._client.get(self._key(session_id, "compaction_state"))
        if not raw:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def commit_compaction(
        self,
        session_id: str,
        *,
        key: str,
        last_event_id: str,
    ) -> bool:
        """Commit durable compaction and retain events appended afterward.

        The Lua path makes the cursor update and list trim one Redis operation.
        A small command fallback keeps deterministic unit-test doubles useful;
        production Redis 8 always takes the atomic path.
        """
        self._require_session(session_id)
        if not last_event_id:
            raise ValueError("last_event_id is required for compaction commit")
        event_key = self._key(session_id, "events")
        state_key = self._key(session_id, "compaction_state")
        marker_key = self._key(session_id, f"compaction:{key}")
        state = json.dumps(
            {"status": "completed", "last_event_id": last_event_id, "key": key},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if hasattr(self._client, "eval"):
            result = await self._client.eval(
                _COMMIT_COMPACTION_SCRIPT,
                3,
                event_key,
                state_key,
                marker_key,
                last_event_id,
                state,
                self._ttl_seconds,
                "completed",
            )
            return bool(result)

        rows = await self._client.lrange(event_key, 0, -1)
        target_index = None
        for index, row in enumerate(rows):
            try:
                value = json.loads(row)
            except (TypeError, json.JSONDecodeError):
                continue
            if str(value.get("id") or "") == last_event_id:
                target_index = index
                break
        if target_index is None:
            return bool((await self.get_compaction_state(session_id) or {}).get("key") == key)
        await self._client.ltrim(event_key, target_index + 1, -1)
        await self._client.set(state_key, state, ex=self._ttl_seconds)
        await self._client.set(marker_key, "completed", ex=self._ttl_seconds)
        return True

    async def mark_compaction_failed(self, session_id: str, *, key: str, error: str) -> None:
        self._require_session(session_id)
        await self._client.set(
            self._key(session_id, f"compaction:{key}"),
            json.dumps(
                {"status": "failed", "error": str(error)[:1000]},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            ex=self._ttl_seconds,
        )

    async def clear_session(self, session_id: str) -> None:
        self._require_session(session_id)
        await self._client.delete(
            self._key(session_id, "events"),
            self._key(session_id, "event_seq"),
            self._key(session_id, "compaction"),
            self._key(session_id, "compaction_state"),
        )

    async def acquire_compaction(self, session_id: str, key: str) -> bool:
        self._require_session(session_id)
        marker = self._key(session_id, f"compaction:{key}")
        if hasattr(self._client, "eval"):
            return bool(
                await self._client.eval(
                    _ACQUIRE_COMPACTION_SCRIPT,
                    1,
                    marker,
                    self._ttl_seconds,
                )
            )
        existing = await self._client.get(marker)
        if existing:
            try:
                state = json.loads(existing)
            except (TypeError, json.JSONDecodeError):
                state = None
            if not isinstance(state, dict) or state.get("status") != "failed":
                return False
            await self._client.set(marker, "running", ex=self._ttl_seconds)
            return True
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

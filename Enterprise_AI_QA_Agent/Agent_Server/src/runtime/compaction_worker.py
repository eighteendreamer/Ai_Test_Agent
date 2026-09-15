"""Compacts Redis hot Session memory into durable PostgreSQL memory."""

from __future__ import annotations

import logging
from typing import Any

from src.infrastructure.redis_hot_memory_store import RedisHotMemoryStore

LOGGER = logging.getLogger(__name__)


class CompactionWorker:
    def __init__(
        self,
        *,
        hot_memory_store: RedisHotMemoryStore,
        memory_runtime_service: Any,
        compression_version: str = "v1",
    ) -> None:
        self._hot = hot_memory_store
        self._memory = memory_runtime_service
        self._compression_version = compression_version

    async def compact_session(
        self,
        *,
        session_id: str,
        turn_id: str,
        trace_id: str,
        user_message: str = "",
        assistant_message: str = "",
        tool_results: list[dict[str, Any]] | None = None,
        context_bundle: dict[str, Any] | None = None,
    ) -> bool:
        events = await self._hot.list_events(session_id, limit=10000)
        if not events:
            return False
        last_event_id = str(events[-1].get("id") or "")
        key = f"{last_event_id}:{self._compression_version}"
        if not await self._hot.acquire_compaction(session_id, key):
            return False
        try:
            if not user_message:
                user_message = self._first_text(events, "user")
            if not assistant_message:
                assistant_message = self._last_text(events, "assistant")
            await self._memory.write_turn_memory(
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                user_message=user_message,
                assistant_message=assistant_message,
                tool_results=tool_results or self._tool_results(events),
                context_bundle=context_bundle or {},
            )
            await self._hot.complete_compaction(session_id, key)
            await self._hot.clear_session(session_id)
            LOGGER.info("session_hot_memory_compacted", extra={"session_id": session_id, "last_event_id": last_event_id, "compression_version": self._compression_version})
            return True
        except Exception:
            LOGGER.exception("session_hot_memory_compaction_failed", extra={"session_id": session_id, "last_event_id": last_event_id})
            return False

    @staticmethod
    def _first_text(events: list[dict[str, Any]], role: str) -> str:
        for event in events:
            payload = event.get("payload") or {}
            if payload.get("role") == role and payload.get("content"):
                return str(payload["content"])
        return ""

    @staticmethod
    def _last_text(events: list[dict[str, Any]], role: str) -> str:
        for event in reversed(events):
            payload = event.get("payload") or {}
            if payload.get("role") == role and payload.get("content"):
                return str(payload["content"])
        return ""

    @staticmethod
    def _tool_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(event.get("payload") or {}) for event in events if str(event.get("type") or "").startswith("tool.")]

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
        state_reader = getattr(self._hot, "get_compaction_state", None)
        state = await state_reader(session_id) if callable(state_reader) else None
        state_status = str(state.get("status") or "") if isinstance(state, dict) else ""
        state_last_event_id = (
            str(state.get("last_event_id") or "") if isinstance(state, dict) else ""
        )
        pending_key = str(state.get("key") or "") if isinstance(state, dict) else ""
        try:
            if state_status == "pending" and state_last_event_id:
                # A failed compaction must retry the exact same event prefix so
                # the stable memory ids make the durable write idempotent.
                all_events = await self._hot.list_events(session_id, limit=10000)
                target_index = next(
                    (
                        index
                        for index, event in enumerate(all_events)
                        if str(event.get("id") or "") == state_last_event_id
                    ),
                    None,
                )
                if target_index is None:
                    return False
                events = all_events[: target_index + 1]
            else:
                after_id = state_last_event_id if state_status == "completed" else ""
                events = await self._hot.list_events(
                    session_id,
                    after_id=after_id or None,
                    limit=10000,
                )
        except TypeError:
            # Keep small in-memory/test stores compatible with the production
            # cursor contract while they are migrated.
            events = await self._hot.list_events(session_id, limit=10000)
        if not events:
            return False
        last_event_id = str(events[-1].get("id") or "")
        if not last_event_id:
            LOGGER.error(
                "session_hot_memory_compaction_invalid_cursor",
                extra={"session_id": session_id},
            )
            return False
        key = pending_key or f"{last_event_id}:{self._compression_version}"
        if not await self._hot.acquire_compaction(session_id, key):
            return False
        pending_marker = getattr(self._hot, "mark_compaction_pending", None)
        if callable(pending_marker):
            try:
                await pending_marker(session_id, last_event_id=last_event_id, key=key)
            except TypeError:
                await pending_marker(session_id, last_event_id=last_event_id)
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
                context_bundle={
                    **(context_bundle or {}),
                    "compaction_key": f"{session_id}:{key}",
                },
                compaction_key=f"{session_id}:{key}",
            )
            commit = getattr(self._hot, "commit_compaction", None)
            if callable(commit):
                committed = await commit(
                    session_id,
                    key=key,
                    last_event_id=last_event_id,
                )
                if not committed:
                    raise RuntimeError(
                        "Compaction cursor is no longer present; hot memory was retained."
                    )
            else:
                await self._hot.complete_compaction(session_id, key)
                await self._hot.clear_session(session_id)
            LOGGER.info("session_hot_memory_compacted", extra={"session_id": session_id, "last_event_id": last_event_id, "compression_version": self._compression_version})
            return True
        except Exception as exc:
            failed_marker = getattr(self._hot, "mark_compaction_failed", None)
            if callable(failed_marker):
                try:
                    await failed_marker(session_id, key=key, error=str(exc))
                except Exception:
                    LOGGER.exception(
                        "session_hot_memory_compaction_failure_marker_failed",
                        extra={"session_id": session_id, "last_event_id": last_event_id},
                    )
            LOGGER.exception("session_hot_memory_compaction_failed", extra={"session_id": session_id, "last_event_id": last_event_id, "error_type": type(exc).__name__})
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

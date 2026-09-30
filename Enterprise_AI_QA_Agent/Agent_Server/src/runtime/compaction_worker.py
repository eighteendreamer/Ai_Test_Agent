"""Compacts Redis hot Session memory into durable PostgreSQL memory."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from src.application.observability.business_metrics import (
    compaction_duration_seconds,
    compaction_events_total,
    compaction_total,
)
from src.infrastructure.redis_hot_memory_store import RedisHotMemoryStore

LOGGER = logging.getLogger(__name__)


class CompactionWorker:
    def __init__(
        self,
        *,
        hot_memory_store: RedisHotMemoryStore,
        memory_runtime_service: Any,
        compression_version: str = "v1",
        record_store: Any | None = None,
        owner_id: str | None = None,
        durable_event_store: Any | None = None,
        durable_page_size: int = 500,
    ) -> None:
        self._hot = hot_memory_store
        self._memory = memory_runtime_service
        self._compression_version = compression_version
        self._record_store = record_store
        self._owner_id = owner_id
        self._durable_event_store = durable_event_store
        self._durable_page_size = max(1, int(durable_page_size))

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
        started = perf_counter()
        state_reader = getattr(self._hot, "get_compaction_state", None)
        state = await state_reader(session_id) if callable(state_reader) else None
        state_status = str(state.get("status") or "") if isinstance(state, dict) else ""
        state_last_event_id = (
            str(state.get("last_event_id") or "") if isinstance(state, dict) else ""
        )
        pending_key = str(state.get("key") or "") if isinstance(state, dict) else ""
        events = await self._load_events(
            session_id=session_id,
            state_status=state_status,
            state_last_event_id=state_last_event_id,
        )
        if not events:
            self._record_cycle("skipped", started=started, events=0)
            return False
        last_event_id = str(events[-1].get("id") or "")
        if not last_event_id:
            LOGGER.error(
                "session_hot_memory_compaction_invalid_cursor",
                extra={"session_id": session_id},
            )
            self._record_cycle("skipped", started=started, events=0)
            return False
        key = pending_key or f"{last_event_id}:{self._compression_version}"
        if not await self._hot.acquire_compaction(session_id, key):
            self._record_cycle("not_owner", started=started, events=0)
            return False
        if self._record_store is not None:
            began = await self._record_store.begin(
                compaction_key=f"{session_id}:{key}",
                session_id=session_id,
                last_event_id=last_event_id,
                compression_version=self._compression_version,
                owner_id=self._owner_id,
            )
            if not began:
                await self._hot.complete_compaction(session_id, key)
                existing = await self._record_store.get(f"{session_id}:{key}")
                if existing is not None and existing.status == "completed":
                    commit = getattr(self._hot, "commit_compaction", None)
                    if callable(commit):
                        await commit(
                            session_id,
                            key=key,
                            last_event_id=last_event_id,
                        )
                    self._record_cycle("completed", started=started, events=0)
                    return True
                self._record_cycle("not_owner", started=started, events=0)
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
            memory_ids = await self._memory.write_turn_memory(
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
            if self._record_store is not None:
                completed = await self._record_store.complete(
                    f"{session_id}:{key}",
                    [str(item) for item in (memory_ids or [])],
                )
                if not completed:
                    existing = await self._record_store.get(f"{session_id}:{key}")
                    if existing is None or existing.status != "completed":
                        raise RuntimeError("Compaction record completion lost ownership")
            LOGGER.info("session_hot_memory_compacted", extra={"session_id": session_id, "last_event_id": last_event_id, "compression_version": self._compression_version})
            self._record_cycle("completed", started=started, events=len(events))
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
            if self._record_store is not None:
                try:
                    await self._record_store.fail(f"{session_id}:{key}", str(exc))
                except Exception:
                    LOGGER.exception(
                        "session_hot_memory_compaction_record_failed",
                        extra={"session_id": session_id, "last_event_id": last_event_id},
                    )
            LOGGER.exception("session_hot_memory_compaction_failed", extra={"session_id": session_id, "last_event_id": last_event_id, "error_type": type(exc).__name__})
            self._record_cycle("failed", started=started, events=0)
            return False

    @staticmethod
    def _record_cycle(result: str, *, started: float, events: int) -> None:
        """Export one compaction cycle.

        Backlog lag is not exported here: the record store persists the event
        sequence watermark and the metrics collector derives the depth from it.
        """
        compaction_total().labels(result=result).inc()
        if events:
            compaction_events_total().inc(events)
        compaction_duration_seconds().observe(max(0.0, perf_counter() - started))

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

    async def _load_events(
        self,
        *,
        session_id: str,
        state_status: str,
        state_last_event_id: str,
    ) -> list[dict[str, Any]]:
        """Read the hot prefix first and fall back to durable FlowEvents.

        Redis is intentionally not the source of truth.  A missing/expired hot
        cursor must not make a completed turn impossible to compact, so the
        PostgreSQL event store is consulted when Redis is unavailable or no
        longer contains the requested cursor.
        """
        hot_events: list[dict[str, Any]] = []
        hot_available = True
        try:
            if state_status == "pending" and state_last_event_id:
                hot_events = await self._hot.list_events(session_id, limit=10000)
                target_index = self._event_index(hot_events, state_last_event_id)
                if target_index is not None:
                    return hot_events[: target_index + 1]
            else:
                after_id = state_last_event_id if state_status == "completed" else ""
                hot_events = await self._hot.list_events(
                    session_id,
                    after_id=after_id or None,
                    limit=10000,
                )
                if hot_events:
                    return hot_events
        # Hot-memory adapters may raise provider-specific errors; log and fall
        # back to the durable source rather than aborting compaction.
        except Exception as exc:  # noqa: BLE001
            hot_available = False
            LOGGER.warning(
                "session_hot_memory_read_failed_using_durable_events",
                extra={"session_id": session_id, "error_type": type(exc).__name__},
            )

        durable_events = await self._read_durable_events(
            session_id=session_id,
            after_event_id=(
                None
                if state_status == "pending"
                else state_last_event_id or None
            ),
        )
        if state_status == "pending" and state_last_event_id:
            target_index = self._event_index(durable_events, state_last_event_id)
            if target_index is None:
                LOGGER.error(
                    "session_compaction_cursor_missing_from_durable_events",
                    extra={"session_id": session_id, "last_event_id": state_last_event_id},
                )
                return []
            return durable_events[: target_index + 1]
        if durable_events:
            if not hot_available:
                LOGGER.info(
                    "session_compaction_loaded_from_durable_events",
                    extra={"session_id": session_id, "event_count": len(durable_events)},
                )
            return durable_events
        return []

    async def _read_durable_events(
        self,
        *,
        session_id: str,
        after_event_id: str | None,
    ) -> list[dict[str, Any]]:
        store = self._durable_event_store
        reader = getattr(store, "read_event_page", None) if store is not None else None
        if not callable(reader):
            return []
        events: list[dict[str, Any]] = []
        cursor = after_event_id
        while True:
            try:
                page = await reader(
                    session_id,
                    limit=self._durable_page_size,
                    after_event_id=cursor,
                )
            # Stop pagination on provider errors after logging; retrying this page
            # here could leave the worker in an unbounded retry loop.
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "session_durable_event_read_failed",
                    extra={"session_id": session_id, "error_type": type(exc).__name__},
                )
                return []
            if not page:
                break
            events.extend(self._event_to_dict(event) for event in page)
            if len(page) < self._durable_page_size:
                break
            cursor = str(self._event_to_dict(page[-1]).get("id") or "") or None
            if cursor is None:
                break
        return events

    @staticmethod
    def _event_index(events: list[dict[str, Any]], event_id: str) -> int | None:
        for index, event in enumerate(events):
            if str(event.get("id") or "") == event_id:
                return index
        return None

    @staticmethod
    def _event_to_dict(event: Any) -> dict[str, Any]:
        if isinstance(event, dict):
            return dict(event)
        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            return dict(model_dump(mode="json"))
        return {
            "id": str(getattr(event, "id", "")),
            "type": str(getattr(event, "type", "")),
            "payload": dict(getattr(event, "payload", {}) or {}),
        }

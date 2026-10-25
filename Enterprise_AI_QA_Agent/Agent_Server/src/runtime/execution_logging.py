from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.application.security.output_safety_policy import OutputSafetyPolicy


_AUDIT_OUTPUT_POLICY = OutputSafetyPolicy()

from src.graph.state import AgentGraphState
from src.runtime.session_event_sink import SessionEventSink
from src.runtime.execution_context import current_graph_event_sink
from src.schemas.session import ExecutionEvent


def truncate_text(value: str, limit: int = 180) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def summarize_messages(messages: list[dict[str, Any]], limit: int = 2) -> list[dict[str, str]]:
    return [
        {
            "role": str(item.get("role", "unknown")),
            "content_preview": truncate_text(str(item.get("content", "")), 160),
        }
        for item in messages[:limit]
    ]


def append_graph_event(
    state: AgentGraphState,
    event_type: str,
    phase: str,
    message: str,
    **payload: Any,
) -> None:
    event_log = state.setdefault("event_log", [])
    event_payload: dict[str, Any] = {
        "step": len(event_log) + 1,
        "phase": phase,
        "message": message,
        "turn_id": state.get("turn_id", ""),
        "trace_id": state.get("trace_id", ""),
    }
    if event_payload["trace_id"]:
        event_payload["correlation_id"] = f"{event_payload['trace_id']}:{event_payload['step']}"
    event_payload.update(_AUDIT_OUTPUT_POLICY.sanitize_for_audit(payload))
    event = ExecutionEvent(type=event_type, session_id=state.get("session_id", ""),
        timestamp=datetime.now(timezone.utc), payload=event_payload)
    event_log.append({"id": event.id, "timestamp": event.timestamp.isoformat(),
                      "type": event_type, "payload": event_payload})

    # The bounded sink persists a live prefix; finalization appends any
    # overflow suffix with the same IDs before the turn is settled.
    event_queue = current_graph_event_sink()
    if event_queue is not None:
        try:
            event_queue.put_nowait(event)
        except asyncio.QueueFull:
            if isinstance(event_queue, SessionEventSink) and event_queue.deferred_to_final_flush:
                return
            logging.getLogger(__name__).error("runtime_event_buffer_full", extra={
                "session_id": event.session_id, "event_id": event.id,
            })
            raise RuntimeError("Runtime event persistence buffer is full")

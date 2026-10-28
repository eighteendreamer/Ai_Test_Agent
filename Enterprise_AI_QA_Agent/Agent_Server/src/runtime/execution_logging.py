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


GRAPH_EVENT_LOG_VERSION_KEY = "framework_event_log_version"


def prepare_graph_event_log(state):
    """Only a new Turn with acknowledged events may opt into completion material.

    Historical/no-sink prefixes remain legal normal paths; their later durable
    suffix cannot retroactively prove the entire original collection.
    """
    context = state["context_bundle"]
    context.pop(GRAPH_EVENT_LOG_VERSION_KEY, None)
    sink = current_graph_event_sink()
    if isinstance(sink, SessionEventSink) and sink.runtime_owner is not None:
        binding = sink.runtime_owner.binding
        if state.get("session_id") != binding.session_id or state.get("turn_id") != binding.turn_id:
            raise ValueError("Runtime graph collection differs from its event sink scope")
        context[GRAPH_EVENT_LOG_VERSION_KEY] = 1


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
    event = _build_graph_event(state, event_type, phase, message, **payload)
    _enqueue_graph_event(event)


async def append_graph_event_durable(
    state: AgentGraphState,
    event_type: str,
    phase: str,
    message: str,
    **payload: Any,
) -> None:
    """Owner-bound producers wait for the existing event transaction, not enqueue.

    Legacy graphs retain their synchronous queue/final-flush contract. This
    acknowledgement is an event fact, never graph/application completion proof.
    """
    event = _build_graph_event(state, event_type, phase, message, **payload)
    await _persist_graph_event_durable(event)


async def append_graph_event_intent_durable(state, event: ExecutionEvent):
    """Append a supplied intent using the same ordered owner-bound ACK port."""
    if (event.session_id != state.get("session_id") or event.payload.get("turn_id") != state.get("turn_id")
        or event.payload.get("trace_id") != state.get("trace_id", "")
        or event.payload.get("step") != len(state.get("event_log", [])) + 1):
        raise ValueError("Prepared graph event differs from its original scope or next step")
    _record_graph_event(state, event)
    await _persist_graph_event_durable(event)


async def _persist_graph_event_durable(event):
    sink = current_graph_event_sink()
    if isinstance(sink, SessionEventSink) and sink.runtime_owner is not None:
        await sink.persist(event)
    else:
        _enqueue_graph_event(event)


async def complete_graph_event_log(state, native_terminal, *, returned_event_offset=0):
    """Optional material on an owner-bound acknowledged sink, never recovery authority.

    Legacy/no-sink calls retain their existing result contract; they cannot
    claim a persisted complete collection using this port.
    """
    if state.get("context_bundle", {}).get(GRAPH_EVENT_LOG_VERSION_KEY) != 1:
        return
    sink = current_graph_event_sink()
    if isinstance(sink, SessionEventSink) and sink.runtime_owner is not None:
        binding = sink.runtime_owner.binding
        if state.get("session_id") != binding.session_id or state.get("turn_id") != binding.turn_id:
            raise ValueError("Runtime graph collection differs from its event sink scope")
        await sink.complete_runtime_events(state["event_log"], native_terminal,
            returned_event_offset=returned_event_offset)


def _graph_event_payload(state, phase, message, **payload):
    event_payload: dict[str, Any] = {
        "step": len(state.get("event_log", [])) + 1,
        "phase": phase,
        "message": message,
        "turn_id": state.get("turn_id", ""),
        "trace_id": state.get("trace_id", ""),
    }
    if event_payload["trace_id"]:
        event_payload["correlation_id"] = f"{event_payload['trace_id']}:{event_payload['step']}"
    event_payload.update(_AUDIT_OUTPUT_POLICY.sanitize_for_audit(payload))
    return event_payload


def build_graph_event_intent(state, event_type, phase, message, *, event_id, timestamp, **payload) -> ExecutionEvent:
    """Pure audit projection: identity/time are supplied, not allocated here."""
    return ExecutionEvent(id=event_id, type=event_type, session_id=state.get("session_id", ""),
        timestamp=timestamp, payload=_graph_event_payload(state, phase, message, **payload))


def _build_graph_event(state, event_type, phase, message, **payload) -> ExecutionEvent:
    event = ExecutionEvent(type=event_type, session_id=state.get("session_id", ""),
        timestamp=datetime.now(timezone.utc), payload=_graph_event_payload(state, phase, message, **payload))
    _record_graph_event(state, event)
    return event


def _record_graph_event(state, event):
    state.setdefault("event_log", []).append({"id": event.id, "timestamp": event.timestamp.isoformat(),
        "type": event.type, "payload": event.payload})


def _enqueue_graph_event(event: ExecutionEvent) -> None:
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

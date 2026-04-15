from __future__ import annotations

from typing import Any

from src.graph.state import AgentGraphState


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
    event_payload: dict[str, Any] = {
        "step": len(state["event_log"]) + 1,
        "phase": phase,
        "message": message,
        "turn_id": state["turn_id"],
        "trace_id": state.get("trace_id", ""),
    }
    if event_payload["trace_id"]:
        event_payload["correlation_id"] = f"{event_payload['trace_id']}:{event_payload['step']}"
    event_payload.update(payload)
    state["event_log"].append({"type": event_type, "payload": event_payload})

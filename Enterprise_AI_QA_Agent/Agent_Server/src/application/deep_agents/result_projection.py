"""Deterministic Deep Agents output projection, shared with read-only native reads.

Inputs must already be obtained and verified through their original ports.
This module performs no I/O and grants no execution/recovery authority. Native
messages remain lossless; normalized messages are display-only. Stage and raw
callback facts must be supplied, never inferred from the last assistant text.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.application.deep_agents.framework_transcript import serialize_messages
from src.schemas.framework_approval import NativeToolInterrupt


class DeepAgentRuntimeError(RuntimeError):
    """A boundary error raised by the optional Deep Agents harness."""


@dataclass(frozen=True)
class DeepAgentRuntimeResult:
    output_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    framework_messages: list[dict[str, Any]] = field(default_factory=list)
    todos: list[dict[str, str]] = field(default_factory=list)
    subagent_events: list[dict[str, Any]] = field(default_factory=list)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    native_interrupts: list[NativeToolInterrupt] = field(default_factory=list)
    native_checkpoint_timestamp: str | None = None


def project_deep_agent_result(
    values: dict[str, Any],
    *,
    stage: str,
    subagent_events: list[dict[str, Any]],
    native_interrupts: list[NativeToolInterrupt] | None = None,
    native_terminal: dict[str, Any] | None = None,
    native_checkpoint_timestamp: str | None = None,
    root_model_budget: dict[str, Any] | None = None,
) -> DeepAgentRuntimeResult:
    """Project verified invocation/native values using one existing rule set.

    No clock, generated identity, model, tool, graph or application finalizer is
    consulted. A terminal proof is only carried through, not authenticated here.
    Raw callback facts and original stage are required separate inputs because
    checkpoints need not contain them. This is NOT a complete RuntimeTurnResult
    projector or permission to resume a Turn without its original result asset.

    Keep the existing nonempty-assistant contract until the structured-result
    boundary in v4 §9.3 is independently implemented and verified.
    """
    raw_messages = values.get("messages")
    messages = _normalize_messages(raw_messages)
    interrupts = [{"id": item.id, "value": item.value} for item in values.get("__interrupt__", [])]
    if interrupts and native_terminal is not None:
        raise DeepAgentRuntimeError("An interrupted result cannot also carry native terminal evidence.")
    output_text = "" if interrupts else _last_assistant_text(messages)
    if not output_text and not interrupts:
        raise DeepAgentRuntimeError("Deep Agents execution returned no assistant message.")
    return DeepAgentRuntimeResult(
        output_text=output_text,
        metadata={"harness": "deepagents", "stage": stage, "message_count": len(messages),
            **({"root_model_budget": deepcopy(root_model_budget)} if root_model_budget is not None else {}),
            **({"native_terminal": deepcopy(native_terminal)} if native_terminal is not None else {})},
        messages=deepcopy(messages),
        framework_messages=deepcopy(serialize_messages(raw_messages)),
        todos=_normalize_todos(values.get("todos")),
        subagent_events=deepcopy(subagent_events),
        interrupts=deepcopy(interrupts),
        native_interrupts=deepcopy(native_interrupts or []),
        native_checkpoint_timestamp=native_checkpoint_timestamp,
    )


def _normalize_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for message in value:
        if isinstance(message, dict):
            normalized.append(message)
            continue
        role = str(getattr(message, "type", "") or "")
        content = getattr(message, "content", "")
        item = {"role": {"ai": "assistant", "human": "user"}.get(role, role), "content": content}
        if getattr(message, "tool_calls", None):
            item["tool_calls"] = list(message.tool_calls)
        if getattr(message, "tool_call_id", None):
            item["tool_call_id"] = message.tool_call_id
            item["name"] = getattr(message, "name", None)
        normalized.append(item)
    return normalized


def _normalize_todos(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        status = str(item.get("status") or "").strip()
        if content and status in {"pending", "in_progress", "completed"}:
            normalized.append({"content": content, "status": status})
    return normalized


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        role = str(message.get("role") or message.get("type") or "").lower()
        if role not in {"assistant", "ai"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "\n".join(
                str(item.get("text") or item.get("content") or "").strip()
                for item in content
                if isinstance(item, dict) and (item.get("text") or item.get("content"))
            ).strip()
            if text:
                return text
    return ""

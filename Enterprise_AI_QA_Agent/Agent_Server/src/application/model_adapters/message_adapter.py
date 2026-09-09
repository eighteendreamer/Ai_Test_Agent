from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.application.model_clients.base import serialize_tool_result_text
from src.schemas.model_config import ContentPart, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall


def to_langchain_messages(
    system_prompt: str,
    request: ModelInvocationRequest,
) -> list[BaseMessage]:
    """Convert the frozen application message contract to LangChain messages."""

    messages: list[BaseMessage] = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    for message in request.structured_messages:
        messages.append(_to_langchain_message(message))
    return messages


def from_langchain_message(message: BaseMessage) -> dict[str, Any]:
    """Extract the existing result fields from a LangChain AI message.

    The return value is deliberately a plain dictionary so the caller can
    construct ``ModelInvocationResult`` without leaking LangChain classes.
    """

    content = _text_content(message.content)
    tool_calls = [
        ModelToolCall(
            id=str(call.get("id") or f"call_{index}"),
            name=str(call.get("name") or ""),
            arguments=_tool_arguments(call.get("args")),
        )
        for index, call in enumerate(getattr(message, "tool_calls", []) or [])
        if isinstance(call, dict) and str(call.get("name") or "").strip()
    ]
    usage = getattr(message, "usage_metadata", None) or {}
    response_metadata = getattr(message, "response_metadata", None) or {}
    return {
        "text": content,
        "tool_calls": tool_calls,
        "usage": dict(usage) if isinstance(usage, dict) else {},
        "response_id": str(response_metadata.get("id") or "") or None,
        "finish_reason": response_metadata.get("finish_reason"),
        "stop_reason": response_metadata.get("stop_reason"),
        "raw_response": response_metadata,
    }


def _to_langchain_message(message: UnifiedMessage) -> BaseMessage:
    content = _parts_to_content(message.parts)
    if message.role == "system":
        return SystemMessage(content=content)
    if message.role == "tool":
        tool_part = next((part for part in message.parts if part.type == "tool_result"), None)
        kwargs: dict[str, Any] = {}
        if tool_part and tool_part.tool_name:
            kwargs["name"] = tool_part.tool_name
        return ToolMessage(
            content=serialize_tool_result_text(tool_part),
            tool_call_id=message.tool_call_id or "unknown",
            **kwargs,
        )
    if message.role == "assistant":
        tool_calls = [
            {
                "id": call.id,
                "name": call.name,
                "args": dict(call.arguments),
                "type": "tool_call",
            }
            for call in message.tool_calls
        ]
        return AIMessage(content=content, tool_calls=tool_calls)
    return HumanMessage(content=content)


def _parts_to_content(parts: list[ContentPart]) -> str | list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in parts:
        block = _part_to_content_block(part)
        if block is not None:
            blocks.append(block)
    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text") or "")
    return blocks


def _part_to_content_block(part: ContentPart) -> dict[str, Any] | None:
    if part.type == "text":
        return {"type": "text", "text": part.text or ""}
    if part.type == "image_url" and part.url:
        return {"type": "image_url", "image_url": {"url": part.url}}
    if part.type == "image_base64" and part.data_base64:
        mime_type = part.mime_type or "image/jpeg"
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{part.data_base64}"},
        }
    if part.type == "file":
        return {"type": "text", "text": f"[file:{part.file_name or 'file'}]"}
    if part.type == "tool_result":
        return {"type": "text", "text": serialize_tool_result_text(part)}
    return None


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "\n".join(part.strip() for part in parts if part.strip()).strip()
    return str(content or "").strip()


def _tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return parsed if isinstance(parsed, dict) else {"raw": value}
    return {}

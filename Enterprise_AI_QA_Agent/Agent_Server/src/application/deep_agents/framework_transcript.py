"""Native LangChain message persistence for framework-owned checkpoints."""
from __future__ import annotations

from typing import Any


def serialize_messages(value: Any) -> list[dict[str, Any]]:
    """Serialize BaseMessage objects with LangChain's lossless wire format.

    Role/content dictionaries are application projections and are intentionally
    not converted back into framework messages here. Reconstructing them would
    discard provider blocks, signatures, and response metadata.
    """
    if not isinstance(value, list) or not value:
        return []
    from langchain_core.messages import BaseMessage, messages_to_dict

    if not all(isinstance(item, BaseMessage) for item in value):
        return []
    return messages_to_dict(value)


def deserialize_messages(value: Any) -> list[Any]:
    """Restore framework messages only from the official serialized format."""
    if not isinstance(value, list) or not value:
        return []
    from langchain_core.messages import messages_from_dict

    if not all(
        isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and isinstance(item.get("data"), dict)
        for item in value
    ):
        raise ValueError("framework transcript must contain LangChain message dictionaries")
    return messages_from_dict(value)


def append_turn_input(
    framework_messages: list[dict[str, Any]],
    content: str,
    turn_id: str,
) -> list[dict[str, Any]]:
    """Append the new user message without converting prior native messages."""
    messages = list(framework_messages)
    if not str(content or "").strip():
        return messages
    from langchain_core.messages import HumanMessage, messages_to_dict

    messages.extend(messages_to_dict([
        HumanMessage(content=content, id=f"framework-input:{turn_id}"),
    ]))
    return messages

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Protocol

from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.tool_runtime import ModelToolCall


TextDeltaHandler = Callable[[str], Awaitable[None]]


_MODEL_TOOL_NAME_MAX_LENGTH = 64


class ProviderClientError(Exception):
    """Uniform wrapper around openai / anthropic / google-genai API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ProviderClient(Protocol):
    """Thin per-provider client backed by the official SDK.

    Both methods return the parsed dict contract used across the runtime:
    text / tool_calls / finish_reason / stop_reason / usage / response_id /
    raw_response. Tool names are already remapped back to their original
    registry names before returning.
    """

    client_key: str

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]: ...

    async def stream(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
        on_text_delta: TextDeltaHandler,
    ) -> dict[str, Any]: ...


def sanitize_tool_name(value: str, *, allow_dash: bool = True) -> str:
    pattern = r"[^a-zA-Z0-9_-]+" if allow_dash else r"[^a-zA-Z0-9_]+"
    cleaned = re.sub(pattern, "_", value).strip("_")
    if not cleaned:
        cleaned = "tool"
    if cleaned[0].isdigit():
        cleaned = f"tool_{cleaned}"
    return cleaned[:_MODEL_TOOL_NAME_MAX_LENGTH]


def _append_tool_name_suffix(base: str, suffix: int) -> str:
    """Append a collision suffix without exceeding the provider name limit."""

    suffix_text = f"_{suffix}"
    prefix_length = _MODEL_TOOL_NAME_MAX_LENGTH - len(suffix_text)
    if prefix_length <= 0:
        # This requires more than 10**62 colliding tools. Keep the failure
        # bounded rather than allowing a truncation loop to repeat forever.
        return f"tool{suffix_text}"[:_MODEL_TOOL_NAME_MAX_LENGTH]
    return f"{base[:prefix_length]}{suffix_text}"


def build_tool_name_map(
    tools: list[dict[str, Any]],
    *,
    allow_dash: bool = True,
) -> dict[str, str]:
    name_map: dict[str, str] = {}
    used: set[str] = set()
    for item in tools:
        original = str(item.get("name") or "").strip()
        if not original:
            continue
        if original in name_map:
            continue
        base = sanitize_tool_name(original, allow_dash=allow_dash)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = _append_tool_name_suffix(base, suffix)
            suffix += 1
            if suffix > len(tools) + 1:
                raise ValueError("Unable to allocate a unique model tool name.")
        used.add(candidate)
        name_map[original] = candidate
    return name_map


def remap_tool_calls(
    tool_calls: list[ModelToolCall],
    tool_name_map: dict[str, str] | None,
) -> list[ModelToolCall]:
    if not tool_name_map:
        return tool_calls
    reverse_map = {sanitized: original for original, sanitized in tool_name_map.items()}
    return [
        ModelToolCall(
            id=item.id,
            name=reverse_map.get(item.name, item.name),
            arguments=item.arguments,
        )
        for item in tool_calls
    ]


def parse_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        try:
            loaded = json.loads(value)
        except Exception:
            return {"raw": value}
        return loaded if isinstance(loaded, dict) else {"raw": value}
    return {}


def serialize_tool_result_text(part: Any) -> str:
    if part is None:
        return ""
    if getattr(part, "text", None):
        return str(part.text)
    if getattr(part, "payload", None):
        return json.dumps(part.payload, ensure_ascii=False)
    return ""


def response_to_dict(response: Any) -> dict[str, Any]:
    """Convert an SDK response object into a plain dict for raw_response."""
    if isinstance(response, dict):
        return response
    for method_name in ("to_json_dict", "model_dump"):
        method = getattr(response, method_name, None)
        if callable(method):
            try:
                data = method()
            except TypeError:
                continue
            if isinstance(data, dict):
                return data
    return {"repr": repr(response)}

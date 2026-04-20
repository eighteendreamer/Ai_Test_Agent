from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.tool_runtime import ModelToolCall


@dataclass(frozen=True)
class AdapterDescriptor:
    name: str
    protocol: str
    chat_path: str
    auth_header: str = "authorization"
    auth_prefix: str = "Bearer "
    extra_headers: dict[str, str] = field(default_factory=dict)
    supports_parallel_tool_calls: bool = False


@dataclass
class StreamParseState:
    response_id: str = ""
    finish_reason: str | None = None
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    text_parts: list[str] = field(default_factory=list)
    tool_call_buffers: dict[int, dict[str, Any]] = field(default_factory=dict)
    current_tool_index: int | None = None


@dataclass
class StreamChunkParseResult:
    text_delta: str = ""
    should_stop: bool = False


class ProviderAdapter:
    adapter_key = "base"

    def matches(self, config: ModelConfigRecord) -> bool:
        raise NotImplementedError

    def describe(self, config: ModelConfigRecord) -> AdapterDescriptor:
        raise NotImplementedError

    def build_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_response(self, config: ModelConfigRecord, data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def create_stream_state(self) -> StreamParseState:
        return StreamParseState()

    def parse_stream_chunk(
        self,
        config: ModelConfigRecord,
        state: StreamParseState,
        chunk: str,
    ) -> StreamChunkParseResult:
        raise NotImplementedError

    def finalize_stream(
        self,
        config: ModelConfigRecord,
        state: StreamParseState,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def build_headers(self, config: ModelConfigRecord, api_key: str) -> dict[str, str]:
        descriptor = self.describe(config)
        authorization_value = (
            f"{descriptor.auth_prefix}{api_key}" if descriptor.auth_prefix else api_key
        )
        return {
            descriptor.auth_header: authorization_value,
            "content-type": "application/json",
            **descriptor.extra_headers,
            **config.extra_headers,
        }

    def build_url(self, config: ModelConfigRecord) -> str:
        descriptor = self.describe(config)
        base_url = config.api_base_url.rstrip("/")
        if base_url.endswith(descriptor.chat_path):
            return base_url
        return f"{base_url}{descriptor.chat_path}"

    def build_tool_name_map(self, tools: list[dict[str, Any]]) -> dict[str, str]:
        name_map: dict[str, str] = {}
        used: set[str] = set()
        for item in tools:
            original = str(item.get("name") or "").strip()
            if not original:
                continue
            candidate = self._sanitize_tool_name(original)
            suffix = 2
            while candidate in used and name_map.get(original) != candidate:
                candidate = self._sanitize_tool_name(f"{original}_{suffix}")
                suffix += 1
            used.add(candidate)
            name_map[original] = candidate
        return name_map

    def remap_tool_calls(
        self,
        tool_calls: list[ModelToolCall],
        tool_name_map: dict[str, str] | None,
    ) -> list[ModelToolCall]:
        if not tool_name_map:
            return tool_calls
        reverse_map = {sanitized: original for original, sanitized in tool_name_map.items()}
        remapped: list[ModelToolCall] = []
        for item in tool_calls:
            remapped.append(
                ModelToolCall(
                    id=item.id,
                    name=reverse_map.get(item.name, item.name),
                    arguments=item.arguments,
                )
            )
        return remapped

    def parse_json_chunk(self, data_chunk: str) -> dict[str, Any] | None:
        try:
            loaded = json.loads(data_chunk)
        except Exception:
            return None
        return loaded if isinstance(loaded, dict) else None

    def parse_tool_arguments(self, value: Any) -> dict[str, Any]:
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

    def extract_openai_message_text(self, message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_blocks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_blocks.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("type") in {"text", "output_text"}:
                    text_blocks.append(str(item.get("text") or item.get("content") or ""))
                    continue
                if isinstance(item.get("text"), dict):
                    text_blocks.append(str(item["text"].get("value", "")))
            return "\n".join(block.strip() for block in text_blocks if block.strip()).strip()
        return str(content or "").strip()

    def extract_openai_tool_calls(self, message: Any) -> list[ModelToolCall]:
        if not isinstance(message, dict):
            return []
        raw_tool_calls = message.get("tool_calls", [])
        if not isinstance(raw_tool_calls, list):
            return []
        output: list[ModelToolCall] = []
        for index, tool_call in enumerate(raw_tool_calls):
            if not isinstance(tool_call, dict):
                continue
            function_block = tool_call.get("function") or {}
            if not isinstance(function_block, dict):
                continue
            output.append(
                ModelToolCall(
                    id=str(tool_call.get("id") or f"call_{index}"),
                    name=str(function_block.get("name") or ""),
                    arguments=self.parse_tool_arguments(function_block.get("arguments", "")),
                )
            )
        return output

    def _sanitize_tool_name(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
        if not cleaned:
            cleaned = "tool"
        if cleaned[0].isdigit():
            cleaned = f"tool_{cleaned}"
        return cleaned[:64]

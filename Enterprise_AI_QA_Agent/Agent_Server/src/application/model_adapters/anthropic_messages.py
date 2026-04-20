from __future__ import annotations

import json
from typing import Any

from src.application.model_adapters.base import (
    AdapterDescriptor,
    ProviderAdapter,
    StreamChunkParseResult,
    StreamParseState,
)
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall


class AnthropicMessagesAdapter(ProviderAdapter):
    adapter_key = "anthropic_messages"

    def matches(self, config: ModelConfigRecord) -> bool:
        return config.transport == "anthropic_messages" or str(config.provider).strip().lower() == "anthropic"

    def describe(self, config: ModelConfigRecord) -> AdapterDescriptor:
        return AdapterDescriptor(
            name="anthropic",
            protocol="anthropic_messages",
            chat_path="/v1/messages",
            auth_header="x-api-key",
            auth_prefix="",
            extra_headers={"anthropic-version": "2023-06-01"},
        )

    def build_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model_id,
            "max_tokens": config.max_tokens,
            "system": request.system_prompt,
            "messages": self._build_messages(request),
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            payload["tools"] = [
                {
                    "name": item["name"],
                    "description": item["description"],
                    "input_schema": item["input_schema"],
                }
                for item in request.tools
            ]
        return payload

    def parse_response(self, config: ModelConfigRecord, data: dict[str, Any]) -> dict[str, Any]:
        blocks = data.get("content", []) or []
        text = "\n".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        tool_calls = [
            ModelToolCall(
                id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                arguments=block.get("input", {}) if isinstance(block.get("input"), dict) else {},
            )
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        return {
            "text": text,
            "tool_calls": tool_calls,
            "finish_reason": None,
            "stop_reason": data.get("stop_reason"),
            "usage": data.get("usage", {}),
            "response_id": data.get("id"),
            "raw_response": data,
        }

    def parse_stream_chunk(
        self,
        config: ModelConfigRecord,
        state: StreamParseState,
        chunk: str,
    ) -> StreamChunkParseResult:
        parsed = self.parse_json_chunk(chunk)
        if parsed is None:
            return StreamChunkParseResult()

        event_type = str(parsed.get("type") or "")
        if event_type == "message_start":
            message = parsed.get("message") or {}
            if isinstance(message, dict):
                state.response_id = str(message.get("id") or state.response_id)
                usage_payload = message.get("usage")
                if isinstance(usage_payload, dict):
                    state.usage = usage_payload
            return StreamChunkParseResult()

        if event_type == "content_block_start":
            index = int(parsed.get("index", 0))
            content_block = parsed.get("content_block") or {}
            if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                state.current_tool_index = index
                state.tool_call_buffers[index] = {
                    "id": str(content_block.get("id") or f"tool_{index}"),
                    "name": str(content_block.get("name") or ""),
                    "arguments": "",
                }
            return StreamChunkParseResult()

        if event_type == "content_block_delta":
            index = int(parsed.get("index", state.current_tool_index or 0))
            delta = parsed.get("delta") or {}
            if not isinstance(delta, dict):
                return StreamChunkParseResult()
            if delta.get("type") == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    state.text_parts.append(text)
                    return StreamChunkParseResult(text_delta=text)
                return StreamChunkParseResult()
            if delta.get("type") == "input_json_delta":
                buffer = state.tool_call_buffers.setdefault(
                    index,
                    {"id": f"tool_{index}", "name": "", "arguments": ""},
                )
                buffer["arguments"] += str(delta.get("partial_json") or "")
            return StreamChunkParseResult()

        if event_type == "message_delta":
            delta = parsed.get("delta") or {}
            if isinstance(delta, dict):
                state.stop_reason = delta.get("stop_reason") or state.stop_reason
            usage_payload = parsed.get("usage")
            if isinstance(usage_payload, dict) and usage_payload:
                state.usage = usage_payload
            return StreamChunkParseResult()

        return StreamChunkParseResult()

    def finalize_stream(
        self,
        config: ModelConfigRecord,
        state: StreamParseState,
    ) -> dict[str, Any]:
        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"tool_{index}"),
                name=str(item.get("name") or ""),
                arguments=self.parse_tool_arguments(item.get("arguments", "")),
            )
            for index, item in sorted(state.tool_call_buffers.items(), key=lambda pair: pair[0])
        ]
        return {
            "text": "".join(state.text_parts).strip(),
            "tool_calls": tool_calls,
            "finish_reason": state.finish_reason,
            "stop_reason": state.stop_reason,
            "usage": state.usage,
            "response_id": state.response_id,
            "raw_response": {
                "mode": "stream",
                "provider": config.provider,
                "stop_reason": state.stop_reason,
                "usage": state.usage,
            },
        }

    def _build_messages(self, request: ModelInvocationRequest) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for item in request.structured_messages:
            role = item.role
            if role == "system":
                continue
            if role == "tool":
                tool_part = next((part for part in item.parts if part.type == "tool_result"), None)
                payload.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": item.tool_call_id,
                                "content": self._serialize_tool_result(tool_part),
                            }
                        ],
                    }
                )
                continue
            payload.append({"role": role, "content": self._serialize_parts(item)})
        return payload

    def _serialize_parts(self, message: UnifiedMessage) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in message.parts:
            block = self._serialize_part(part)
            if block is not None:
                blocks.append(block)
        return blocks or [{"type": "text", "text": ""}]

    def _serialize_part(self, part: ContentPart) -> dict[str, Any] | None:
        if part.type == "text":
            return {"type": "text", "text": part.text or ""}
        if part.type == "image_base64" and part.data_base64:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": part.mime_type or "image/jpeg",
                    "data": part.data_base64,
                },
            }
        if part.type == "image_url" and part.url and part.url.startswith("data:") and ";base64," in part.url:
            prefix, data_base64 = part.url.split(";base64,", 1)
            mime_type = prefix.split("data:", 1)[-1] or "image/jpeg"
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": data_base64,
                },
            }
        if part.type == "image_url" and part.url:
            return {"type": "text", "text": f"Image URL: {part.url}"}
        if part.type == "file":
            label = part.file_name or "file"
            return {"type": "text", "text": f"[file:{label}]"}
        if part.type == "tool_result":
            return {"type": "text", "text": self._serialize_tool_result(part)}
        return None

    def _serialize_tool_result(self, part: ContentPart | None) -> str:
        if part is None:
            return ""
        if part.text:
            return part.text
        if part.payload:
            return json.dumps(part.payload, ensure_ascii=False)
        return ""

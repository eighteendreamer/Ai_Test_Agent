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


class GoogleGeminiGenerateContentAdapter(ProviderAdapter):
    adapter_key = "google_gemini_generate_content"

    def matches(self, config: ModelConfigRecord) -> bool:
        provider = str(config.provider or "").strip().lower()
        return config.transport == "google_gemini_generate_content" or provider in {"google", "gemini"}

    def describe(self, config: ModelConfigRecord) -> AdapterDescriptor:
        return AdapterDescriptor(
            name="google",
            protocol="google_gemini_generate_content",
            chat_path=f"/v1beta/models/{config.model_id}:generateContent",
            auth_header="x-goog-api-key",
            auth_prefix="",
        )

    def build_url(self, config: ModelConfigRecord) -> str:
        base_url = config.api_base_url.rstrip("/")
        descriptor = self.describe(config)
        if base_url.endswith(descriptor.chat_path):
            return base_url
        return f"{base_url}{descriptor.chat_path}"

    def build_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": self._build_contents(request),
            "generationConfig": {
                "maxOutputTokens": config.max_tokens,
            },
        }
        if request.system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}],
            }
        if config.temperature is not None:
            payload["generationConfig"]["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": (tool_name_map or {}).get(item["name"], item["name"]),
                            "description": item["description"],
                            "parameters": item["input_schema"],
                        }
                        for item in request.tools
                    ]
                }
            ]
        return payload

    def parse_response(self, config: ModelConfigRecord, data: dict[str, Any]) -> dict[str, Any]:
        candidates = data.get("candidates", []) or []
        first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        content = first.get("content") or {}
        parts = content.get("parts") or []

        text_blocks: list[str] = []
        tool_calls: list[ModelToolCall] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if part.get("text"):
                text_blocks.append(str(part.get("text") or ""))
            function_call = part.get("functionCall") or {}
            if isinstance(function_call, dict) and function_call.get("name"):
                tool_calls.append(
                    ModelToolCall(
                        id=f"gemini_call_{index}",
                        name=str(function_call.get("name") or ""),
                        arguments=function_call.get("args", {})
                        if isinstance(function_call.get("args"), dict)
                        else {},
                    )
                )

        usage = data.get("usageMetadata", {}) if isinstance(data.get("usageMetadata"), dict) else {}
        finish_reason = first.get("finishReason")
        return {
            "text": "\n".join(block.strip() for block in text_blocks if block.strip()).strip(),
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "stop_reason": finish_reason,
            "usage": usage,
            "response_id": data.get("responseId"),
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

        candidates = parsed.get("candidates", []) or []
        if candidates and isinstance(candidates[0], dict):
            first = candidates[0]
            content = first.get("content") or {}
            parts = content.get("parts") or []
            text_delta = ""
            for index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                if part.get("text"):
                    block = str(part.get("text") or "")
                    state.text_parts.append(block)
                    text_delta += block
                function_call = part.get("functionCall") or {}
                if isinstance(function_call, dict) and function_call.get("name"):
                    state.tool_call_buffers[index] = {
                        "id": f"gemini_call_{index}",
                        "name": str(function_call.get("name") or ""),
                        "arguments": json.dumps(function_call.get("args", {}), ensure_ascii=False),
                    }
            state.finish_reason = first.get("finishReason") or state.finish_reason
            state.stop_reason = first.get("finishReason") or state.stop_reason
            return StreamChunkParseResult(text_delta=text_delta)

        usage = parsed.get("usageMetadata")
        if isinstance(usage, dict):
            state.usage = usage
        if parsed.get("responseId"):
            state.response_id = str(parsed.get("responseId"))
        return StreamChunkParseResult()

    def finalize_stream(
        self,
        config: ModelConfigRecord,
        state: StreamParseState,
    ) -> dict[str, Any]:
        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"gemini_call_{index}"),
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
                "finish_reason": state.finish_reason,
                "usage": state.usage,
            },
        }

    def _build_contents(self, request: ModelInvocationRequest) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for message in request.structured_messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                role = "user"
            elif message.role == "assistant":
                role = "model"
            else:
                role = "user"
            contents.append(
                {
                    "role": role,
                    "parts": self._serialize_parts(message),
                }
            )
        return contents

    def _serialize_parts(self, message: UnifiedMessage) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for part in message.parts:
            serialized = self._serialize_part(part)
            if serialized is not None:
                parts.append(serialized)
        return parts or [{"text": ""}]

    def _serialize_part(self, part: ContentPart) -> dict[str, Any] | None:
        if part.type == "text":
            return {"text": part.text or ""}
        if part.type == "image_base64" and part.data_base64:
            return {
                "inlineData": {
                    "mimeType": part.mime_type or "image/jpeg",
                    "data": part.data_base64,
                }
            }
        if part.type == "image_url" and part.url:
            if part.url.startswith("data:") and ";base64," in part.url:
                prefix, data_base64 = part.url.split(";base64,", 1)
                mime_type = prefix.split("data:", 1)[-1] or "image/jpeg"
                return {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": data_base64,
                    }
                }
            return {"text": f"Image URL: {part.url}"}
        if part.type == "tool_result":
            payload = part.payload if isinstance(part.payload, dict) else {}
            return {
                "functionResponse": {
                    "name": part.tool_name or "tool_result",
                    "response": payload or {"content": part.text or ""},
                }
            }
        if part.type == "file":
            label = part.file_name or "file"
            return {"text": f"[file:{label}]"}
        return None

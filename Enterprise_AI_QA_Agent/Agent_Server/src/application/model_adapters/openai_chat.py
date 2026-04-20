from __future__ import annotations

import json
from typing import Any

from src.application.model_adapters.base import (
    AdapterDescriptor,
    ProviderAdapter,
    StreamChunkParseResult,
    StreamParseState,
)
from src.application.model_adapters.provider_profiles import resolve_provider_profile
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall


class OpenAIChatCompletionsAdapter(ProviderAdapter):
    adapter_key = "openai_chat_completions"

    def matches(self, config: ModelConfigRecord) -> bool:
        return config.transport == "openai_chat_completions"

    def describe(self, config: ModelConfigRecord) -> AdapterDescriptor:
        provider_key = resolve_provider_profile(config.provider).provider
        return AdapterDescriptor(
            name=provider_key,
            protocol="openai_chat_completions",
            chat_path="/chat/completions",
            supports_parallel_tool_calls=config.capabilities.parallel_tool_calls,
        )

    def build_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model_id,
            "messages": self._build_messages(request.system_prompt, request, tool_name_map),
            "max_tokens": config.max_tokens,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": (tool_name_map or {}).get(item["name"], item["name"]),
                        "description": item["description"],
                        "parameters": item["input_schema"],
                    },
                }
                for item in request.tools
            ]
            payload["tool_choice"] = "auto"
            if self.describe(config).supports_parallel_tool_calls:
                payload["parallel_tool_calls"] = False
        return payload

    def parse_response(self, config: ModelConfigRecord, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", []) or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        text = self.extract_openai_message_text(message)
        tool_calls = self.extract_openai_tool_calls(message)
        return {
            "text": text,
            "tool_calls": tool_calls,
            "finish_reason": choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None,
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
        if chunk == "[DONE]":
            return StreamChunkParseResult(should_stop=True)

        parsed = self.parse_json_chunk(chunk)
        if parsed is None:
            return StreamChunkParseResult()

        state.response_id = str(parsed.get("id") or state.response_id)
        usage_payload = parsed.get("usage")
        if isinstance(usage_payload, dict) and usage_payload:
            state.usage = usage_payload

        choices = parsed.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return StreamChunkParseResult()
        choice = choices[0]
        state.finish_reason = choice.get("finish_reason") or state.finish_reason
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            return StreamChunkParseResult()

        text_delta = ""
        content = delta.get("content")
        if isinstance(content, str) and content:
            state.text_parts.append(content)
            text_delta = content

        for tool_call in delta.get("tool_calls", []) or []:
            if not isinstance(tool_call, dict):
                continue
            index = int(tool_call.get("index", len(state.tool_call_buffers)))
            buffer = state.tool_call_buffers.setdefault(
                index,
                {"id": "", "name": "", "arguments": ""},
            )
            if tool_call.get("id"):
                buffer["id"] = str(tool_call["id"])
            function_block = tool_call.get("function") or {}
            if isinstance(function_block, dict):
                if function_block.get("name"):
                    buffer["name"] = str(function_block["name"])
                if function_block.get("arguments"):
                    buffer["arguments"] += str(function_block["arguments"])

        return StreamChunkParseResult(text_delta=text_delta)

    def finalize_stream(
        self,
        config: ModelConfigRecord,
        state: StreamParseState,
    ) -> dict[str, Any]:
        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"call_{index}"),
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

    def _build_messages(
        self,
        system_prompt: str,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if system_prompt:
            payload.append({"role": "system", "content": system_prompt})
        for item in request.structured_messages:
            role = item.role
            if role == "tool":
                tool_part = next((part for part in item.parts if part.type == "tool_result"), None)
                tool_name = tool_part.tool_name if tool_part else ""
                payload.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "name": (tool_name_map or {}).get(tool_name, tool_name),
                        "content": self._serialize_tool_result(tool_part),
                    }
                )
                continue
            payload.append({"role": role, "content": self._serialize_parts(item)})
        return payload

    def _serialize_parts(self, message: UnifiedMessage) -> str | list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in message.parts:
            block = self._serialize_part(part)
            if block is not None:
                blocks.append(block)
        if not blocks:
            return ""
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            return str(blocks[0].get("text") or "")
        return blocks

    def _serialize_part(self, part: ContentPart) -> dict[str, Any] | None:
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

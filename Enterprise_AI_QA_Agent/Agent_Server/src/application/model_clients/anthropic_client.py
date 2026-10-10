from __future__ import annotations

from src.application.model_clients.request_mapping import map_model_request

from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from src.application.model_clients.base import (
    ProviderClientError,
    TextDeltaHandler,
    build_tool_name_map,
    parse_tool_arguments,
    remap_tool_calls,
    response_to_dict,
    serialize_tool_result_text,
)
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall


class AnthropicMessagesClient:
    """Messages API client backed by the official anthropic SDK."""

    client_key = "anthropic_messages"

    def __init__(self, *, timeout_seconds: float = 300.0, max_retries: int = 2) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]:
        tool_name_map = build_tool_name_map(request.tools)
        params = self._build_params(config, request, tool_name_map)
        try:
            async with self._make_client(config, api_key) as client:
                response = await client.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise self._wrap_status_error(exc) from exc
        except anthropic.APIError as exc:
            raise ProviderClientError(str(exc)) from exc
        parsed = self._parse_response(config, response_to_dict(response))
        parsed["tool_calls"] = remap_tool_calls(parsed["tool_calls"], tool_name_map)
        return parsed

    async def stream(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
        on_text_delta: TextDeltaHandler,
    ) -> dict[str, Any]:
        tool_name_map = build_tool_name_map(request.tools)
        params = self._build_params(config, request, tool_name_map)
        params["stream"] = True

        response_id = ""
        stop_reason: str | None = None
        message_stop_received = False
        usage: dict[str, Any] = {}
        text_parts: list[str] = []
        tool_call_buffers: dict[int, dict[str, Any]] = {}

        try:
            async with self._make_client(config, api_key) as client:
                stream = await client.messages.create(**params)
                async for event in stream:
                    data = response_to_dict(event)
                    event_type = str(data.get("type") or "")
                    if event_type == "message_stop":
                        message_stop_received = True
                        continue
                    if event_type == "message_start":
                        message = data.get("message") or {}
                        if isinstance(message, dict):
                            response_id = str(message.get("id") or response_id)
                            usage_payload = message.get("usage")
                            if isinstance(usage_payload, dict):
                                usage = usage_payload
                        continue
                    if event_type == "content_block_start":
                        index = int(data.get("index", 0))
                        content_block = data.get("content_block") or {}
                        if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                            tool_call_buffers[index] = {
                                "id": str(content_block.get("id") or f"tool_{index}"),
                                "name": str(content_block.get("name") or ""),
                                "arguments": "",
                            }
                        continue
                    if event_type == "content_block_delta":
                        index = int(data.get("index", 0))
                        delta = data.get("delta") or {}
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text_delta":
                            text = str(delta.get("text") or "")
                            if text:
                                text_parts.append(text)
                                await on_text_delta(text)
                            continue
                        if delta.get("type") == "input_json_delta":
                            buffer = tool_call_buffers.setdefault(
                                index,
                                {"id": f"tool_{index}", "name": "", "arguments": ""},
                            )
                            buffer["arguments"] += str(delta.get("partial_json") or "")
                        continue
                    if event_type == "message_delta":
                        delta = data.get("delta") or {}
                        if isinstance(delta, dict):
                            stop_reason = delta.get("stop_reason") or stop_reason
                        usage_payload = data.get("usage")
                        if isinstance(usage_payload, dict) and usage_payload:
                            # MessageDeltaUsage expands omitted optional fields
                            # to None. They must not erase message_start facts;
                            # an explicit 0 is still an observed cumulative value.
                            usage = {**usage, **{key: value for key, value in usage_payload.items()
                                if value is not None}}
        except anthropic.APIStatusError as exc:
            raise self._wrap_status_error(exc) from exc
        except anthropic.APIError as exc:
            raise ProviderClientError(str(exc)) from exc

        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"tool_{index}"),
                name=str(item.get("name") or ""),
                arguments=parse_tool_arguments(item.get("arguments", "")),
            )
            for index, item in sorted(tool_call_buffers.items(), key=lambda pair: pair[0])
        ]
        return {
            "text": "".join(text_parts).strip(),
            "tool_calls": remap_tool_calls(tool_calls, tool_name_map),
            "stream_terminal_received": message_stop_received and bool(stop_reason),
            "finish_reason": None,
            "stop_reason": stop_reason,
            "usage": usage,
            "response_id": response_id,
            "raw_response": {
                "mode": "stream",
                "provider": config.provider,
                "stream_terminal_received": message_stop_received and bool(stop_reason),
                "stop_reason": stop_reason,
                "usage": usage,
            },
        }

    def _make_client(self, config: ModelConfigRecord, api_key: str) -> AsyncAnthropic:
        return AsyncAnthropic(
            api_key=api_key,
            base_url=config.api_base_url,
            default_headers=dict(config.extra_headers) or None,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )

    def _wrap_status_error(self, exc: anthropic.APIStatusError) -> ProviderClientError:
        body = ""
        try:
            body = exc.response.text or ""
        except Exception:
            body = ""
        return ProviderClientError(
            str(exc),
            status_code=exc.status_code,
            response_body=body,
        )

    def _build_params(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": config.model_id,
            "max_tokens": config.max_output_tokens,
            "system": request.system_prompt,
            "messages": self._build_messages(request, tool_name_map),
        }
        if config.temperature is not None:
            params["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            params["tools"] = [
                {
                    "name": (tool_name_map or {}).get(item["name"], item["name"]),
                    "description": item["description"],
                    "input_schema": item["input_schema"],
                }
                for item in request.tools
            ]
        params.update(map_model_request(config, request).parameters)
        return params

    def _parse_response(self, config: ModelConfigRecord, data: dict[str, Any]) -> dict[str, Any]:
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
            "usage": data.get("usage") or {},
            "response_id": data.get("id"),
            "raw_response": data,
        }

    def _build_messages(
        self,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
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
                                "content": serialize_tool_result_text(tool_part),
                            }
                        ],
                    }
                )
                continue
            content_blocks = self._serialize_parts(item)
            if role == "assistant" and item.tool_calls:
                for tool_call in item.tool_calls:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.id,
                            "name": (tool_name_map or {}).get(tool_call.name, tool_call.name),
                            "input": tool_call.arguments or {},
                        }
                    )
            payload.append({"role": role, "content": content_blocks})
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
            return {"type": "text", "text": serialize_tool_result_text(part)}
        return None

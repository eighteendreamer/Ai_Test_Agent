from __future__ import annotations

from src.application.model_clients.request_mapping import map_model_request

import json
from typing import Any

import openai
from openai import AsyncOpenAI

from src.application.model_clients.base import (
    ProviderClientError,
    TextDeltaHandler,
    build_tool_name_map,
    parse_tool_arguments,
    remap_tool_calls,
    response_to_dict,
    serialize_tool_result_text,
)
from src.application.model_clients.provider_profiles import resolve_provider_profile
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall


class OpenAIChatClient:
    """Chat-completions client for OpenAI and OpenAI-compatible providers."""

    client_key = "openai_chat_completions"

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
                response = await client.chat.completions.create(**params)
        except openai.APIStatusError as exc:
            raise self._wrap_status_error(exc) from exc
        except openai.APIError as exc:
            raise ProviderClientError(str(exc)) from exc
        parsed = self._parse_response(response_to_dict(response))
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
        params["stream_options"] = {"include_usage": True}

        response_id = ""
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        text_parts: list[str] = []
        tool_call_buffers: dict[int, dict[str, Any]] = {}

        try:
            async with self._make_client(config, api_key) as client:
                stream = await client.chat.completions.create(**params)
                async for chunk in stream:
                    data = response_to_dict(chunk)
                    response_id = str(data.get("id") or response_id)
                    usage_payload = data.get("usage")
                    if isinstance(usage_payload, dict) and usage_payload:
                        usage = usage_payload
                    choices = data.get("choices") or []
                    if not choices or not isinstance(choices[0], dict):
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        text_parts.append(content)
                        await on_text_delta(content)
                    for tool_call in delta.get("tool_calls") or []:
                        if not isinstance(tool_call, dict):
                            continue
                        index = int(tool_call.get("index", len(tool_call_buffers)))
                        buffer = tool_call_buffers.setdefault(
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
        except openai.APIStatusError as exc:
            raise self._wrap_status_error(exc) from exc
        except openai.APIError as exc:
            raise ProviderClientError(str(exc)) from exc

        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"call_{index}"),
                name=str(item.get("name") or ""),
                arguments=parse_tool_arguments(item.get("arguments", "")),
            )
            for index, item in sorted(tool_call_buffers.items(), key=lambda pair: pair[0])
        ]
        return {
            "text": "".join(text_parts).strip(),
            "tool_calls": remap_tool_calls(tool_calls, tool_name_map),
            "stream_terminal_received": bool(finish_reason),
            "finish_reason": finish_reason,
            "stop_reason": None,
            "usage": usage,
            "response_id": response_id,
            "raw_response": {
                "mode": "stream",
                "provider": config.provider,
                "stream_terminal_received": bool(finish_reason),
                "finish_reason": finish_reason,
                "usage": usage,
            },
        }

    def _make_client(self, config: ModelConfigRecord, api_key: str) -> AsyncOpenAI:
        default_headers = dict(config.extra_headers)
        if resolve_provider_profile(config.provider).provider == "github":
            default_headers.setdefault("Copilot-Integration-Id", "vscode-chat")
        return AsyncOpenAI(
            api_key=api_key,
            base_url=config.api_base_url,
            default_headers=default_headers or None,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )

    def _wrap_status_error(self, exc: openai.APIStatusError) -> ProviderClientError:
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
            "messages": self._build_messages(request.system_prompt, request, tool_name_map),
            "max_tokens": config.max_output_tokens,
        }
        if config.temperature is not None:
            params["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            params["tools"] = [
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
            params["tool_choice"] = "auto"
            if config.capabilities.parallel_tool_calls:
                params["parallel_tool_calls"] = True
        mapped = map_model_request(config, request).parameters
        if config.request_profile and config.request_profile.chat_token_parameter:
            params.pop("max_tokens", None)
        params.update(mapped)
        return params

    def _parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", []) or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        return {
            "text": self._extract_message_text(message),
            "tool_calls": self._extract_tool_calls(message),
            "finish_reason": choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None,
            "stop_reason": data.get("stop_reason"),
            "usage": data.get("usage") or {},
            "response_id": data.get("id"),
            "raw_response": data,
        }

    def _extract_message_text(self, message: Any) -> str:
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

    def _extract_tool_calls(self, message: Any) -> list[ModelToolCall]:
        if not isinstance(message, dict):
            return []
        raw_tool_calls = message.get("tool_calls") or []
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
                    arguments=parse_tool_arguments(function_block.get("arguments", "")),
                )
            )
        return output

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
                        "tool_call_id": item.tool_call_id,
                        "name": (tool_name_map or {}).get(tool_name, tool_name),
                        "content": serialize_tool_result_text(tool_part),
                    }
                )
                continue
            message_payload: dict[str, Any] = {
                "role": role,
                "content": self._serialize_parts(item),
            }
            if role == "assistant" and item.tool_calls:
                message_payload["tool_calls"] = self._serialize_assistant_tool_calls(
                    item.tool_calls,
                    tool_name_map,
                )
            payload.append(message_payload)
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
            return {"type": "text", "text": serialize_tool_result_text(part)}
        return None

    def _serialize_assistant_tool_calls(
        self,
        tool_calls: list[ModelToolCall],
        tool_name_map: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "type": "function",
                "function": {
                    "name": (tool_name_map or {}).get(item.name, item.name),
                    "arguments": json.dumps(item.arguments or {}, ensure_ascii=False),
                },
            }
            for item in tool_calls
        ]

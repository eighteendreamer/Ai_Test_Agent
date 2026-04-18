from __future__ import annotations

import contextvars
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx

from src.application.model_compatibility import ModelCompatibilityLayer
from src.core.config import Settings
from src.registry.models import ModelRegistry
from src.runtime.execution_logging import summarize_messages, truncate_text
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult
from src.schemas.tool_runtime import ModelToolCall


StreamChunkHandler = Callable[[str], Awaitable[None]]
_stream_handler_var: contextvars.ContextVar[StreamChunkHandler | None] = contextvars.ContextVar(
    "model_stream_handler",
    default=None,
)


class ModelRuntimeService:
    def __init__(self, model_registry: ModelRegistry, settings: Settings) -> None:
        self._model_registry = model_registry
        self._settings = settings
        self._compatibility = ModelCompatibilityLayer()

    async def invoke(
        self,
        model_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        try:
            config = self._model_registry.get_runtime_config(model_key)
        except KeyError:
            return ModelInvocationResult(
                text=(
                    f"No active model configuration found for '{model_key}'. "
                    "Activate a row in the MySQL model config table before running this turn."
                ),
                request_payload={
                    "requested_model_key": model_key,
                    "messages": summarize_messages(request.messages),
                },
                response_summary={"mode": "missing_active_model", "model_key": model_key},
                raw_response={"mode": "missing_active_model", "model_key": model_key},
            )
        api_key = self._resolve_api_key(config)

        if not api_key:
            return ModelInvocationResult(
                text=(
                    f"Model '{config.name}' is active in the database but has no usable API key. "
                    f"Configure `api_key` or set environment variable `{config.api_key_env}`."
                ),
                request_payload=self._summarize_request(config, request),
                response_summary={"mode": "missing_api_key", "model_key": config.key},
                raw_response={"mode": "missing_api_key", "model_key": config.key},
            )

        profile = self._compatibility.resolve_profile(config)
        if profile.protocol == "anthropic_messages":
            return await self._invoke_anthropic(config, api_key, request)
        return await self._invoke_openai_compatible(config, api_key, request)

    def _resolve_api_key(self, config: ModelConfigRecord) -> str | None:
        if config.api_key:
            return config.api_key
        if config.api_key_env:
            return os.getenv(config.api_key_env)
        return None

    @asynccontextmanager
    async def stream_handler(self, handler: StreamChunkHandler | None):
        token = _stream_handler_var.set(handler)
        try:
            yield
        finally:
            _stream_handler_var.reset(token)

    async def _invoke_anthropic(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        url = self._compatibility.build_url(config)
        headers = self._compatibility.build_headers(config, api_key)
        payload = self._compatibility.build_request(config, request)

        try:
            if _stream_handler_var.get() is not None:
                parsed = await self._stream_anthropic(config, request, url, headers, payload)
            else:
                async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                parsed = self._compatibility.parse_response(config, data)
        except httpx.HTTPError as exc:
            return self._http_error_result(config, request, exc)

        return ModelInvocationResult(
            text=parsed["text"] or "",
            tool_calls=parsed["tool_calls"],
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": "ok",
                "provider": config.provider,
                "provider_profile": self._compatibility.resolve_profile(config).name,
                "transport": self._compatibility.resolve_profile(config).protocol,
                "response_id": parsed["response_id"],
                "stop_reason": parsed["stop_reason"],
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "tool_call_names": [item.name for item in parsed["tool_calls"]],
                "content_preview": truncate_text(parsed["text"] or "", 180),
            },
            raw_response=parsed["raw_response"],
        )

    async def _invoke_openai_compatible(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        profile = self._compatibility.resolve_profile(config)
        url = self._compatibility.build_url(config)
        headers = self._compatibility.build_headers(config, api_key)
        tool_name_map = self._compatibility.build_tool_name_map(request.tools)
        payload = self._compatibility.build_request(
            config,
            request,
            tool_name_map=tool_name_map,
        )

        try:
            if _stream_handler_var.get() is not None:
                parsed = await self._stream_openai_compatible(config, request, url, headers, payload)
            else:
                async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                parsed = self._compatibility.parse_response(config, data)
        except httpx.HTTPError as exc:
            return self._http_error_result(config, request, exc)

        parsed["tool_calls"] = self._compatibility.remap_tool_calls(
            parsed["tool_calls"],
            tool_name_map,
        )

        return ModelInvocationResult(
            text=parsed["text"],
            tool_calls=parsed["tool_calls"],
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": "ok",
                "provider": config.provider,
                "provider_profile": profile.name,
                "transport": profile.protocol,
                "response_id": parsed["response_id"],
                "finish_reason": parsed["finish_reason"],
                "stop_reason": parsed["stop_reason"],
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "tool_call_names": [item.name for item in parsed["tool_calls"]],
                "content_preview": truncate_text(parsed["text"], 180),
            },
            raw_response=parsed["raw_response"],
        )

    def _http_error_result(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        exc: httpx.HTTPError,
    ) -> ModelInvocationResult:
        response_body = ""
        status_code = None
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            status_code = exc.response.status_code
            try:
                response_body = truncate_text(exc.response.text or "", 400)
            except Exception:
                response_body = ""
        return ModelInvocationResult(
            text=(
                f"Model invocation failed for '{config.name}' via provider '{config.provider}': "
                f"{truncate_text(str(exc), 180)}"
                + (f" | response={response_body}" if response_body else "")
            ),
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": "http_error",
                "provider": config.provider,
                "provider_profile": self._compatibility.resolve_profile(config).name,
                "transport": self._compatibility.resolve_profile(config).protocol,
                "error_type": exc.__class__.__name__,
                "status_code": status_code,
                "error": truncate_text(str(exc), 180),
                "response_body": response_body,
            },
            raw_response={
                "mode": "http_error",
                "error": str(exc),
                "status_code": status_code,
                "response_body": response_body,
            },
        )

    def _summarize_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]:
        return {
            "model_key": config.key,
            "model_id": config.model_id,
            "provider": config.provider,
            "provider_profile": self._compatibility.resolve_profile(config).name,
            "transport": self._compatibility.resolve_profile(config).protocol,
            "api_base_url": config.api_base_url,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "tool_count": len(request.tools),
            "tool_names": [item.get("name", "") for item in request.tools],
            "system_prompt_preview": truncate_text(request.system_prompt, 180),
            "messages": summarize_messages(request.messages),
        }

    async def _emit_stream_chunk(self, chunk: str) -> None:
        handler = _stream_handler_var.get()
        if handler is None or not chunk:
            return
        await handler(chunk)

    async def _stream_openai_compatible(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        stream_payload = {
            **payload,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        response_id = ""
        finish_reason = None
        usage: dict[str, Any] = {}
        text_parts: list[str] = []
        tool_call_buffers: dict[int, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
            async with client.stream("POST", url, headers=headers, json=stream_payload) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_chunk = line[5:].strip()
                    if data_chunk == "[DONE]":
                        break

                    parsed = self._parse_json_chunk(data_chunk)
                    if parsed is None:
                        continue

                    response_id = str(parsed.get("id") or response_id)
                    usage_payload = parsed.get("usage")
                    if isinstance(usage_payload, dict) and usage_payload:
                        usage = usage_payload

                    choices = parsed.get("choices") or []
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
                        await self._emit_stream_chunk(content)

                    for tool_call in delta.get("tool_calls", []) or []:
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

        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"call_{index}"),
                name=str(item.get("name") or ""),
                arguments=self._compatibility._parse_tool_arguments(item.get("arguments", "")),
            )
            for index, item in sorted(tool_call_buffers.items(), key=lambda pair: pair[0])
        ]

        return {
            "text": "".join(text_parts).strip(),
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "stop_reason": None,
            "usage": usage,
            "response_id": response_id,
            "raw_response": {
                "mode": "stream",
                "provider": config.provider,
                "finish_reason": finish_reason,
                "usage": usage,
            },
        }

    async def _stream_anthropic(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        stream_payload = {
            **payload,
            "stream": True,
        }
        response_id = ""
        stop_reason = None
        usage: dict[str, Any] = {}
        text_parts: list[str] = []
        tool_call_buffers: dict[int, dict[str, Any]] = {}
        current_tool_index: int | None = None

        async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
            async with client.stream("POST", url, headers=headers, json=stream_payload) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data_chunk = line[5:].strip()
                    parsed = self._parse_json_chunk(data_chunk)
                    if parsed is None:
                        continue

                    event_type = str(parsed.get("type") or "")
                    if event_type == "message_start":
                        message = parsed.get("message") or {}
                        if isinstance(message, dict):
                            response_id = str(message.get("id") or response_id)
                            usage_payload = message.get("usage")
                            if isinstance(usage_payload, dict):
                                usage = usage_payload
                    elif event_type == "content_block_start":
                        index = int(parsed.get("index", 0))
                        content_block = parsed.get("content_block") or {}
                        if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                            current_tool_index = index
                            tool_call_buffers[index] = {
                                "id": str(content_block.get("id") or f"tool_{index}"),
                                "name": str(content_block.get("name") or ""),
                                "arguments": "",
                            }
                    elif event_type == "content_block_delta":
                        index = int(parsed.get("index", current_tool_index or 0))
                        delta = parsed.get("delta") or {}
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text_delta":
                            text = str(delta.get("text") or "")
                            if text:
                                text_parts.append(text)
                                await self._emit_stream_chunk(text)
                        elif delta.get("type") == "input_json_delta":
                            buffer = tool_call_buffers.setdefault(
                                index,
                                {"id": f"tool_{index}", "name": "", "arguments": ""},
                            )
                            buffer["arguments"] += str(delta.get("partial_json") or "")
                    elif event_type == "message_delta":
                        delta = parsed.get("delta") or {}
                        if isinstance(delta, dict):
                            stop_reason = delta.get("stop_reason") or stop_reason
                        usage_payload = parsed.get("usage")
                        if isinstance(usage_payload, dict) and usage_payload:
                            usage = usage_payload

        tool_calls = [
            ModelToolCall(
                id=str(item.get("id") or f"tool_{index}"),
                name=str(item.get("name") or ""),
                arguments=self._compatibility._parse_tool_arguments(item.get("arguments", "")),
            )
            for index, item in sorted(tool_call_buffers.items(), key=lambda pair: pair[0])
        ]

        return {
            "text": "".join(text_parts).strip(),
            "tool_calls": tool_calls,
            "finish_reason": None,
            "stop_reason": stop_reason,
            "usage": usage,
            "response_id": response_id,
            "raw_response": {
                "mode": "stream",
                "provider": config.provider,
                "stop_reason": stop_reason,
                "usage": usage,
            },
        }

    def _parse_json_chunk(self, data_chunk: str) -> dict[str, Any] | None:
        try:
            loaded = json.loads(data_chunk)
        except Exception:
            return None
        return loaded if isinstance(loaded, dict) else None

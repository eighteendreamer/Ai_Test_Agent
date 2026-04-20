from __future__ import annotations

import contextvars
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx

from src.application.model_adapters import AdapterRegistry, build_default_adapter_registry
from src.application.model_adapters.base import ProviderAdapter
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
    def __init__(
        self,
        model_registry: ModelRegistry,
        settings: Settings,
        adapter_registry: AdapterRegistry | None = None,
    ) -> None:
        self._model_registry = model_registry
        self._settings = settings
        self._adapter_registry = adapter_registry or build_default_adapter_registry()
        self._compatibility = ModelCompatibilityLayer(adapter_registry=self._adapter_registry)

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

        adapter = self._adapter_registry.resolve(config)
        return await self._invoke_with_adapter(adapter, config, api_key, request)

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

    async def _invoke_with_adapter(
        self,
        adapter: ProviderAdapter,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        descriptor = adapter.describe(config)
        url = adapter.build_url(config)
        headers = adapter.build_headers(config, api_key)
        tool_name_map = adapter.build_tool_name_map(request.tools)
        payload = adapter.build_request(config, request, tool_name_map=tool_name_map)

        try:
            if _stream_handler_var.get() is not None:
                parsed = await self._stream_with_adapter(adapter, config, url, headers, payload)
            else:
                async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                parsed = adapter.parse_response(config, data)
        except httpx.HTTPError as exc:
            return self._http_error_result(config, request, exc)

        parsed["tool_calls"] = adapter.remap_tool_calls(
            parsed["tool_calls"],
            tool_name_map,
        )

        return ModelInvocationResult(
            text=parsed["text"] or "",
            tool_calls=parsed["tool_calls"],
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": "ok",
                "provider": config.provider,
                "provider_profile": descriptor.name,
                "transport": descriptor.protocol,
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
            "system_prompt_section_count": len(request.system_prompt_sections),
            "system_prompt_section_keys": [item.key for item in request.system_prompt_sections],
            "runtime_message_section_count": len(request.runtime_message_sections),
            "runtime_message_section_keys": [item.key for item in request.runtime_message_sections],
            "messages": summarize_messages(request.messages),
        }

    async def _emit_stream_chunk(self, chunk: str) -> None:
        handler = _stream_handler_var.get()
        if handler is None or not chunk:
            return
        await handler(chunk)

    async def _stream_with_adapter(
        self,
        adapter,
        config: ModelConfigRecord,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        descriptor = adapter.describe(config)
        stream_payload = {**payload, "stream": True}
        if descriptor.protocol == "openai_chat_completions":
            stream_payload["stream_options"] = {"include_usage": True}
        state = adapter.create_stream_state()

        async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
            async with client.stream("POST", url, headers=headers, json=stream_payload) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if descriptor.protocol == "anthropic_messages":
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                    elif not line or not line.startswith("data:"):
                        continue
                    data_chunk = line[5:].strip()
                    parsed = adapter.parse_stream_chunk(config, state, data_chunk)
                    if parsed.text_delta:
                        await self._emit_stream_chunk(parsed.text_delta)
                    if parsed.should_stop:
                        break

        return adapter.finalize_stream(config, state)

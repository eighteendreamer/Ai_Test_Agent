from __future__ import annotations

import os

import httpx

from src.application.model_compatibility import ModelCompatibilityLayer
from src.core.config import Settings
from src.registry.models import ModelRegistry
from src.runtime.execution_logging import summarize_messages, truncate_text
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult


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
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            return self._http_error_result(config, request, exc)

        parsed = self._compatibility.parse_response(config, data)
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
        payload = self._compatibility.build_request(config, request)

        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            return self._http_error_result(config, request, exc)

        parsed = self._compatibility.parse_response(config, data)
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
        return ModelInvocationResult(
            text=(
                f"Model invocation failed for '{config.name}' via provider '{config.provider}': "
                f"{truncate_text(str(exc), 180)}"
            ),
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": "http_error",
                "provider": config.provider,
                "provider_profile": self._compatibility.resolve_profile(config).name,
                "transport": self._compatibility.resolve_profile(config).protocol,
                "error_type": exc.__class__.__name__,
                "error": truncate_text(str(exc), 180),
            },
            raw_response={"mode": "http_error", "error": str(exc)},
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

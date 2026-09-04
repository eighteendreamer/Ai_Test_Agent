from __future__ import annotations

import contextvars
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from src.application.model_clients import ProviderClient, ProviderClientError, resolve_client
from src.application.model_clients.provider_profiles import (
    normalize_transport,
    resolve_provider_profile,
)
from src.application.models.oauth_token_service import OAuthTokenService
from src.core.config import Settings
from src.registry.models import ModelRegistry
from src.runtime.execution_logging import summarize_messages, truncate_text
from src.schemas.model_config import (
    ContentPart,
    ModelConfigRecord,
    ModelInvocationRequest,
    ModelInvocationResult,
    UnifiedMessage,
)
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
        oauth_token_service: OAuthTokenService | None = None,
    ) -> None:
        self._model_registry = model_registry
        self._settings = settings
        self._oauth_token_service = oauth_token_service

    def get_default_model_config(self) -> ModelConfigRecord | None:
        try:
            return self._model_registry.get_default_runtime_config()
        except KeyError:
            return None

    def get_model_config(self, model_key: str) -> ModelConfigRecord | None:
        try:
            return self._model_registry.get_runtime_config(model_key)
        except KeyError:
            return None

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
        try:
            api_key = await self._resolve_auth_token(config)
        except Exception as exc:
            return ModelInvocationResult(
                text=(
                    f"Model '{config.name}' authentication failed: {exc}"
                ),
                request_payload=self._summarize_request(config, request),
                response_summary={"mode": "auth_error", "model_key": config.key},
                raw_response={"mode": "auth_error", "model_key": config.key},
            )

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

        client = resolve_client(
            config,
            timeout_seconds=self._settings.model.llm_request_timeout_seconds,
        )
        return await self._invoke_with_client(client, config, api_key, request)

    async def _resolve_auth_token(self, config: ModelConfigRecord) -> str | None:
        """Return the bearer token to use for this model config.

        For oauth2 auth_type the token is obtained (and cached) via the
        OAuthTokenService. For api_key auth_type the existing static key
        resolution path is used.
        """
        if config.auth_type == "oauth2":
            if self._oauth_token_service is None:
                raise RuntimeError(
                    "OAuthTokenService is not configured. "
                    "Inject an OAuthTokenService instance into ModelRuntimeService."
                )
            return await self._oauth_token_service.get_token(config)
        return self._resolve_api_key(config)

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

    async def _invoke_with_client(
        self,
        client: ProviderClient,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        request = self._sanitize_request_for_provider(config, request)
        effective_request = request
        tool_fallback_used = False

        try:
            parsed = await self._send_with_client(client, config, api_key, request)
        except ProviderClientError as exc:
            if self._should_retry_google_without_tools(config, request, exc):
                effective_request = self._build_google_tool_free_request(request)
                tool_fallback_used = True
                try:
                    parsed = await self._send_with_client(client, config, api_key, effective_request)
                except ProviderClientError as retry_exc:
                    return self._http_error_result(config, request, retry_exc)
            else:
                return self._http_error_result(config, request, exc)

        request_payload = self._summarize_request(config, effective_request)
        if tool_fallback_used:
            request_payload["original_tool_count"] = len(request.tools)
            request_payload["original_tool_names"] = [item.get("name", "") for item in request.tools]

        return ModelInvocationResult(
            text=parsed["text"] or "",
            tool_calls=parsed["tool_calls"],
            request_payload=request_payload,
            response_summary={
                "mode": "ok",
                "provider": config.provider,
                "provider_profile": self._provider_profile_name(config),
                "transport": self._resolved_transport(config),
                "response_id": parsed["response_id"],
                "finish_reason": parsed["finish_reason"],
                "stop_reason": parsed["stop_reason"],
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "tool_call_names": [item.name for item in parsed["tool_calls"]],
                "content_preview": truncate_text(parsed["text"], 180),
                "tool_fallback": "google_retry_without_tools" if tool_fallback_used else "",
            },
            raw_response=parsed["raw_response"],
        )

    async def _send_with_client(
        self,
        client: ProviderClient,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]:
        if _stream_handler_var.get() is not None:
            return await client.stream(config, api_key, request, self._emit_stream_chunk)
        return await client.invoke(config, api_key, request)

    def _provider_profile_name(self, config: ModelConfigRecord) -> str:
        return resolve_provider_profile(config.provider).provider

    def _resolved_transport(self, config: ModelConfigRecord) -> str:
        return normalize_transport(config.transport, provider=config.provider)

    def _should_retry_google_without_tools(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        exc: ProviderClientError,
    ) -> bool:
        if config.transport != "google_gemini_generate_content" or not request.tools:
            return False
        return exc.status_code == 400

    def _build_google_tool_free_request(
        self,
        request: ModelInvocationRequest,
    ) -> ModelInvocationRequest:
        filtered_structured_messages = []
        for message in request.structured_messages:
            if message.role == "tool":
                continue
            if message.role == "assistant" and message.tool_calls and not self._has_visible_message_content(message):
                continue
            filtered_structured_messages.append(
                message.model_copy(update={"tool_calls": []}) if message.tool_calls else message
            )

        filtered_messages: list[dict[str, Any]] = []
        for item in request.messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role == "tool":
                continue
            if role == "assistant" and item.get("tool_calls") and not self._has_visible_raw_message_content(item):
                continue
            if role == "assistant" and item.get("tool_calls"):
                filtered = dict(item)
                filtered["tool_calls"] = []
                filtered_messages.append(filtered)
                continue
            filtered_messages.append(item)

        return request.model_copy(
            update={
                "tools": [],
                "messages": filtered_messages,
                "structured_messages": filtered_structured_messages,
            }
        )

    def _sanitize_request_for_provider(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
    ) -> ModelInvocationRequest:
        """Normalize tool-call transcript history for strict OpenAI-style protocols."""
        if config.transport not in {"openai_chat_completions", "openai_responses"}:
            return request

        sanitized = self._sanitize_structured_tool_messages(request.structured_messages)
        if sanitized == request.structured_messages:
            return request
        return request.model_copy(
            update={
                "structured_messages": sanitized,
                "messages": self._raw_messages_from_structured(sanitized),
            }
        )

    def _sanitize_structured_tool_messages(
        self,
        messages: list[UnifiedMessage],
    ) -> list[UnifiedMessage]:
        sanitized: list[UnifiedMessage] = []
        transcript_notes: list[str] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role == "assistant" and message.tool_calls:
                tool_call_ids = {
                    str(item.id or "").strip()
                    for item in message.tool_calls
                    if str(item.id or "").strip()
                }
                following_tools: list[UnifiedMessage] = []
                next_index = index + 1
                while next_index < len(messages) and messages[next_index].role == "tool":
                    tool_message = messages[next_index]
                    if str(tool_message.tool_call_id or "") in tool_call_ids:
                        following_tools.append(tool_message)
                    else:
                        transcript_notes.append(self._summarize_tool_message(tool_message))
                    next_index += 1

                matched_ids = {
                    str(item.tool_call_id or "").strip()
                    for item in following_tools
                    if str(item.tool_call_id or "").strip()
                }
                if tool_call_ids and tool_call_ids <= matched_ids:
                    sanitized.append(message)
                    sanitized.extend(following_tools)
                else:
                    stripped = self._assistant_message_without_tool_calls(message)
                    if stripped is not None:
                        sanitized.append(stripped)
                    for tool_message in following_tools:
                        transcript_notes.append(self._summarize_tool_message(tool_message))
                    names = ", ".join(item.name for item in message.tool_calls if item.name)
                    transcript_notes.append(f"Omitted unresolved assistant tool call(s): {names or 'unknown'}.")
                index = next_index
                continue

            if message.role == "tool":
                transcript_notes.append(self._summarize_tool_message(message))
                index += 1
                continue

            sanitized.append(message)
            index += 1

        if transcript_notes:
            sanitized.append(
                UnifiedMessage(
                    role="user",
                    parts=[
                        ContentPart(
                            type="text",
                            text=(
                                "Runtime tool transcript summary for provider compatibility:\n"
                                + "\n".join(f"- {note}" for note in transcript_notes[:8])
                            ),
                        )
                    ],
                )
            )
        return sanitized

    def _assistant_message_without_tool_calls(self, message: UnifiedMessage) -> UnifiedMessage | None:
        if not self._has_visible_message_content(message):
            return None
        return message.model_copy(update={"tool_calls": []})

    def _summarize_tool_message(self, message: UnifiedMessage) -> str:
        tool_call_id = str(message.tool_call_id or "").strip() or "unknown"
        text_parts: list[str] = []
        tool_name = ""
        for part in message.parts:
            if part.tool_name and not tool_name:
                tool_name = part.tool_name
            if part.text:
                text_parts.append(part.text)
            elif part.payload:
                text_parts.append(json.dumps(part.payload, ensure_ascii=False))
        content = truncate_text(" ".join(text_parts).strip(), 220)
        return f"tool_call_id={tool_call_id}; tool={tool_name or 'unknown'}; {content}"

    def _raw_messages_from_structured(self, messages: list[UnifiedMessage]) -> list[dict[str, Any]]:
        raw_messages: list[dict[str, Any]] = []
        for message in messages:
            item: dict[str, Any] = {
                "role": message.role,
                "content": self._parts_to_text(message.parts),
            }
            if message.role == "tool":
                item["tool_call_id"] = message.tool_call_id or ""
                tool_part = next((part for part in message.parts if part.type == "tool_result"), None)
                if tool_part and tool_part.tool_name:
                    item["name"] = tool_part.tool_name
            if message.role == "assistant" and message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments or {}, ensure_ascii=False),
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            raw_messages.append(item)
        return raw_messages

    def _parts_to_text(self, parts: list[ContentPart]) -> str:
        text_parts: list[str] = []
        for part in parts:
            if part.text:
                text_parts.append(part.text)
            elif part.payload:
                text_parts.append(json.dumps(part.payload, ensure_ascii=False))
            elif part.url:
                text_parts.append(part.url)
            elif part.file_name:
                text_parts.append(f"[file:{part.file_name}]")
        return "\n".join(text_parts)

    def _has_visible_message_content(self, message: Any) -> bool:
        parts = getattr(message, "parts", []) or []
        for part in parts:
            part_type = str(getattr(part, "type", "") or "")
            if part_type != "text":
                return True
            if str(getattr(part, "text", "") or "").strip():
                return True
        return False

    def _has_visible_raw_message_content(self, item: dict[str, Any]) -> bool:
        content = item.get("content")
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            for entry in content:
                if isinstance(entry, str) and entry.strip():
                    return True
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("type") or "").strip().lower() in {"text", "output_text"}:
                    text_value = entry.get("text")
                    if isinstance(text_value, dict):
                        text_value = text_value.get("value", "")
                    if str(text_value or entry.get("content") or "").strip():
                        return True
                else:
                    return True
        if isinstance(content, dict):
            if str(content.get("type") or "").strip().lower() in {"text", "output_text"}:
                text_value = content.get("text")
                if isinstance(text_value, dict):
                    text_value = text_value.get("value", "")
                return bool(str(text_value or content.get("content") or "").strip())
            return True
        return False

    def _http_error_result(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        exc: ProviderClientError,
    ) -> ModelInvocationResult:
        response_body = truncate_text(exc.response_body or "", 400)
        status_code = exc.status_code
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
                "provider_profile": self._provider_profile_name(config),
                "transport": self._resolved_transport(config),
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
            "provider_profile": self._provider_profile_name(config),
            "transport": self._resolved_transport(config),
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

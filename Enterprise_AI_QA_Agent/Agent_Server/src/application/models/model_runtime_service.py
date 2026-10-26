from __future__ import annotations

import asyncio
import contextvars
import importlib.metadata
import json
import logging
import os
from contextlib import asynccontextmanager, nullcontext
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.application.model_clients import ProviderClient, ProviderClientError, resolve_client
from src.application.model_clients.completion import model_completion_status, model_response_mode, stream_completion_fields
from src.application.model_clients.http_observation import (
    current_provider_request_attempt_count,
    observe_provider_call,
)
from src.application.model_clients.request_mapping import map_model_request, UnsupportedModelRequest
from src.application.model_adapters.langchain_model_adapter import LangChainModelAdapter
from src.application.model_clients.provider_profiles import (
    normalize_transport,
    resolve_provider_profile,
)
from src.application.models.oauth_token_service import OAuthTokenService
from src.application.observability import LangSmithObservabilityAdapter
from src.application.observability.trace_context import TraceContext
from src.core.config import Settings
from src.registry.models import ModelRegistry
from src.runtime.execution_logging import summarize_messages, truncate_text
from src.runtime.execution_context import current_framework_owner
from src.schemas.framework_model_call import FrameworkModelCallRecord
from src.schemas.model_config import (
    ContentPart,
    ModelConfigRecord,
    ModelInvocationRequest,
    ModelInvocationResult,
    ModelTransportOptions,
    UnifiedMessage,
    model_invocation_parameters,
)
from src.schemas.tool_runtime import ModelToolCall


StreamChunkHandler = Callable[[str], Awaitable[None]]
_stream_handler_var: contextvars.ContextVar[StreamChunkHandler | None] = contextvars.ContextVar(
    "model_stream_handler",
    default=None,
)

logger = logging.getLogger(__name__)


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
        self._observability_service: LangSmithObservabilityAdapter | None = None
        self._framework_model_call_policy = None

    def set_observability_service(self, service: LangSmithObservabilityAdapter | None) -> None:
        self._observability_service = service

    def set_framework_model_call_policy(self, policy) -> None:
        self._framework_model_call_policy = policy

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

    def governed_transport_options(self) -> ModelTransportOptions:
        return ModelTransportOptions(timeout_seconds=self._settings.model.llm_request_timeout_seconds, max_retries=0)

    def get_invocation_runtime_parameters(self, model_key: str, *,
        transport_options: ModelTransportOptions | None = None) -> dict[str, Any]:
        config = self.get_model_config(model_key)
        if config is None:
            raise ValueError("没有可用模型，不能固定调用运行参数")
        adapter = self._uses_langchain(config)
        dependencies = (["openai", "langchain-openai", "langchain-core"] if adapter else ["openai"]) if config.transport in {
            "openai_chat_completions", "openai_responses"} else ["anthropic"] if config.transport == "anthropic_messages" else ["google-genai"]
        parameters = {"langchain_model_adapter_enabled": adapter,
            "llm_request_timeout_seconds": self._settings.model.llm_request_timeout_seconds,
            "sdk_retry_policy": "existing_provider_defaults",
            "dependency_versions": {name: importlib.metadata.version(name) for name in dependencies}}
        if transport_options is not None:
            if config.transport not in {"openai_chat_completions", "openai_responses", "anthropic_messages",
                "google_gemini_generate_content"}:
                raise ValueError("该模型传输尚未验证显式SDK重试设置")
            parameters.update(sdk_retry_policy="explicit_transport_options",
                transport_options=transport_options.model_dump(mode="json"))
        return parameters

    async def resolve_langchain_chat_model(self, model_key: str) -> Any:
        """Resolve a database-backed LangChain chat model for a harness.

        Authentication and provider selection stay owned by this service; a
        harness receives only the already configured model object.
        """
        try:
            config = self._model_registry.get_runtime_config(model_key)
        except KeyError as exc:
            raise ProviderClientError(f"No active model configuration found for '{model_key}'.") from exc
        api_key = await self._resolve_auth_token(config)
        if not api_key:
            raise ProviderClientError(f"Model '{config.name}' has no usable API key.")
        model = LangChainModelAdapter(
            timeout_seconds=self._settings.model.llm_request_timeout_seconds,
            max_retries=0,
        ).build_model(config, api_key)
        from src.application.models.framework_model_call_policy import model_config_fingerprint

        # This describes the configuration actually used to build the official
        # object. It is non-secret metadata, never a provider request parameter.
        return model.model_copy(update={"metadata": {**(model.metadata or {}),
            "qa_model_config_fingerprint": model_config_fingerprint(config)}})

    async def restore_completed(
        self,
        model_key: str,
        request: ModelInvocationRequest,
        *,
        expected: FrameworkModelCallRecord,
        expected_parameters: dict[str, Any] | None,
        expected_runtime_parameters: dict[str, Any] | None,
        transport_options: ModelTransportOptions | None,
    ) -> ModelInvocationResult:
        """Consume a proven application DTO; never authenticate, send or emit tokens.

        No upstream counterpart (checked: FrameworkModelCallPolicy, prepared
        semantic input, LangGraph persistence, v3 section 5.1): this request ran
        before graph admission. Its original settlement owns the response.
        """
        try:
            owner = current_framework_owner()
            if owner is None or self._framework_model_call_policy is None:
                raise RuntimeError("Completed model restoration requires an owner and framework model policy")
            if expected_parameters is None or expected_runtime_parameters is None or transport_options is None:
                raise ValueError("Completed model restoration requires its full frozen invocation contract")
            if transport_options.max_retries != 0:
                raise ValueError("Completed model restoration cannot authorize retries")
            config = self.get_model_config(model_key)
            if config is None or model_invocation_parameters(config) != expected_parameters:
                raise ValueError("Frozen completed model configuration changed")
            if self.get_invocation_runtime_parameters(model_key,
                transport_options=transport_options) != expected_runtime_parameters:
                raise ValueError("Frozen completed model runtime configuration changed")
            return await self._framework_model_call_policy.restore_completed_dto(
                owner=owner, expected=expected, config=config, request=request,
                options=self._framework_invocation_options(config, transport_options))
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("model_completed_result_rejected", extra={"model_key": model_key,
                "logical_call_id": request.trace_context.get("logical_call_id", ""),
                "error_type": type(exc).__name__})
            raise

    def _framework_invocation_options(self, config, transport_options):
        # Sending and reading must compare the same provider-affecting options.
        return {"transport_options": transport_options.model_dump(mode="json"),
            "streaming": _stream_handler_var.get() is not None,
            "langchain_adapter": self._uses_langchain(config)}

    async def invoke(
        self,
        model_key: str,
        request: ModelInvocationRequest,
        *, expected_parameters: dict[str, Any] | None = None,
        expected_runtime_parameters: dict[str, Any] | None = None,
        transport_options: ModelTransportOptions | None = None,
    ) -> ModelInvocationResult:
        owner = current_framework_owner()
        if owner is not None:
            if self._framework_model_call_policy is None:
                raise RuntimeError("Owner-bound DTO model invocation requires the framework model policy")
            if transport_options is not None and transport_options.max_retries != 0:
                raise ValueError("Owner-bound model retries require explicit accounted attempts")
            transport_options = transport_options or self.governed_transport_options()
        trace_context = dict(request.trace_context)
        if owner is not None and not (trace_context.get("logical_call_id") or trace_context.get("call_id")):
            raise ValueError("Owner-bound model invocation requires a durable call position")
        logical_call_id = str(trace_context.get("logical_call_id") or trace_context.get("call_id") or uuid4())
        trace_context["logical_call_id"] = logical_call_id
        request = request.model_copy(update={"trace_context": trace_context})
        if (expected_runtime_parameters is not None and
            self.get_invocation_runtime_parameters(model_key, transport_options=transport_options) != expected_runtime_parameters):
            logger.warning("model_invocation_runtime_changed", extra={
                "model_key": model_key, "logical_call_id": logical_call_id})
            raise ValueError("模型调用适配器、SDK或超时设置已变化，不能续写原任务")
        try:
            config = self._model_registry.get_runtime_config(model_key)
        except KeyError:
            logger.warning(
                "model_invocation_failed",
                extra={"model_key": model_key, "mode": "missing_active_model",
                    "logical_call_id": logical_call_id},
            )
            return ModelInvocationResult(
                text=(
                    "No active model configuration was found for this turn. "
                    "Activate a model and retry."
                ),
                request_payload={
                    "requested_model_key": model_key,
                    "messages": summarize_messages(request.messages),
                },
                response_summary={"mode": "missing_active_model", "model_key": model_key,
                    "logical_call_id": logical_call_id},
                raw_response={"mode": "missing_active_model", "model_key": model_key},
            )
        if expected_parameters is not None:
            if model_invocation_parameters(config) != expected_parameters:
                logger.warning("model_invocation_parameters_changed", extra={
                    "model_key": model_key, "logical_call_id": logical_call_id})
                raise ValueError("模型参数已变化，不能使用新配置续写原任务")
        if transport_options is not None:
            self.get_invocation_runtime_parameters(model_key, transport_options=transport_options)
            logger.info("model_invocation_transport_configured", extra={"model_key": config.key,
                "timeout_seconds": transport_options.timeout_seconds, "max_retries": transport_options.max_retries})
        try:
            mapped = map_model_request(config, request)
        except UnsupportedModelRequest as exc:
            logger.warning("model_request_option_unsupported", extra={"model_key": config.key,
                "option": exc.option, "reason": exc.reason, "transport": config.transport,
                "logical_call_id": logical_call_id})
            return ModelInvocationResult(text=str(exc), request_payload=self._summarize_request(config, request),
                response_summary={"mode": "unsupported_request", "model_key": config.key,
                    "option": exc.option, "reason": exc.reason, "logical_call_id": logical_call_id},
                raw_response={"mode": "unsupported_request", "option": exc.option, "reason": exc.reason})
        for decision in mapped.decisions:
            logger.info("model_request_option_decided", extra={"model_key": config.key,
                "transport": config.transport, **decision})
        try:
            api_key = await self._resolve_auth_token(config)
        except Exception as exc:
            logger.warning(
                "model_invocation_failed",
                extra={
                    "model_key": config.key,
                    "mode": "auth_error",
                    "error_type": type(exc).__name__,
                    "logical_call_id": logical_call_id,
                },
            )
            return ModelInvocationResult(
                text="Model authentication failed. Check its credentials and retry.",
                request_payload=self._summarize_request(config, request),
                response_summary={
                    "mode": "auth_error",
                    "model_key": config.key,
                    "error_type": type(exc).__name__,
                    "logical_call_id": logical_call_id,
                },
                raw_response={
                    "mode": "auth_error",
                    "model_key": config.key,
                    "error_type": type(exc).__name__,
                },
            )

        if not api_key:
            logger.warning(
                "model_invocation_failed",
                extra={"model_key": config.key, "mode": "missing_api_key",
                    "logical_call_id": logical_call_id},
            )
            return ModelInvocationResult(
                text="The active model has no usable API key. Configure its credentials and retry.",
                request_payload=self._summarize_request(config, request),
                response_summary={"mode": "missing_api_key", "model_key": config.key,
                    "logical_call_id": logical_call_id},
                raw_response={"mode": "missing_api_key", "model_key": config.key},
            )

        with observe_provider_call(
            logical_call_id,
            self._resolved_transport(config),
            trace_id=trace_context.get("trace_id", ""),
            attempt_id=trace_context.get("attempt_id", ""),
        ) as observation:
            async def send():
                if self._uses_langchain(config):
                    return await self._invoke_with_langchain_adapter(config, api_key, request,
                        transport_options=transport_options, cache=False if owner is not None else None)
                client = resolve_client(config,
                    timeout_seconds=transport_options.timeout_seconds if transport_options else self._settings.model.llm_request_timeout_seconds,
                    **({"max_retries": transport_options.max_retries} if transport_options else {}))
                return await self._invoke_with_client(client, config, api_key, request)

            try:
                if owner is None:
                    result = await send()
                else:
                    result = await self._framework_model_call_policy.invoke_dto(owner=owner, config=config,
                        request=request, options=self._framework_invocation_options(config, transport_options), send=send)
            except ProviderClientError as exc:
                result = self._http_error_result(config, request, exc)
        result.response_summary["logical_call_id"] = logical_call_id
        result.response_summary["http_request_attempt_count"] = observation.request_attempt_count
        if mapped.decisions:
            result.request_payload["request_mapping"] = {"version": "model-request-v1", "decisions": mapped.decisions}
            result.response_summary["request_mapping"] = result.request_payload["request_mapping"]
        return result

    def _uses_langchain(self, config: ModelConfigRecord) -> bool:
        return (config.transport == "openai_chat_completions" and
            bool(getattr(self._settings.model, "langchain_model_adapter_enabled", False)) and
            not (config.request_profile and config.request_profile.chat_token_parameter == "max_tokens"))

    async def _invoke_with_langchain_adapter(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
        *, transport_options: ModelTransportOptions | None = None,
        cache: bool | None = None,
    ) -> ModelInvocationResult:
        adapter = LangChainModelAdapter(
            timeout_seconds=transport_options.timeout_seconds if transport_options else self._settings.model.llm_request_timeout_seconds,
            max_retries=transport_options.max_retries if transport_options else 2,
            stream_handler=self._emit_stream_chunk if _stream_handler_var.get() is not None else None,
            cache=cache,
        )
        trace_context = None
        if (self._observability_service is not None and
            all(request.trace_context.get(key) for key in ("session_id", "turn_id", "trace_id"))):
            trace_context = self._observability_service.context_from_mapping(request.trace_context)
        trace_manager = (
            self._observability_service.trace_node(
                trace_context,
                node_name="model_call_langchain",
                inputs={"model_key": config.key, "provider": config.provider},
            )
            if trace_context is not None
            else nullcontext()
        )
        with trace_manager:
            result = await adapter.invoke(config, api_key, request)
        self._log_completion(config, result.response_summary)
        return result

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

        parsed = await self._send_with_client(client, config, api_key, request)

        self._log_completion(config, parsed)
        return ModelInvocationResult(
            text=parsed["text"] or "",
            tool_calls=parsed["tool_calls"],
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": model_response_mode(parsed),
                "provider": config.provider,
                "provider_profile": self._provider_profile_name(config),
                "transport": self._resolved_transport(config),
                "response_id": parsed["response_id"],
                "finish_reason": parsed["finish_reason"],
                "stop_reason": parsed["stop_reason"],
                "completion_status": model_completion_status(parsed),
                **stream_completion_fields(parsed),
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "tool_call_names": [item.name for item in parsed["tool_calls"]],
                "content_preview": truncate_text(parsed["text"], 180),
                "tool_fallback": "",
            },
            raw_response=parsed["raw_response"],
        )

    @staticmethod
    def _log_completion(config: ModelConfigRecord, summary: dict[str, Any]) -> None:
        log = logger.warning if model_response_mode(summary) == "incomplete_response" else logger.info
        log("model_completion_observed", extra={"model_key": config.key,
            "transport": config.transport, "completion_status": model_completion_status(summary),
            **stream_completion_fields(summary)})

    async def _send_with_client(
        self,
        client: ProviderClient,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]:
        trace_context = None
        if (self._observability_service is not None and
            all(request.trace_context.get(key) for key in ("session_id", "turn_id", "trace_id"))):
            trace_context = self._observability_service.context_from_mapping(request.trace_context)
        trace_manager = (
            self._observability_service.trace_node(
                trace_context,
                node_name="model_call",
                inputs={"model_key": config.key, "provider": config.provider},
            )
            if trace_context is not None
            else nullcontext()
        )
        with trace_manager:
            if _stream_handler_var.get() is not None:
                return await client.stream(config, api_key, request, self._emit_stream_chunk)
            return await client.invoke(config, api_key, request)

    def _provider_profile_name(self, config: ModelConfigRecord) -> str:
        return resolve_provider_profile(config.provider).provider

    def _resolved_transport(self, config: ModelConfigRecord) -> str:
        return normalize_transport(config.transport, provider=config.provider)

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

    def _http_error_result(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        exc: ProviderClientError,
    ) -> ModelInvocationResult:
        response_body = truncate_text(exc.response_body or "", 400)
        status_code = exc.status_code
        cause_error_type = type(exc.__cause__).__name__ if exc.__cause__ is not None else ""
        logger.warning(
            "model_invocation_failed",
            extra={
                "model_key": config.key,
                "mode": "http_error",
                "provider": config.provider,
                "transport": self._resolved_transport(config),
                "error_type": type(exc).__name__,
                "cause_error_type": cause_error_type,
                "status_code": status_code,
                "logical_call_id": request.trace_context.get("logical_call_id", ""),
                "http_request_attempt_count": current_provider_request_attempt_count(),
            },
        )
        return ModelInvocationResult(
            text="Model invocation failed. Check the model provider and retry.",
            request_payload=self._summarize_request(config, request),
            response_summary={
                "mode": "http_error",
                "provider": config.provider,
                "provider_profile": self._provider_profile_name(config),
                "transport": self._resolved_transport(config),
                "error_type": exc.__class__.__name__,
                "cause_error_type": cause_error_type,
                "status_code": status_code,
                "error": truncate_text(str(exc), 180),
                "response_body": response_body,
                "logical_call_id": request.trace_context.get("logical_call_id", ""),
                "http_request_attempt_count": current_provider_request_attempt_count(),
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

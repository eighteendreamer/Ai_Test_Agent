from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_openai import ChatOpenAI

from src.application.model_adapters.base import ModelPort
from src.application.model_adapters.message_adapter import (
    from_langchain_message,
    to_langchain_messages,
)
from src.application.model_clients.base import ProviderClientError
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult
from src.runtime.execution_logging import truncate_text


StreamHandler = Callable[[str], Awaitable[None]]


class LangChainModelAdapter(ModelPort):
    """LangChain adapter for OpenAI-compatible Chat Completions providers.

    The adapter returns the application's DTOs and deliberately does not
    expose LangChain message objects beyond this module.  Provider-specific
    integrations are added only after their dependency and contract matrix is
    verified.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        stream_handler: StreamHandler | None = None,
        model_factory: Callable[..., Any] = ChatOpenAI,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._stream_handler = stream_handler
        self._model_factory = model_factory

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        if config.transport != "openai_chat_completions":
            raise ProviderClientError(
                "LangChainModelAdapter currently supports only openai_chat_completions; "
                f"received transport={config.transport!r}."
            )

        try:
            model = self._build_model(config, api_key)
            if config.supports_tools and request.tools:
                model = model.bind_tools(_langchain_tools(request.tools), tool_choice="auto")
            messages = to_langchain_messages(request.system_prompt, request)
            if self._stream_handler is None:
                response = await model.ainvoke(messages)
            else:
                response = await self._stream(model, messages)
            parsed = from_langchain_message(response)
        except ProviderClientError:
            raise
        except Exception as exc:
            raise ProviderClientError(
                f"LangChain model invocation failed for provider={config.provider!r}: "
                f"{truncate_text(str(exc), 240)}",
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
            ) from exc

        return ModelInvocationResult(
            text=parsed["text"],
            tool_calls=parsed["tool_calls"],
            request_payload={
                "model_key": config.key,
                "model_id": config.model_id,
                "provider": config.provider,
                "transport": config.transport,
                "api_base_url": config.api_base_url,
                "tool_count": len(request.tools),
                "tool_names": [str(item.get("name") or "") for item in request.tools],
            },
            response_summary={
                "mode": "ok",
                "provider": config.provider,
                "transport": config.transport,
                "response_id": parsed["response_id"],
                "finish_reason": parsed["finish_reason"],
                "stop_reason": parsed["stop_reason"],
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "tool_call_names": [call.name for call in parsed["tool_calls"]],
            },
            raw_response=parsed["raw_response"],
        )

    def _build_model(self, config: ModelConfigRecord, api_key: str) -> Any:
        kwargs: dict[str, Any] = {
            "model": config.model_id,
            "api_key": api_key,
            "base_url": config.api_base_url,
            "timeout": self._timeout_seconds,
            "max_retries": self._max_retries,
            "max_completion_tokens": config.max_output_tokens,
        }
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if config.extra_headers:
            kwargs["default_headers"] = dict(config.extra_headers)
        return self._model_factory(**kwargs)

    async def _stream(self, model: Any, messages: list[Any]) -> Any:
        aggregate = None
        async for chunk in model.astream(messages):
            text = _text_from_content(getattr(chunk, "content", ""))
            if text and self._stream_handler is not None:
                await self._stream_handler(text)
            aggregate = chunk if aggregate is None else aggregate + chunk
        if aggregate is None:
            raise ProviderClientError("LangChain streaming returned no response chunks.")
        return aggregate


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or item.get("content") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    return ""


def _langchain_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map the application's tool schema to LangChain's provider-neutral form."""

    mapped: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        mapped.append(
            {
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": tool.get("input_schema") or {"type": "object"},
            }
        )
    return mapped

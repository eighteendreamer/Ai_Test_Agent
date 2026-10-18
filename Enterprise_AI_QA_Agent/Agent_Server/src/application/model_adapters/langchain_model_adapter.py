from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_openai import ChatOpenAI

from src.application.model_adapters.base import ModelPort
from src.application.model_adapters.message_adapter import (
    from_langchain_message,
    to_langchain_messages,
    to_langchain_tools,
)
from src.application.model_clients.base import ProviderClientError
from src.application.model_clients.http_observation import create_observed_http_client
from src.application.model_clients.request_mapping import map_model_request
from src.application.model_clients.completion import model_completion_status, model_response_mode, stream_completion_fields
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

        http_client = create_observed_http_client()
        try:
            model = self._build_model(config, api_key, http_async_client=http_client)
            if config.supports_tools and request.tools:
                model = model.bind_tools(to_langchain_tools(request.tools), tool_choice="auto")
            mapped = map_model_request(config, request).parameters
            if "response_format" in mapped:
                # ChatOpenAI chooses SDK parse/stream helpers when this key is
                # top-level, rejecting our existing non-strict tool schemas.
                # OpenAI's supported extra_body merge preserves the same wire
                # field while leaving business validation with our callers.
                mapped["extra_body"] = {"response_format": mapped.pop("response_format")}
            if mapped:
                model = model.bind(**mapped)
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
                status_code=_exception_status_code(exc),
            ) from exc
        finally:
            if http_client is not None:
                await http_client.aclose()

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
                "mode": model_response_mode(parsed),
                "provider": config.provider,
                "transport": config.transport,
                "response_id": parsed["response_id"],
                "finish_reason": parsed["finish_reason"],
                "stop_reason": parsed["stop_reason"],
                "completion_status": model_completion_status(parsed),
                **stream_completion_fields(parsed),
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "tool_call_names": [call.name for call in parsed["tool_calls"]],
            },
            raw_response=parsed["raw_response"],
        )

    async def invoke_structured(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
        schema: Any,
    ) -> Any:
        """Invoke the model with LangChain's validated structured-output path.

        ``include_raw=True`` is intentional: it lets us preserve provider
        response metadata for observability while returning only the validated
        schema to the caller.  Parsing failures become the existing provider
        error type so callers can apply their normal recovery policy.
        """

        if config.transport != "openai_chat_completions":
            raise ProviderClientError(
                "Structured LangChain output currently supports only "
                f"openai_chat_completions; received transport={config.transport!r}."
            )
        http_client = create_observed_http_client()
        try:
            model = self._build_model(config, api_key, http_async_client=http_client)
            structured_model = model.with_structured_output(schema, include_raw=True)
            payload = await structured_model.ainvoke(
                to_langchain_messages(request.system_prompt, request)
            )
        except ProviderClientError:
            raise
        except Exception as exc:
            raise ProviderClientError(
                f"LangChain structured-output invocation failed for provider={config.provider!r}: "
                f"{truncate_text(str(exc), 240)}",
                status_code=_exception_status_code(exc),
            ) from exc
        finally:
            if http_client is not None:
                await http_client.aclose()

        if not isinstance(payload, dict):
            raise ProviderClientError(
                "LangChain structured-output response did not use the include_raw envelope."
            )
        parsing_error = payload.get("parsing_error")
        if parsing_error is not None:
            raise ProviderClientError(
                "LangChain structured-output parsing failed: "
                f"{truncate_text(str(parsing_error), 240)}"
            )
        parsed = payload.get("parsed")
        if parsed is None:
            raise ProviderClientError(
                "LangChain structured-output response contained no parsed value."
            )
        return parsed

    def _build_model(self, config: ModelConfigRecord, api_key: str, *, http_async_client: Any = None) -> Any:
        if config.request_profile and config.request_profile.chat_token_parameter == "max_tokens":
            raise ProviderClientError("Explicit max_tokens requires the native Chat client; LangChain renames this field.")
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
        if http_async_client is not None:
            kwargs["http_async_client"] = http_async_client
        return self._model_factory(**kwargs)

    def build_model(self, config: ModelConfigRecord, api_key: str) -> Any:
        """Build the provider model for another LangChain-native harness.

        The construction remains in this adapter so timeout, retry, headers
        and database-backed provider settings cannot diverge between the
        normal LangChain path and a Deep Agents pilot.
        """
        model = self._build_model(config, api_key)
        mapped = map_model_request(config, ModelInvocationRequest(system_prompt="", messages=[])).parameters
        return model.bind(**mapped) if mapped else model

    async def _stream(self, model: Any, messages: list[Any]) -> Any:
        aggregate = None
        terminal_received = False
        async for chunk in model.astream(messages):
            if getattr(chunk, "response_metadata", {}).get("finish_reason"):
                terminal_received = True
            text = _text_from_content(getattr(chunk, "content", ""))
            if text and self._stream_handler is not None:
                await self._stream_handler(text)
            aggregate = chunk if aggregate is None else aggregate + chunk
        if aggregate is None:
            raise ProviderClientError("LangChain streaming returned no response chunks.")
        return aggregate.model_copy(update={"response_metadata": {
            **aggregate.response_metadata, "stream_terminal_received": terminal_received}})


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


def _exception_status_code(exc: BaseException) -> int | None:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    nested = getattr(response, "status_code", None)
    return nested if isinstance(nested, int) else None

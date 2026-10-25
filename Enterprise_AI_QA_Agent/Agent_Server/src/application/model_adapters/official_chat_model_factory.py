"""Construct official chat models from application configuration, without a gateway."""
from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from src.application.model_clients.base import ProviderClientError
from src.application.model_clients.request_mapping import map_model_request
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelTransportOptions


logger = logging.getLogger(__name__)


class OfficialChatModelFactory:
    """Own provider construction; model-call governance belongs to the runtime.

    max_retries counts retries after the initial request, for every transport.
    The Google integration uses total attempts and therefore needs +1.
    """

    def __init__(self, *, timeout_seconds: float = 60.0, max_retries: int = 0,
        openai_factory: Callable[..., Any] = ChatOpenAI) -> None:
        self._options = ModelTransportOptions(timeout_seconds=timeout_seconds, max_retries=max_retries)
        self._openai_factory = openai_factory

    def create(self, config: ModelConfigRecord, api_key: str, *, http_async_client: Any = None) -> Any:
        """Return an official BaseChatModel, preserving the configured transport."""
        return self._create(config, api_key, http_async_client=http_async_client, parameters={})

    def _create(self, config: ModelConfigRecord, api_key: str, *,
        parameters: dict[str, Any], http_async_client: Any = None) -> Any:
        transport = config.transport
        if http_async_client is not None and transport not in {"openai_chat_completions", "openai_responses"}:
            raise ProviderClientError(f"Custom HTTP client is not mapped for transport={transport!r}.")
        if transport in {"openai_chat_completions", "openai_responses"}:
            kwargs: dict[str, Any] = {
                "model": config.model_id, "api_key": api_key, "base_url": config.api_base_url,
                "use_responses_api": transport == "openai_responses",
                "timeout": self._options.timeout_seconds, "max_retries": self._options.max_retries,
            }
            if (transport == "openai_chat_completions" and config.request_profile
                and config.request_profile.chat_token_parameter == "max_tokens"):
                # ChatOpenAI 1.6.2 renames top-level max_tokens. Its supported
                # extra_body is merged by the official SDK after that mapping.
                kwargs["extra_body"] = {"max_tokens": config.max_output_tokens}
            else:
                kwargs["max_completion_tokens"] = config.max_output_tokens
            if config.extra_headers:
                kwargs["default_headers"] = dict(config.extra_headers)
            if http_async_client is not None:
                kwargs["http_async_client"] = http_async_client
            constructor = self._openai_factory
        elif transport == "anthropic_messages":
            from src.application.model_adapters.anthropic_chat_model import UsagePreservingChatAnthropic

            kwargs = {
                "model": config.model_id, "api_key": api_key, "base_url": config.api_base_url,
                "max_tokens": config.max_output_tokens, "timeout": self._options.timeout_seconds,
                "max_retries": self._options.max_retries,
                "default_headers": dict(config.extra_headers),
            }
            constructor = UsagePreservingChatAnthropic
        elif transport == "google_gemini_generate_content":
            from langchain_google_genai import ChatGoogleGenerativeAI

            kwargs = {
                "model": config.model_id, "google_api_key": api_key,
                "base_url": config.api_base_url, "vertexai": False,
                "max_output_tokens": config.max_output_tokens, "timeout": self._options.timeout_seconds,
                # langchain-google-genai 4.4.0 _common.py: 0 means SDK defaults;
                # 1 means the initial attempt only. Never pass application 0.
                "max_retries": self._options.max_retries + 1,
                "additional_headers": dict(config.extra_headers),
            }
            constructor = ChatGoogleGenerativeAI
        else:
            raise ProviderClientError(f"No official chat model for transport={transport!r}.")
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        parameters = dict(parameters)
        if transport == "openai_chat_completions" and "max_tokens" in parameters:
            kwargs["extra_body"] = {**kwargs.get("extra_body", {}), "max_tokens": parameters.pop("max_tokens")}
        kwargs.update(parameters)
        model = constructor(**kwargs)
        logger.info("official_chat_model_created", extra={
            "model_key": config.key, "transport": transport,
            "timeout_seconds": self._options.timeout_seconds,
            "max_retries": self._options.max_retries,
        })
        return model

    def build_model(self, config: ModelConfigRecord, api_key: str) -> Any:
        """Keep defaults on the actual model so bind_tools cannot discard them.

        RunnableBinding forwards bind_tools to its bound model without its
        invocation kwargs. Constructor-level native options survive that path.
        Per-call request options belong in the runtime's model-call middleware.
        """
        mapped = map_model_request(config, ModelInvocationRequest(system_prompt="", messages=[]))
        for decision in mapped.decisions:
            logger.info("framework_model_request_option_decided", extra={
                "model_key": config.key, "transport": config.transport, **decision})
        return self._create(config, api_key, parameters=mapped.parameters)

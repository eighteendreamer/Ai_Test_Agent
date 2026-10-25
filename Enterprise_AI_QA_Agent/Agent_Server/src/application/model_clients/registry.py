from __future__ import annotations

from src.application.model_clients.anthropic_client import AnthropicMessagesClient
from src.application.model_clients.base import ProviderClient
from src.application.model_clients.google_client import GoogleGenAIClient
from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.model_clients.openai_responses_client import OpenAIResponsesClient
from src.application.model_clients.provider_profiles import normalize_provider, normalize_transport
from src.schemas.model_config import ModelConfigRecord


def resolve_client(
    config: ModelConfigRecord,
    *,
    timeout_seconds: float = 300.0,
    max_retries: int | None = None,
) -> ProviderClient:
    """Pick the official-SDK client for this config's transport.

    OpenAI-compatible vendors keep going through the openai client.
    """
    transport = normalize_transport(
        config.transport,
        provider=normalize_provider(config.provider),
    )
    retry_options = {"max_retries": max_retries} if max_retries is not None else {}
    if transport == "anthropic_messages":
        return AnthropicMessagesClient(timeout_seconds=timeout_seconds, **retry_options)
    if transport == "google_gemini_generate_content":
        return GoogleGenAIClient(timeout_seconds=timeout_seconds, **retry_options)
    if transport == "openai_responses":
        return OpenAIResponsesClient(timeout_seconds=timeout_seconds, **retry_options)
    return OpenAIChatClient(timeout_seconds=timeout_seconds, **retry_options)

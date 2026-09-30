from __future__ import annotations

import json

import anthropic
import httpx
import httpx2
import openai
import pytest

from src.application.model_clients.anthropic_client import AnthropicMessagesClient
from src.application.model_clients.base import ProviderClientError
from src.application.model_clients.openai_client import OpenAIChatClient
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


def _event(provider: str) -> bytes:
    if provider == "openai":
        chunk = {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"content": "first"}, "finish_reason": None}],
        }
        return f"data: {json.dumps(chunk)}\n\n".encode()
    chunk = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "first"},
    }
    return f"event: content_block_delta\ndata: {json.dumps(chunk)}\n\n".encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_sdk_does_not_retry_after_first_streamed_delta(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    http_library = httpx if provider == "openai" else httpx2
    requests: list = []

    class OneDeltaThenDisconnect(http_library.AsyncByteStream):
        async def __aiter__(self):
            yield _event(provider)
            raise http_library.ReadError("midstream disconnect")

    async def handle(request):
        requests.append(request)
        return http_library.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=OneDeltaThenDisconnect(),
        )

    transport = http_library.MockTransport(handle)
    http_client = http_library.AsyncClient(transport=transport)
    sdk_client = (
        openai.AsyncOpenAI(
            api_key="test-key",
            base_url="https://model.example.test/v1",
            max_retries=2,
            http_client=http_client,
        )
        if provider == "openai"
        else anthropic.AsyncAnthropic(
            api_key="test-key",
            base_url="https://model.example.test/v1",
            max_retries=2,
            http_client=http_client,
        )
    )
    client = OpenAIChatClient() if provider == "openai" else AnthropicMessagesClient()
    monkeypatch.setattr(client, "_make_client", lambda config, api_key: sdk_client)
    config = ModelConfigRecord(
        key="test-model",
        name="Test model",
        provider=provider,
        transport="openai_chat_completions" if provider == "openai" else "anthropic_messages",
        model_id="test-model",
        api_base_url="https://model.example.test/v1",
    )
    request = ModelInvocationRequest(
        system_prompt="Test",
        messages=[{"role": "user", "content": "Begin"}],
    )
    deltas: list[str] = []

    async def receive_delta(delta: str) -> None:
        deltas.append(delta)

    with pytest.raises((ProviderClientError, httpx2.ReadError)):
        await client.stream(config, "test-key", request, receive_delta)

    assert deltas == ["first"]
    assert len(requests) == 1

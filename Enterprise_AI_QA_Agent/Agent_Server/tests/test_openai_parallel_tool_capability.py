from __future__ import annotations

import pytest
from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.model_clients.openai_responses_client import OpenAIResponsesClient
from src.schemas.model_config import (
    ModelCapabilities,
    ModelConfigRecord,
    ModelInvocationRequest,
)


@pytest.mark.parametrize(
    ("client_type", "transport"),
    [
        (OpenAIChatClient, "openai_chat_completions"),
        (OpenAIResponsesClient, "openai_responses"),
    ],
)
def test_openai_clients_enable_parallel_calls_when_capability_is_true(
    client_type: type[OpenAIChatClient | OpenAIResponsesClient],
    transport: str,
) -> None:
    config = ModelConfigRecord(
        key="openai-test",
        name="OpenAI test",
        provider="openai",
        transport=transport,
        model_id="gpt-test",
        api_base_url="https://api.openai.com/v1",
        capabilities=ModelCapabilities(parallel_tool_calls=True),
    )
    request = ModelInvocationRequest(
        system_prompt="",
        messages=[{"role": "user", "content": "Run the two checks."}],
        tools=[
            {
                "name": "check_first",
                "description": "First check",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "check_second",
                "description": "Second check",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )

    params = client_type()._build_params(config, request, None)

    assert params["parallel_tool_calls"] is True

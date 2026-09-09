from __future__ import annotations

import pytest

from src.application.model_adapters.legacy_model_adapter import LegacyProviderAdapter
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.tool_runtime import ModelToolCall


class _FakeClient:
    client_key = "openai_chat_completions"

    async def invoke(self, config, api_key, request):
        return {
            "text": "legacy response",
            "tool_calls": [ModelToolCall(id="call-1", name="inspect", arguments={"x": 1})],
            "response_id": "resp-legacy",
            "finish_reason": "tool_calls",
            "stop_reason": None,
            "usage": {"total_tokens": 9},
            "raw_response": {"id": "resp-legacy"},
        }


def _config() -> ModelConfigRecord:
    return ModelConfigRecord(
        key="legacy-test",
        name="Legacy test",
        provider="qwen",
        transport="openai_chat_completions",
        model_id="qwen-test",
        api_base_url="https://example.test/v1",
    )


@pytest.mark.asyncio
async def test_legacy_provider_adapter_maps_existing_client_contract():
    result = await LegacyProviderAdapter(_FakeClient()).invoke(
        _config(),
        "secret-not-logged",
        ModelInvocationRequest(
            system_prompt="",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"name": "inspect"}],
        ),
    )

    assert result.text == "legacy response"
    assert result.tool_calls[0].id == "call-1"
    assert result.response_summary["usage"]["total_tokens"] == 9
    assert result.response_summary["tool_call_names"] == ["inspect"]

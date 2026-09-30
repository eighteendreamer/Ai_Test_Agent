from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from src.application.model_clients.google_client import GoogleGenAIClient
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


class _FakeGoogleModels:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class _FakeGoogleClient:
    last_instance: _FakeGoogleClient | None = None

    def __init__(self, response: Any) -> None:
        self.models = _FakeGoogleModels(response)
        self.aio = SimpleNamespace(models=self.models)
        _FakeGoogleClient.last_instance = self


def test_google_tool_result_history_uses_the_declared_function_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(candidates=[], usage_metadata=None, response_id="resp-history")
    monkeypatch.setattr(
        GoogleGenAIClient,
        "_make_client",
        lambda self, config, api_key: _FakeGoogleClient(response),
    )
    config = ModelConfigRecord(
        key="gemini",
        name="Gemini",
        provider="google",
        transport="google_gemini_generate_content",
        model_id="gemini-test",
        api_base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    request = ModelInvocationRequest(
        system_prompt="You are a QA agent.",
        messages=[
            {"role": "user", "content": "run scan"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "run.security-scan",
                            "arguments": "{\"target\": \"example.test\"}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "run.security-scan",
                "content": {"ok": True},
            },
        ],
        tools=[
            {
                "name": "run.security-scan",
                "description": "Run a scan",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    asyncio.run(GoogleGenAIClient().invoke(config, "test-key", request))

    call = _FakeGoogleClient.last_instance.models.calls[0]
    declared_name = call["config"]["tools"][0]["function_declarations"][0]["name"]
    assistant_call = call["contents"][1]["parts"][-1]["function_call"]["name"]
    tool_result = call["contents"][2]["parts"][0]["function_response"]
    assert declared_name == "run_security_scan"
    assert assistant_call == declared_name
    assert tool_result["name"] == declared_name
    assert tool_result["response"] == {"ok": True}

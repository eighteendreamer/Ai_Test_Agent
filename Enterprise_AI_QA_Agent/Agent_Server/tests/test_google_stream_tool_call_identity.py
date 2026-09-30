from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types as genai_types

from src.application.model_clients.google_client import GoogleGenAIClient
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


def _config() -> ModelConfigRecord:
    return ModelConfigRecord(
        key="gemini-test",
        name="Gemini test",
        provider="google",
        transport="google_gemini_generate_content",
        model_id="gemini-test",
        api_base_url="https://generativelanguage.googleapis.com/v1beta",
    )


def _function_call_response(call_id: str | None, name: str) -> genai_types.GenerateContentResponse:
    return genai_types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "id": call_id,
                                    "name": name,
                                    "args": {"target": name},
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )


class _FakeModels:
    def __init__(self, chunks: list[genai_types.GenerateContentResponse]) -> None:
        self.chunks = chunks

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        async def _chunks() -> Any:
            for chunk in self.chunks:
                yield chunk

        return _chunks()


def test_google_stream_keeps_complete_calls_from_separate_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = _FakeModels(
        [
            _function_call_response("sdk-call-a", "check_first"),
            _function_call_response("sdk-call-b", "check_second"),
        ]
    )
    monkeypatch.setattr(
        GoogleGenAIClient,
        "_make_client",
        lambda self, config, api_key: SimpleNamespace(
            aio=SimpleNamespace(models=models)
        ),
    )

    async def on_text_delta(text: str) -> None:
        raise AssertionError(f"Unexpected text delta: {text}")

    result = asyncio.run(
        GoogleGenAIClient().stream(
            _config(),
            "test-key",
            ModelInvocationRequest(
                system_prompt="",
                messages=[{"role": "user", "content": "Run both checks."}],
            ),
            on_text_delta,
        )
    )

    assert [(call.id, call.name) for call in result["tool_calls"]] == [
        ("sdk-call-a", "check_first"),
        ("sdk-call-b", "check_second"),
    ]


def test_google_provider_call_id_survives_response_and_history() -> None:
    client = GoogleGenAIClient()
    response = client._parse_response(
        _function_call_response("sdk-call-a", "check_first")
    )
    assert response["tool_calls"][0].id == "sdk-call-a"

    request = ModelInvocationRequest(
        system_prompt="",
        messages=[
            {"role": "user", "content": "Run the check."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "sdk-call-a",
                        "name": "check_first",
                        "arguments": {"target": "example.test"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "sdk-call-a",
                "name": "check_first",
                "content": {"ok": True},
            },
        ],
    )
    contents = client._build_contents(request)
    assistant_call = contents[1]["parts"][-1]["function_call"]
    tool_response = contents[2]["parts"][0]["function_response"]
    assert assistant_call["id"] == "sdk-call-a"
    assert tool_response["id"] == "sdk-call-a"
    genai_types.Content.model_validate(contents[1])
    genai_types.Content.model_validate(contents[2])


def test_google_missing_provider_ids_get_distinct_local_ids() -> None:
    client = GoogleGenAIClient()
    response = _function_call_response(None, "check_first")

    first = client._parse_response(response)["tool_calls"][0].id
    second = client._parse_response(response)["tool_calls"][0].id

    assert first != second

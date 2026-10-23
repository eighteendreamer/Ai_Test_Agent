from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from src.application.model_clients.anthropic_client import AnthropicMessagesClient
from src.application.model_clients.base import (
    ProviderClientError,
    build_tool_name_map,
    remap_tool_calls,
)
from src.application.model_clients.google_client import GoogleGenAIClient
from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.model_clients.openai_responses_client import OpenAIResponsesClient
from src.application.model_clients.registry import resolve_client
from src.application.model_clients.provider_profiles import normalize_transport
from src.application.models.model_runtime_service import ModelRuntimeService
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.tool_runtime import ModelToolCall


def _config(**updates) -> ModelConfigRecord:
    values = {
        "key": "chat-model",
        "name": "chat-model",
        "provider": "openai",
        "transport": "openai_chat_completions",
        "model_id": "gpt-test",
        "api_base_url": "https://api.openai.com/v1",
        "api_key": "secret",
        "max_output_tokens": 4096,
        "temperature": 0.2,
    }
    values.update(updates)
    return ModelConfigRecord(**values)


def _request(**updates) -> ModelInvocationRequest:
    values: dict[str, Any] = {
        "system_prompt": "You are a QA agent.",
        "messages": [{"role": "user", "content": "ping"}],
    }
    values.update(updates)
    return ModelInvocationRequest(**values)


_TOOLS = [
    {
        "name": "run.security-scan",
        "description": "Run a scan",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
    }
]


# ---------------------------------------------------------------------------
# transport dispatch


def test_resolve_client_dispatches_by_transport() -> None:
    assert isinstance(resolve_client(_config()), OpenAIChatClient)
    assert isinstance(
        resolve_client(_config(provider="deepseek", api_base_url="https://api.deepseek.com/v1")),
        OpenAIChatClient,
    )
    assert isinstance(
        resolve_client(_config(transport="openai_responses")),
        OpenAIResponsesClient,
    )
    assert isinstance(
        resolve_client(_config(provider="anthropic", transport="anthropic_messages")),
        AnthropicMessagesClient,
    )
    assert isinstance(
        resolve_client(_config(provider="google", transport="google_gemini_generate_content")),
        GoogleGenAIClient,
    )
    # transport omitted in DB rows falls back to the provider profile default
    assert isinstance(
        resolve_client(_config(provider="gemini", transport="openai_chat_completions").model_copy(update={"transport": "openai_chat_completions"})),
        OpenAIChatClient,
    )


def test_unknown_explicit_transport_is_rejected_but_missing_transport_uses_provider_default() -> None:
    assert normalize_transport(None, provider="anthropic") == "anthropic_messages"
    assert normalize_transport("", provider="google") == "google_gemini_generate_content"
    config = _config(transport="openai_chat_completions").model_copy(update={"transport": "responses_v2"})
    with pytest.raises(ValueError, match="Unsupported model transport: responses_v2"):
        resolve_client(config)


# ---------------------------------------------------------------------------
# openai client


class _FakeChatCompletions:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeAsyncOpenAI:
    last_instance: _FakeAsyncOpenAI | None = None

    def __init__(self, response: Any) -> None:
        self.completions = _FakeChatCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)
        _FakeAsyncOpenAI.last_instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _patch_openai(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    monkeypatch.setattr(
        OpenAIChatClient,
        "_make_client",
        lambda self, config, api_key: _FakeAsyncOpenAI(response),
    )


def test_openai_client_maps_request_and_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "working on it",
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "function": {
                                "name": "run_security-scan",
                                "arguments": "{\"target\": \"https://example.test\"}",
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
    }
    _patch_openai(monkeypatch, response)

    request = _request(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "scan this"},
                    {"type": "image_url", "image_url": {"url": "https://img.test/a.png"}},
                ],
            }
        ],
        tools=_TOOLS,
    )
    parsed = asyncio.run(OpenAIChatClient().invoke(_config(supports_vision=True), "secret", request))

    call = _FakeAsyncOpenAI.last_instance.completions.calls[0]
    assert call["model"] == "gpt-test"
    assert call["max_tokens"] == 4096
    assert call["temperature"] == 0.2
    assert call["messages"][0] == {"role": "system", "content": "You are a QA agent."}
    assert call["messages"][1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://img.test/a.png"},
    }
    assert call["tools"][0]["function"]["name"] == "run_security-scan"
    assert call["tool_choice"] == "auto"

    assert parsed["text"] == "working on it"
    # sanitized tool name is remapped back to the registry name
    assert parsed["tool_calls"][0].name == "run.security-scan"
    assert parsed["tool_calls"][0].arguments == {"target": "https://example.test"}
    assert parsed["usage"] == {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
    assert parsed["finish_reason"] == "tool_calls"
    assert parsed["response_id"] == "chatcmpl-1"


def test_openai_client_wraps_api_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    http_response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        content=b'{"error": "rate limited"}',
    )
    _patch_openai(
        monkeypatch,
        openai.APIStatusError("rate limited", response=http_response, body=None),
    )

    with pytest.raises(ProviderClientError) as exc_info:
        asyncio.run(OpenAIChatClient().invoke(_config(), "secret", _request()))

    assert exc_info.value.status_code == 429
    assert "rate limited" in exc_info.value.response_body


# ---------------------------------------------------------------------------
# openai responses client


class _FakeResponsesResource:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeResponsesOpenAI:
    last_instance: _FakeResponsesOpenAI | None = None

    def __init__(self, response: Any) -> None:
        self.responses = _FakeResponsesResource(response)
        _FakeResponsesOpenAI.last_instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _responses_result() -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_123",
        status="completed",
        incomplete_details=None,
        output=[
            SimpleNamespace(
                type="reasoning",
                content=[],
            ),
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="output_text", text="scan queued"),
                ],
            ),
            SimpleNamespace(
                type="function_call",
                call_id="call_77",
                name="run_security-scan",
                arguments="{\"target\": \"https://example.test\"}",
            ),
        ],
        usage=SimpleNamespace(input_tokens=42, output_tokens=9, total_tokens=51),
    )


def test_responses_client_maps_request_and_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OpenAIResponsesClient,
        "_make_client",
        lambda self, config, api_key: _FakeResponsesOpenAI(_responses_result()),
    )

    config = _config(transport="openai_responses")
    request = _request(
        messages=[
            {"role": "user", "content": "scan this"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "run.security-scan", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "previous output"},
        ],
        tools=_TOOLS,
    )
    parsed = asyncio.run(OpenAIResponsesClient().invoke(config, "secret", request))

    call = _FakeResponsesOpenAI.last_instance.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["instructions"] == "You are a QA agent."
    assert call["max_output_tokens"] == 4096
    assert call["store"] is False
    # flat function tool format (not nested under "function")
    assert call["tools"][0] == {
        "type": "function",
        "name": "run_security-scan",
        "description": "Run a scan",
        "parameters": _TOOLS[0]["input_schema"],
        "strict": False,
    }
    # history mapping: user message + function_call item + function_call_output item
    assert call["input"][0] == {"role": "user", "content": "scan this"}
    assert call["input"][1]["type"] == "function_call"
    assert call["input"][1]["call_id"] == "call_1"
    assert call["input"][1]["name"] == "run_security-scan"
    assert call["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "previous output",
    }

    assert parsed["text"] == "scan queued"
    assert parsed["tool_calls"][0].name == "run.security-scan"
    assert parsed["tool_calls"][0].id == "call_77"
    assert parsed["tool_calls"][0].arguments == {"target": "https://example.test"}
    assert parsed["finish_reason"] == "completed"
    assert parsed["usage"] == {"input_tokens": 42, "output_tokens": 9, "total_tokens": 51}
    assert parsed["response_id"] == "resp_123"


def test_responses_client_stream_collects_deltas_and_final_response(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="scan "),
        SimpleNamespace(type="response.output_text.delta", delta="queued"),
        SimpleNamespace(type="response.completed", response=_responses_result()),
    ]

    class _FakeStream:
        def __aiter__(self):
            self._iter = iter(events)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    monkeypatch.setattr(
        OpenAIResponsesClient,
        "_make_client",
        lambda self, config, api_key: _FakeResponsesOpenAI(_FakeStream()),
    )

    received: list[str] = []

    async def on_delta(chunk: str) -> None:
        received.append(chunk)

    config = _config(transport="openai_responses")
    parsed = asyncio.run(
        OpenAIResponsesClient().stream(config, "secret", _request(tools=_TOOLS), on_delta)
    )

    assert received == ["scan ", "queued"]
    assert _FakeResponsesOpenAI.last_instance.responses.calls[0]["stream"] is True
    assert parsed["text"] == "scan queued"
    assert parsed["tool_calls"][0].name == "run.security-scan"
    assert parsed["usage"] == {"input_tokens": 42, "output_tokens": 9, "total_tokens": 51}
    assert parsed["raw_response"]["mode"] == "stream"


def test_responses_client_wraps_api_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    http_response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        content=b'{"error": "bad request"}',
    )
    monkeypatch.setattr(
        OpenAIResponsesClient,
        "_make_client",
        lambda self, config, api_key: _FakeResponsesOpenAI(
            openai.APIStatusError("bad request", response=http_response, body=None)
        ),
    )

    with pytest.raises(ProviderClientError) as exc_info:
        asyncio.run(
            OpenAIResponsesClient().invoke(_config(transport="openai_responses"), "secret", _request())
        )

    assert exc_info.value.status_code == 400
    assert "bad request" in exc_info.value.response_body


# ---------------------------------------------------------------------------
# anthropic client


class _FakeAnthropicMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeAsyncAnthropic:
    last_instance: _FakeAsyncAnthropic | None = None

    def __init__(self, response: Any) -> None:
        self.messages = _FakeAnthropicMessages(response)
        _FakeAsyncAnthropic.last_instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def test_anthropic_client_maps_request_and_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "id": "msg-1",
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "let me scan"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "run_security-scan",
                "input": {"target": "https://example.test"},
            },
        ],
        "usage": {"input_tokens": 20, "output_tokens": 9},
    }
    monkeypatch.setattr(
        AnthropicMessagesClient,
        "_make_client",
        lambda self, config, api_key: _FakeAsyncAnthropic(response),
    )

    config = _config(provider="anthropic", transport="anthropic_messages", model_id="claude-test")
    parsed = asyncio.run(
        AnthropicMessagesClient().invoke(config, "secret", _request(tools=_TOOLS))
    )

    call = _FakeAsyncAnthropic.last_instance.messages.calls[0]
    assert call["model"] == "claude-test"
    assert call["system"] == "You are a QA agent."
    assert call["max_tokens"] == 4096
    assert call["tools"][0]["name"] == "run_security-scan"
    assert call["tools"][0]["input_schema"]["required"] == ["target"]
    assert call["messages"] == [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]

    assert parsed["text"] == "let me scan"
    assert parsed["tool_calls"][0].name == "run.security-scan"
    assert parsed["stop_reason"] == "tool_use"
    assert parsed["usage"] == {"input_tokens": 20, "output_tokens": 9}


# ---------------------------------------------------------------------------
# google client


class _FakeGoogleModels:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeGenAIClient:
    last_instance: _FakeGenAIClient | None = None

    def __init__(self, response: Any) -> None:
        self.models = _FakeGoogleModels(response)
        self.aio = SimpleNamespace(models=self.models)
        _FakeGenAIClient.last_instance = self


def _google_config() -> ModelConfigRecord:
    return _config(
        provider="google",
        transport="google_gemini_generate_content",
        model_id="gemini-test",
        api_base_url="https://generativelanguage.googleapis.com/v1beta",
    )


def test_google_client_maps_request_and_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        response_id="resp-1",
        candidates=[
            SimpleNamespace(
                finish_reason=SimpleNamespace(value="STOP"),
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text="scanning now", function_call=None),
                        SimpleNamespace(
                            text=None,
                            function_call=SimpleNamespace(
                                name="run_security_scan",
                                args={"target": "https://example.test"},
                            ),
                        ),
                    ]
                ),
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=30,
            candidates_token_count=7,
            total_token_count=37,
            cached_content_token_count=None,
            thoughts_token_count=None,
        ),
    )
    monkeypatch.setattr(
        GoogleGenAIClient,
        "_make_client",
        lambda self, config, api_key: _FakeGenAIClient(response),
    )

    parsed = asyncio.run(
        GoogleGenAIClient().invoke(_google_config(), "secret", _request(tools=_TOOLS))
    )

    call = _FakeGenAIClient.last_instance.models.calls[0]
    assert call["model"] == "gemini-test"
    declaration = call["config"]["tools"][0]["function_declarations"][0]
    # The project's shared name map is used for declarations and call remapping.
    assert declaration["name"] == "run_security_scan"
    # A strict parameter object must retain its additional-property constraint.
    assert declaration["parameters"] == {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    }
    assert call["config"]["system_instruction"] == "You are a QA agent."
    assert call["contents"] == [{"role": "user", "parts": [{"text": "ping"}]}]

    assert parsed["text"] == "scanning now"
    assert parsed["tool_calls"][0].name == "run.security-scan"
    assert parsed["finish_reason"] == "STOP"
    assert parsed["usage"] == {
        "promptTokenCount": 30,
        "candidatesTokenCount": 7,
        "totalTokenCount": 37,
    }
    assert parsed["response_id"] == "resp-1"


def test_google_client_wraps_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.genai import errors as genai_errors

    api_error = genai_errors.APIError(
        400,
        {"error": {"message": "tools unsupported", "status": "INVALID_ARGUMENT"}},
    )
    monkeypatch.setattr(
        GoogleGenAIClient,
        "_make_client",
        lambda self, config, api_key: _FakeGenAIClient(api_error),
    )

    with pytest.raises(ProviderClientError) as exc_info:
        asyncio.run(GoogleGenAIClient().invoke(_google_config(), "secret", _request()))

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# tool name sanitize / remap helpers


def test_google_tool_name_map_and_remap_round_trip() -> None:
    tools = [{"name": "mcp.browser-click"}, {"name": "mcp_browser_click"}]
    name_map = build_tool_name_map(tools, allow_dash=False)
    assert name_map["mcp.browser-click"] == "mcp_browser_click"
    # collision gets a numeric suffix
    assert name_map["mcp_browser_click"] != name_map["mcp.browser-click"]

    remapped = remap_tool_calls(
        [ModelToolCall(id="1", name="mcp_browser_click", arguments={})],
        {"mcp.browser-click": "mcp_browser_click"},
    )
    assert remapped[0].name == "mcp.browser-click"


def test_tool_name_map_handles_colliding_long_names_without_truncation_loop() -> None:
    tools = [
        {"name": "mcp." + "a" * 80},
        {"name": "mcp/" + "a" * 80},
        {"name": "mcp:" + "a" * 80},
    ]

    name_map = build_tool_name_map(tools, allow_dash=False)
    mapped = [name_map[item["name"]] for item in tools]

    assert len(set(mapped)) == len(mapped)
    assert all(1 <= len(name) <= 64 for name in mapped)
    assert mapped[0].startswith("mcp_")
    assert not mapped[0].endswith("_2")
    assert mapped[1].endswith("_2")
    assert mapped[2].endswith("_3")


def test_tool_name_map_long_collisions_preserve_limit_and_uniqueness() -> None:
    prefix = "mcp_" + "x" * 60
    tools = [
        {"name": f"{prefix}-one"},
        {"name": f"{prefix}-two"},
        {"name": f"{prefix}-three"},
    ]

    name_map = build_tool_name_map(tools)

    assert name_map[tools[0]["name"]] == prefix
    assert name_map[tools[1]["name"]] == f"{prefix[:62]}_2"
    assert name_map[tools[2]["name"]] == f"{prefix[:62]}_3"
    assert len(set(name_map.values())) == len(name_map)
    assert all(len(name) <= 64 for name in name_map.values())


def test_tool_name_map_collision_suffix_uses_original_base() -> None:
    name_map = build_tool_name_map(
        [
            {"name": "foo"},
            {"name": "foo_2"},
            {"name": "foo!"},
        ]
    )

    assert name_map == {"foo": "foo", "foo_2": "foo_2", "foo!": "foo_3"}


# ---------------------------------------------------------------------------
# runtime integration: Gemini 400 preserves the tool contract and reports failure


class _FakeRuntimeClient:
    def __init__(self) -> None:
        self.requests: list[ModelInvocationRequest] = []

    async def invoke(self, config, api_key, request):
        self.requests.append(request)
        if request.tools:
            raise ProviderClientError(
                "tools rejected",
                status_code=400,
                response_body="function calling unsupported",
            )
        return {
            "text": "fallback ok",
            "tool_calls": [],
            "finish_reason": "STOP",
            "stop_reason": "STOP",
            "usage": {"promptTokenCount": 3, "totalTokenCount": 4},
            "response_id": "resp-2",
            "raw_response": {},
        }

    async def stream(self, config, api_key, request, on_text_delta):
        raise AssertionError("stream must not be used without a handler")


def test_model_runtime_reports_gemini_400_without_removing_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _google_config()
    fake_client = _FakeRuntimeClient()
    monkeypatch.setattr(
        "src.application.models.model_runtime_service.resolve_client",
        lambda cfg, timeout_seconds: fake_client,
    )

    registry = SimpleNamespace(get_runtime_config=lambda key: config)
    settings = SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=30))
    service = ModelRuntimeService(model_registry=registry, settings=settings)

    result = asyncio.run(service.invoke("chat-model", _request(tools=_TOOLS)))

    assert len(fake_client.requests) == 1
    assert fake_client.requests[0].tools == _TOOLS
    assert result.response_summary["mode"] == "http_error"
    assert result.response_summary["status_code"] == 400
    assert "tool_fallback" not in result.response_summary
    assert result.response_summary["provider_profile"] == "google"
    assert result.response_summary["transport"] == "google_gemini_generate_content"
    assert result.request_payload["tool_count"] == 1
    assert result.request_payload["tool_names"] == ["run.security-scan"]
    assert "fallback ok" not in result.text
    assert "tools rejected" not in result.text


def test_model_runtime_reports_http_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()

    class _AlwaysFailClient:
        async def invoke(self, config, api_key, request):
            raise ProviderClientError(
                "server exploded",
                status_code=500,
                response_body='{"error": "boom"}',
            )

    monkeypatch.setattr(
        "src.application.models.model_runtime_service.resolve_client",
        lambda cfg, timeout_seconds: _AlwaysFailClient(),
    )

    registry = SimpleNamespace(get_runtime_config=lambda key: config)
    settings = SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=30))
    service = ModelRuntimeService(model_registry=registry, settings=settings)

    result = asyncio.run(service.invoke("chat-model", _request()))

    assert result.response_summary["mode"] == "http_error"
    assert result.response_summary["status_code"] == 500
    assert result.response_summary["provider_profile"] == "openai"
    assert result.response_summary["transport"] == "openai_chat_completions"
    assert "boom" in result.response_summary["response_body"]

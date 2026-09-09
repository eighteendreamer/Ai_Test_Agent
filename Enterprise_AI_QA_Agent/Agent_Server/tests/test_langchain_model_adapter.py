from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from types import SimpleNamespace
from pydantic import BaseModel

from src.application.model_adapters.langchain_model_adapter import LangChainModelAdapter
from src.application.model_adapters.legacy_model_adapter import LegacyProviderAdapter
from src.application.model_clients.base import ProviderClientError
from src.application.models.model_runtime_service import ModelRuntimeService
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult


def _config(**updates) -> ModelConfigRecord:
    values = {
        "key": "qwen-test",
        "name": "Qwen test",
        "provider": "qwen",
        "transport": "openai_chat_completions",
        "model_id": "qwen-test",
        "api_base_url": "https://example.test/v1",
        "api_key": None,
        "max_output_tokens": 128,
        "supports_tools": True,
    }
    values.update(updates)
    return ModelConfigRecord(**values)


def _request(**updates) -> ModelInvocationRequest:
    values = {
        "system_prompt": "You are concise.",
        "messages": [{"role": "user", "content": "hello"}],
    }
    values.update(updates)
    return ModelInvocationRequest(**values)


class _FakeRunnable:
    def __init__(self, *, stream=False):
        self.stream = stream
        self.bound_tools = None

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = (tools, kwargs)
        return self

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        return AIMessage(
            content="hello back",
            response_metadata={"id": "resp-1", "finish_reason": "stop"},
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        )

    async def astream(self, messages):
        yield AIMessageChunk(content="hello ")
        yield AIMessageChunk(content="back")

    async def ainvoke_structured(self, messages):
        return {"parsed": {"answer": "ok"}, "raw": {}, "parsing_error": None}


class _FakeFactory:
    def __init__(self):
        self.kwargs = None
        self.model = _FakeRunnable()

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.model


class _FailingRunnable(_FakeRunnable):
    async def ainvoke(self, messages):
        raise RuntimeError("provider failure")


class _RateLimitedError(Exception):
    status_code = 429


class _RateLimitedRunnable(_FakeRunnable):
    async def ainvoke(self, messages):
        raise _RateLimitedError("rate limited")


class _FailingFactory(_FakeFactory):
    def __init__(self):
        super().__init__()
        self.model = _FailingRunnable()


class _RateLimitedFactory(_FakeFactory):
    def __init__(self):
        super().__init__()
        self.model = _RateLimitedRunnable()


class _CancelledRunnable(_FakeRunnable):
    async def ainvoke(self, messages):
        raise asyncio.CancelledError()


class _CancelledFactory(_FakeFactory):
    def __init__(self):
        super().__init__()
        self.model = _CancelledRunnable()


@pytest.mark.asyncio
async def test_adapter_invokes_langchain_and_returns_business_dto():
    factory = _FakeFactory()
    adapter = LangChainModelAdapter(model_factory=factory)

    result = await adapter.invoke(_config(), "secret-not-logged", _request())

    assert result.text == "hello back"
    assert result.response_summary["usage"]["total_tokens"] == 5
    assert factory.kwargs["base_url"] == "https://example.test/v1"
    assert factory.kwargs["max_completion_tokens"] == 128


@pytest.mark.asyncio
async def test_adapter_maps_application_tool_schema_before_binding():
    factory = _FakeFactory()
    adapter = LangChainModelAdapter(model_factory=factory)

    await adapter.invoke(
        _config(),
        "secret-not-logged",
        _request(
            tools=[
                {
                    "name": "inspect",
                    "description": "inspect",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        ),
    )

    bound = factory.model.bound_tools[0][0]
    assert bound["name"] == "inspect"
    assert bound["parameters"]["properties"]["path"]["type"] == "string"


@pytest.mark.asyncio
async def test_adapter_binds_tools_and_preserves_stream_order():
    factory = _FakeFactory()
    chunks: list[str] = []

    async def collect(text: str) -> None:
        chunks.append(text)

    adapter = LangChainModelAdapter(model_factory=factory, stream_handler=collect)

    result = await adapter.invoke(
        _config(),
        "secret-not-logged",
        _request(tools=[{"name": "inspect", "description": "inspect", "input_schema": {"type": "object"}}]),
    )

    assert chunks == ["hello ", "back"]
    assert result.text == "hello back"
    assert factory.model.bound_tools[0][0]["name"] == "inspect"


@pytest.mark.asyncio
async def test_adapter_rejects_unsupported_transport():
    adapter = LangChainModelAdapter(model_factory=_FakeFactory())

    with pytest.raises(ProviderClientError, match="supports only openai_chat_completions"):
        await adapter.invoke(
            _config(transport="anthropic_messages"),
            "secret-not-logged",
            _request(),
        )


class _StructuredRunnable(_FakeRunnable):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def with_structured_output(self, schema, **kwargs):
        parent = self

        class _Structured:
            async def ainvoke(self, messages):
                return parent.payload

        return _Structured()


class _Answer(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_adapter_returns_validated_structured_output():
    factory = _FakeFactory()
    factory.model = _StructuredRunnable({"parsed": _Answer(answer="ok"), "raw": {}, "parsing_error": None})
    adapter = LangChainModelAdapter(model_factory=factory)

    result = await adapter.invoke_structured(_config(), "secret-not-logged", _request(), _Answer)

    assert isinstance(result, _Answer)
    assert result.answer == "ok"


@pytest.mark.asyncio
async def test_adapter_surfaces_structured_output_parse_error():
    factory = _FakeFactory()
    factory.model = _StructuredRunnable({"parsed": None, "raw": {}, "parsing_error": "invalid"})
    adapter = LangChainModelAdapter(model_factory=factory)

    with pytest.raises(ProviderClientError, match="structured-output parsing failed"):
        await adapter.invoke_structured(_config(), "secret-not-logged", _request(), _Answer)


@pytest.mark.asyncio
async def test_adapter_maps_unexpected_provider_failure_to_uniform_error():
    adapter = LangChainModelAdapter(model_factory=_FailingFactory())

    with pytest.raises(ProviderClientError, match="LangChain model invocation failed"):
        await adapter.invoke(_config(), "secret-not-logged", _request())


@pytest.mark.asyncio
async def test_adapter_preserves_direct_rate_limit_status_code():
    adapter = LangChainModelAdapter(model_factory=_RateLimitedFactory())

    with pytest.raises(ProviderClientError) as exc_info:
        await adapter.invoke(_config(), "secret-not-logged", _request())

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_legacy_and_langchain_adapters_share_structured_contract():
    class _LegacyClient:
        async def invoke(self, config, api_key, request):
            return {
                "text": "same semantic result",
                "tool_calls": [],
                "response_id": "legacy",
                "finish_reason": "stop",
                "stop_reason": None,
                "usage": {"total_tokens": 5},
                "raw_response": {},
            }

    modern_factory = _FakeFactory()
    legacy = await LegacyProviderAdapter(_LegacyClient()).invoke(
        _config(), "secret-not-logged", _request()
    )
    modern = await LangChainModelAdapter(model_factory=modern_factory).invoke(
        _config(), "secret-not-logged", _request()
    )

    assert (legacy.response_summary["mode"], modern.response_summary["mode"]) == ("ok", "ok")
    assert len(legacy.tool_calls) == len(modern.tool_calls) == 0
    assert bool(legacy.response_summary["usage"]) == bool(modern.response_summary["usage"])


@pytest.mark.asyncio
async def test_adapter_does_not_swallow_task_cancellation():
    adapter = LangChainModelAdapter(model_factory=_CancelledFactory())

    with pytest.raises(asyncio.CancelledError):
        await adapter.invoke(_config(), "secret-not-logged", _request())


def test_runtime_flag_routes_openai_compatible_model_to_langchain_adapter(monkeypatch):
    calls = []

    class _FlagAdapter:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def invoke(self, config, api_key, request):
            calls.append(("invoke", config.key, bool(api_key)))
            return ModelInvocationResult(text="flagged", response_summary={"mode": "ok"})

    monkeypatch.setattr(
        "src.application.models.model_runtime_service.LangChainModelAdapter",
        _FlagAdapter,
    )
    config = _config(api_key="db-secret")
    registry = SimpleNamespace(get_runtime_config=lambda key: config)
    settings = SimpleNamespace(
        model=SimpleNamespace(
            llm_request_timeout_seconds=30,
            langchain_model_adapter_enabled=True,
        )
    )
    service = ModelRuntimeService(model_registry=registry, settings=settings)

    result = __import__("asyncio").run(service.invoke("qwen-test", _request()))

    assert result.text == "flagged"
    assert calls[1] == ("invoke", "qwen-test", True)

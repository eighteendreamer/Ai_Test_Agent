"""Inspect actual SDK HTTP bytes; model compatibility is an explicit declaration."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

import pytest
from pydantic import ValidationError

from test_model_completion_http import provider, Registry
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.model_clients.request_mapping import map_model_request, UnsupportedModelRequest
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage, model_invocation_parameters
from src.schemas.model_request_options import ModelOutputFormat, ModelReasoningOptions, ModelRequestProfile


SCHEMA = {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}


def runtime_config(url, variant, *, profile=True):
    transport = variant if variant in {"openai_responses", "anthropic_messages", "google_gemini_generate_content"} else "openai_chat_completions"
    reasoning = "effort" if transport.startswith("openai") else "budget"
    declared = ModelRequestProfile(output_formats=["json_schema"] if transport == "anthropic_messages" else ["json_object", "json_schema"],
        chat_token_parameter=("max_tokens" if variant.endswith("legacy") else "max_completion_tokens") if transport == "openai_chat_completions" else None,
        reasoning_style=reasoning, reasoning_efforts=["low", "high"] if reasoning == "effort" else [],
        default_reasoning=ModelReasoningOptions(effort="low") if reasoning == "effort" else ModelReasoningOptions(budget_tokens=1024))
    config = ModelConfigRecord(key="fixture", name="fixture", provider="fixture", transport=transport,
        model_id="fixture-model", api_base_url=url, api_key="local-fixture-marker", max_output_tokens=2048,
        request_profile=declared if profile else None)
    settings = get_settings().model_copy(deep=True)
    settings.model.langchain_model_adapter_enabled = variant.startswith("langchain")
    return config, ModelRuntimeService(Registry(config), settings)


def evidence(name, values):
    if os.getenv("QA_MODEL_REQUEST_PROFILE_DIR"):
        directory = Path(os.environ["QA_MODEL_REQUEST_PROFILE_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (name + ".json")).write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["native_legacy", "native_completion", "langchain_legacy", "langchain_completion",
    "openai_responses", "anthropic_messages", "google_gemini_generate_content"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", ["json_schema", "json_object"])
async def test_actual_profile_fields(variant, streaming, kind):
    transport = variant if variant in {"openai_responses", "anthropic_messages", "google_gemini_generate_content"} else "openai_chat_completions"
    with provider(transport, False) as (url, sends, replies):
        config, runtime = runtime_config(url, variant)
        required = not (transport == "anthropic_messages" and kind == "json_object")
        request = ModelInvocationRequest(system_prompt="Return one JSON object.", messages=[{"role": "user", "content": "{}"}],
            output_format=ModelOutputFormat(kind=kind, schema_definition=SCHEMA if kind == "json_schema" else None, required=required))
        async def delta(text): pass
        if streaming:
            async with runtime.stream_handler(delta):
                result = await runtime.invoke("fixture", request)
        else:
            result = await runtime.invoke("fixture", request)
        evidence(f"{variant}-{kind}-{'stream' if streaming else 'sync'}", {"physical_requests": sends,
            "physical_response": [raw.decode() for raw in replies], "result": result.model_dump(mode="json"),
            "config": model_invocation_parameters(config), "runtime": runtime.get_invocation_runtime_parameters("fixture")})
        assert result.response_summary["mode"] == "ok", result.model_dump()
        assert len(sends) == 1
        body = sends[0]["body"]
        if transport == "openai_chat_completions":
            token = config.request_profile.chat_token_parameter
            assert body[token] == 2048 and ("max_tokens" if token == "max_completion_tokens" else "max_completion_tokens") not in body
            assert body["reasoning_effort"] == "low"
            assert body["response_format"]["type"] == kind
            if kind == "json_schema": assert body["response_format"]["json_schema"]["schema"] == SCHEMA
            assert runtime.get_invocation_runtime_parameters("fixture")["langchain_model_adapter_enabled"] == (variant == "langchain_completion")
        elif transport == "openai_responses":
            assert body["max_output_tokens"] == 2048 and body["reasoning"] == {"effort": "low"}
            assert body["text"]["format"]["type"] == kind
            if kind == "json_schema": assert body["text"]["format"]["schema"] == SCHEMA
        elif transport == "anthropic_messages":
            assert body["max_tokens"] == 2048 and body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
            if kind == "json_schema": assert body["output_config"]["format"]["schema"] == SCHEMA
            else: assert "output_config" not in body
        else:
            generated = body["generationConfig"]
            assert generated["maxOutputTokens"] == 2048 and generated["thinkingConfig"] == {"thinking_budget": 1024}
            assert generated["responseMimeType"] == "application/json"
            if kind == "json_schema": assert generated["responseJsonSchema"] == SCHEMA


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["output_format", "reasoning"])
async def test_required_undeclared_zero_http_and_optional_fallback(option):
    with provider("openai_chat_completions", False) as (url, sends, _):
        config, runtime = runtime_config(url, "native_legacy", profile=False)
        value = ModelOutputFormat(kind="json_object") if option == "output_format" else ModelReasoningOptions(effort="high")
        request = ModelInvocationRequest(system_prompt="Return JSON.", messages=[{"role": "user", "content": "{}"}], **{option: value})
        rejected = await runtime.invoke("fixture", request)
        assert rejected.response_summary["mode"] == "unsupported_request" and not sends
        value.required = False
        result = await runtime.invoke("fixture", request)
        assert len(sends) == 1 and result.response_summary["mode"] == "ok"
        assert result.response_summary["request_mapping"]["decisions"] == [{"option": option, "action": "prompt_fallback",
            "reason": "format_not_declared" if option == "output_format" else "effort_not_declared"}]
        assert "response_format" not in sends[0]["body"] and "reasoning_effort" not in sends[0]["body"]
        evidence(option+"-undeclared", {"rejected": rejected.model_dump(mode="json"), "fallback": result.model_dump(mode="json"), "requests": sends})


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["anthropic_messages", "google_gemini_generate_content"])
async def test_explicit_zero_reasoning_budget(transport):
    with provider(transport, False) as (url, sends, _):
        _, runtime = runtime_config(url, transport)
        result = await runtime.invoke("fixture", ModelInvocationRequest(system_prompt="Return JSON.", messages=[{"role": "user", "content": "{}"}],
            reasoning=ModelReasoningOptions(budget_tokens=0)))
        assert result.response_summary["mode"] == "ok"
        if transport == "anthropic_messages": assert sends[0]["body"]["thinking"] == {"type": "disabled"}
        else: assert sends[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinking_budget": 0}
        evidence(transport+"-budget-zero", {"requests": sends, "result": result.model_dump(mode="json")})


@pytest.mark.asyncio
@pytest.mark.parametrize(("parts", "capabilities", "option"), [
    ([ContentPart(type="image_base64", data_base64="aGVsbG8=", mime_type="image/png")], {}, "vision"),
    ([ContentPart(type="image_base64", data_base64="aGVsbG8=", mime_type="image/png")],
        {"vision": True, "image_base64_input": False}, "image_base64_input"),
    ([ContentPart(type="image_url", url="https://img.test/a.png")],
        {"vision": True, "image_url_input": False}, "image_url_input"),
    ([ContentPart(type="image_url", url="https://img.test/a.png"),
        ContentPart(type="image_url", url="https://img.test/b.png")],
        {"vision": True, "image_url_input": True, "multi_image": False}, "multi_image"),
    ([ContentPart(type="file", file_name="spec.pdf", mime_type="application/pdf", data_base64="cGRm")],
        {}, "pdf_input"),
    ([ContentPart(type="file", file_name="notes.txt", mime_type="text/plain", data_base64="bm90ZXM=")],
        {"file_input": True}, "file_input"),
])
async def test_unsupported_media_is_rejected_before_provider_send(parts, capabilities, option):
    with provider("openai_chat_completions", False) as (url, sends, _):
        config, runtime = runtime_config(url, "native_completion")
        updated = config.capabilities.model_copy(update=capabilities)
        config = config.model_copy(update={"supports_vision": updated.vision, "capabilities": updated})
        runtime._model_registry = Registry(config)
        request = ModelInvocationRequest(system_prompt="Inspect the supplied media.", messages=[],
            structured_messages=[UnifiedMessage(role="user", parts=parts)])
        result = await runtime.invoke("fixture", request)
        assert result.response_summary["mode"] == "unsupported_request"
        assert result.response_summary["option"] == option
        assert not sends


@pytest.mark.asyncio
async def test_declared_single_image_is_sent_with_native_openai_chat_shape():
    with provider("openai_chat_completions", False) as (url, sends, _):
        config, runtime = runtime_config(url, "native_completion")
        capabilities = config.capabilities.model_copy(update={
            "vision": True, "image_url_input": True, "multi_image": False,
        })
        config = config.model_copy(update={"supports_vision": True, "capabilities": capabilities})
        runtime._model_registry = Registry(config)
        request = ModelInvocationRequest(system_prompt="Describe the image.", messages=[],
            structured_messages=[UnifiedMessage(role="user", parts=[
                ContentPart(type="image_url", url="https://img.test/a.png"),
            ])])
        result = await runtime.invoke("fixture", request)
        assert result.response_summary["mode"] == "ok"
        assert len(sends) == 1
        assert sends[0]["body"]["messages"][-1]["content"] == [
            {"type": "image_url", "image_url": {"url": "https://img.test/a.png"}},
        ]


@contextmanager
def rejected_provider():
    sends = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            sends.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"error": {"message": "Declared parameter rejected by controlled model", "type": "invalid_request_error"}}).encode()
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}/v1", sends
    finally: server.shutdown(); server.server_close(); thread.join(5)


@pytest.mark.asyncio
async def test_http_400_is_not_reissued_with_other_fields():
    with rejected_provider() as (url, sends):
        _, runtime = runtime_config(url, "langchain_completion")
        result = await runtime.invoke("fixture", ModelInvocationRequest(system_prompt="Return JSON.", messages=[{"role": "user", "content": "{}"}],
            output_format=ModelOutputFormat(kind="json_object")))
        evidence("http-400", {"requests": sends, "result": result.model_dump(mode="json")})
        assert len(sends) == 1 and result.response_summary["mode"] == "http_error"
        assert result.response_summary["status_code"] == 400


def test_legacy_serialization_and_strict_profile():
    request = ModelInvocationRequest(system_prompt="", messages=[])
    assert "output_format" not in request.model_dump() and "reasoning" not in request.model_dump()
    config, _ = runtime_config("http://127.0.0.1/unused", "native_legacy", profile=False)
    assert "request_profile" not in model_invocation_parameters(config)
    with pytest.raises(ValidationError): ModelRequestProfile.model_validate({"extra_body": {"api_key": "not-accepted"}})
    with pytest.raises(ValidationError): ModelReasoningOptions(effort="low", budget_tokens=1)
    with pytest.raises(ValidationError): ModelOutputFormat(kind="json_schema")


def test_optional_schema_declared_json_object_fallback_and_budget_bounds():
    config, _ = runtime_config("http://127.0.0.1/unused", "native_legacy")
    config.request_profile.output_formats = ["json_object"]
    mapped = map_model_request(config, ModelInvocationRequest(system_prompt="Return JSON.", messages=[],
        output_format=ModelOutputFormat(kind="json_schema", schema_definition=SCHEMA, required=False)))
    assert mapped.parameters["response_format"] == {"type": "json_object"}
    assert mapped.decisions[0]["action"] == "json_object_fallback"
    config, _ = runtime_config("http://127.0.0.1/unused", "anthropic_messages")
    for budget in (1, 1023, 2048):
        with pytest.raises(UnsupportedModelRequest): map_model_request(config, ModelInvocationRequest(system_prompt="", messages=[],
            reasoning=ModelReasoningOptions(budget_tokens=budget)))


@pytest.mark.asyncio
async def test_langchain_exported_model_keeps_declared_default_and_exact_legacy_token():
    with provider("openai_chat_completions", False) as (url, sends, _):
        from langchain_core.messages import HumanMessage
        config, runtime = runtime_config(url, "langchain_completion")
        model = await runtime.resolve_langchain_chat_model("fixture")
        await model.ainvoke([HumanMessage(content="Return JSON.")])
        assert len(sends) == 1 and sends[0]["body"]["reasoning_effort"] == "low"
        assert sends[0]["body"]["max_completion_tokens"] == 2048
        config.request_profile.chat_token_parameter = "max_tokens"
        model = await runtime.resolve_langchain_chat_model("fixture")
        await model.ainvoke([HumanMessage(content="Return JSON.")])
        assert len(sends) == 2
        assert sends[1]["body"]["max_tokens"] == 2048
        assert "max_completion_tokens" not in sends[1]["body"]
        assert sends[1]["body"]["reasoning_effort"] == "low"
        evidence("langchain-exported-default", {"requests": sends, "legacy_harness_exact_token_field": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["native_completion", "langchain_completion"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_format_binding_keeps_existing_tools(variant, streaming):
    with provider("openai_chat_completions", False) as (url, sends, _):
        _, runtime = runtime_config(url, variant)
        request = ModelInvocationRequest(system_prompt="Return JSON.",
            messages=[{"role": "user", "content": "{}"}], output_format=ModelOutputFormat(kind="json_object"),
            tools=[{"name": "inspect", "description": "Read owned evidence", "input_schema": {"type": "object", "properties": {}}}])
        async def delta(text): pass
        if streaming:
            async with runtime.stream_handler(delta): result = await runtime.invoke("fixture", request)
        else: result = await runtime.invoke("fixture", request)
        evidence(variant+f"-{'stream' if streaming else 'sync'}-tools-and-format", {"requests": sends, "result": result.model_dump(mode="json")})
        assert result.response_summary["mode"] == "ok" and len(sends) == 1, result.model_dump()
        assert sends[0]["body"]["tools"][0]["function"]["name"] == "inspect"
        assert sends[0]["body"]["response_format"] == {"type": "json_object"}


def test_previous_rustfs_generation_snapshot_hashes_still_validate():
    from src.schemas.case_generation import IncrementalGenerationScope
    root = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-model-stream-terminal-generation/guards"
    facts = json.loads((root / "verification.json").read_bytes())
    old = IncrementalGenerationScope.model_validate_json((root / "scope.json").read_bytes())
    assert old.authoring.sha256 and len(old.modules) > 0
    assert all("output_format" not in module.generation.invocation.model_dump() for module in old.modules)
    assert facts["guard_send_count"] == 0


def _model_config_row(provider_name, transport=None, capability_overrides=None):
    return {
        "id": 1,
        "model_name": f"fixture-{provider_name}",
        "api_key": "",
        "base_url": "https://models.example/v1",
        "provider": provider_name,
        "transport": transport,
        "capability_overrides": json.dumps(capability_overrides) if capability_overrides is not None else None,
        "is_active": 1,
        "created_at": None,
        "updated_at": None,
        "auth_type": "api_key",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "request_profile": None,
    }


@pytest.mark.parametrize(
    ("provider_name", "transport", "expected_vision", "expected_multi_image", "expected_image_url"),
    [
        ("deepseek", "openai_chat_completions", False, False, True),
        ("anthropic", "anthropic_messages", True, False, False),
        ("openai", "anthropic_messages", True, False, False),
        ("google", "google_gemini_generate_content", True, True, False),
    ],
)
def test_model_capabilities_follow_protocol_provider_defaults(
    provider_name, transport, expected_vision, expected_multi_image, expected_image_url,
):
    record = MySQLModelConfigStore(get_settings())._row_to_record(
        _model_config_row(provider_name, transport)
    )

    assert record.capabilities.vision is expected_vision
    assert record.capabilities.multi_image is expected_multi_image
    assert record.capabilities.image_url_input is expected_image_url
    assert record.capabilities.tool_calling is True
    assert record.capabilities.streaming is True
    assert record.capabilities.parallel_tool_calls is False


def test_sparse_model_capability_overrides_refine_provider_defaults():
    record = MySQLModelConfigStore(get_settings())._row_to_record(
        _model_config_row("deepseek", "openai_chat_completions", {"tool_calling": False, "vision": True})
    )

    assert record.capability_overrides.tool_calling is False
    assert record.capability_overrides.vision is True
    assert record.capabilities.tool_calling is False
    assert record.capabilities.vision is True
    assert record.capabilities.reasoning is True
    assert record.capabilities.streaming is True

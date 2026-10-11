"""Use the actual failed default generation scope, preserving old hashes."""
from contextlib import asynccontextmanager
import json
from pathlib import Path
import os
from uuid import uuid4

import pytest

from src.application.test_cases.generation_pipeline import ModelTestCaseGenerator
from src.schemas.case_generation import fixed_generation_snapshot
from src.schemas.model_config import ModelInvocationResult

ROOT = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def captured_scope():
    from src.schemas.document_case_generation import DocumentGenerationScope
    raw = json.loads((ROOT / "p1-generation-default-current-verified/scope.json").read_bytes())
    return raw, DocumentGenerationScope.model_validate(raw)


class Models:
    def __init__(self, response=None, fail=False):
        self.streaming = False
        self.calls = []
        self.response = response or ModelInvocationResult(text='{"cases":[{"case_key":"source-case","title":"注册","case_type":"json","content_json":{"objective":"邮箱唯一"}}]}',
            response_summary={"mode": "ok", "finish_reason": "stop"})
        self.fail = fail

    @asynccontextmanager
    async def stream_handler(self, handler):
        assert not self.streaming
        self.streaming = True
        try:
            await handler("public fragment")
            yield
        finally:
            self.streaming = False

    async def invoke(self, key, request, **options):
        self.calls.append({"streaming": self.streaming, "key": key, "request": request, "options": options})
        if self.fail:
            raise TimeoutError("actual captured failure category")
        return self.response


def test_actual_old_scope_and_requests_preserve_exact_serialization_and_hashes():
    raw, scope = captured_scope()
    assert scope.model_dump(mode="json") == raw
    for module in scope.modules:
        assert module.generation.response_transport_protocol is None


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False])
async def test_new_document_uses_frozen_capability_and_original_runtime_budget(streaming):
    _, scope = captured_scope()
    old = scope.modules[0].generation
    authoring = old.authoring.model_dump(mode="json", exclude={"sha256"})
    authoring["model_parameters"]["capabilities"]["streaming"] = streaming
    authoring["skills"] = old.authoring.skills
    fixed = fixed_generation_snapshot(type(old.authoring), **authoring)
    models = Models()
    generator = ModelTestCaseGenerator(model_runtime_service=models, skill_runtime_service=None)
    prepared = await generator.prepare_frozen(request=old.request,
        context=json.loads(old.invocation.messages[0]["content"])["evidence_context"], authoring=fixed)
    result = await generator.invoke_frozen(prepared)
    assert result is models.response and len(models.calls) == 1
    assert models.calls[0]["streaming"] is streaming and not models.streaming
    assert prepared.authoring.runtime_parameters == old.authoring.runtime_parameters
    assert models.calls[0]["options"]["expected_parameters"] == fixed.model_parameters
    assert models.calls[0]["options"]["expected_runtime_parameters"] == fixed.runtime_parameters
    assert generator.parse_frozen(prepared, result).cases[0].content_json == {"objective": "邮箱唯一"}
    assert ("response_transport_protocol" in prepared.model_dump(mode="json")) is streaming
    await generator.invoke_frozen(old)
    assert not models.calls[-1]["streaming"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "truncated"])
async def test_new_stream_does_not_retry_or_accept_partial_public_output(failure):
    _, scope = captured_scope()
    old = scope.modules[0].generation
    models = Models(fail=failure == "timeout", response=ModelInvocationResult(text='{"cases":[',
        response_summary={"mode": "incomplete_response", "finish_reason": "length"}))
    generator = ModelTestCaseGenerator(model_runtime_service=models, skill_runtime_service=None)
    prepared = await generator.prepare_frozen(request=old.request,
        context=json.loads(old.invocation.messages[0]["content"])["evidence_context"], authoring=old.authoring)
    if failure == "timeout":
        with pytest.raises(TimeoutError):
            await generator.invoke_frozen(prepared)
    else:
        result = await generator.invoke_frozen(prepared)
        with pytest.raises(ValueError, match="截断"):
            generator.parse_frozen(prepared, result)
    assert len(models.calls) == 1 and not models.streaming


@pytest.mark.asyncio
async def test_actual_native_sdk_timeout_records_each_retry_without_changing_body(tmp_path):
    from default_model_probe import install
    from test_document_impact_calls import provider
    from src.application.model_clients.base import ProviderClientError
    from src.application.model_clients.openai_client import OpenAIChatClient
    from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest

    directory = Path(os.getenv("QA_GENERATION_TIMEOUT_PROBE_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    original = install(directory)
    secret = uuid4().hex
    try:
        # Real local HTTP holds headers; the actual native SDK makes its retry.
        # The existing provider releases all handlers in finally.
        with provider(gated=True) as (endpoint, calls, _, _):
            config = ModelConfigRecord(key="timeout-probe", name="timeout-probe", provider="openai",
                transport="openai_chat_completions", model_id="timeout-probe", api_base_url=endpoint,
                max_output_tokens=80)
            with pytest.raises(ProviderClientError):
                await OpenAIChatClient(timeout_seconds=0.15, max_retries=1).invoke(config, secret,
                    ModelInvocationRequest(system_prompt="Physical retry fixture",
                        messages=[{"role": "user", "content": "Physical retry fixture"}]))
            assert len(calls) == 2 and calls[0] == calls[1]
        attempts = [json.loads(p.read_bytes()) for p in directory.glob("*.attempt.json")]
        sdk = [json.loads(p.read_bytes()) for p in directory.glob("*.sdk-create.json")]
        requests = [p.read_bytes() for p in directory.glob("*.request.json")]
        assert len(attempts) == len(requests) == 2 and requests[0] == requests[1]
        assert len(sdk) == 1 and len(sdk[0]["physical_requests"]) == 2
        assert sdk[0]["error_type"] == "APITimeoutError"
        assert all(a["error_type"] == "ReadTimeout" and not a["response_headers_received"]
            and not a["body_read_by_probe"] and not a["request_headers_saved"] for a in attempts)
        assert not list(directory.glob("*.response.json"))
        assert secret not in "".join(p.read_text(encoding="utf-8") for p in directory.glob("*.json"))
    finally:
        OpenAIChatClient._make_client = original

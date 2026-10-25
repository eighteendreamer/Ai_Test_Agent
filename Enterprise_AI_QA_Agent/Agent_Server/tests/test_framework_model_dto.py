from __future__ import annotations

import asyncio
import json

import pytest

from src.runtime.execution_context import framework_owner_scope
from src.schemas.model_config import ModelInvocationRequest, ModelInvocationResult, ModelTransportOptions
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider
from tests.test_framework_model_policy import governed, execute
from tests.test_framework_model_admission import admission, submit
from tests.test_runtime_binding_persistence import make_command


PATHS = (*TRANSPORTS, "langchain_chat")


def dto_request(h, *, logical="dto-fixture"):
    return ModelInvocationRequest(system_prompt="Return the fixture answer.",
        messages=[{"role": "user", "content": "fixture request"}],
        trace_context={"session_id": h.session_id, "turn_id": h.binding.turn_id,
            "logical_call_id": logical, "agent_key": "semantic-intent-classifier"})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_owner_bound_dto_request_uses_actual_sdk_and_shared_root_budget(harness, path, streaming):
    transport = "openai_chat_completions" if path == "langchain_chat" else path
    with _provider(transport) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            h.settings.model.langchain_model_adapter_enabled = path == "langchain_chat"
            h.models.set_framework_model_call_policy(h.policy)
            deltas = []

            async def receive(delta):
                deltas.append(delta)

            with framework_owner_scope(h.owner.runtime_owner):
                async with h.models.stream_handler(receive if streaming else None):
                    response = await h.models.invoke(h.request.model_key, dto_request(h))
            assert response.text == "fixture answer" and len(requests) == 1
            assert response.response_summary["http_request_attempt_count"] == 1
            assert bool(deltas) is streaming
            wire = requests[0]["body"]
            assert requests[0]["fixture_header"] == "framework"
            if transport == "google_gemini_generate_content":
                assert wire["generationConfig"]["temperature"] == 0.2
                assert wire["generationConfig"]["maxOutputTokens"] == 512
                assert (":streamGenerateContent" in requests[0]["path"]) is streaming
            else:
                assert wire["temperature"] == 0.2
                token_field = ("max_completion_tokens" if path == "langchain_chat" else
                    "max_output_tokens" if transport == "openai_responses" else "max_tokens")
                assert wire[token_field] == 512
                assert requests[0]["path"] == {
                    "openai_chat_completions": "/v1/chat/completions",
                    "openai_responses": "/v1/responses", "anthropic_messages": "/v1/messages"}[transport]
            snapshot = await h.calls.snapshot(h.binding)
            assert snapshot.reserved_calls == 1 and snapshot.actual_tokens == 10
            assert snapshot.calls[0].phase == "completed"
            assert snapshot.calls[0].intent.transport == transport
            await execute(h)
            snapshot = await h.calls.snapshot(h.binding)
            assert len(requests) == snapshot.reserved_calls == 2 and snapshot.actual_tokens == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_governed_dto_http_error_has_no_hidden_retry_and_cannot_be_replayed(harness, path):
    transport = "openai_chat_completions" if path == "langchain_chat" else path
    with _provider(transport, status=503) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            h.settings.model.langchain_model_adapter_enabled = path == "langchain_chat"
            h.models.set_framework_model_call_policy(h.policy)
            with framework_owner_scope(h.owner.runtime_owner):
                result = await h.models.invoke(h.request.model_key, dto_request(h))
                assert result.response_summary["mode"] == "http_error"
                snapshot = await h.calls.snapshot(h.binding)
                assert len(requests) == snapshot.unknown_calls == snapshot.reserved_calls == 1
                with pytest.raises(ValueError, match="retry"):
                    await h.models.invoke(h.request.model_key, dto_request(h))
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot


@pytest.mark.asyncio
async def test_dto_exhaustion_and_missing_policy_reject_before_provider_request(harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), max_calls=1) as h:
            with framework_owner_scope(h.owner.runtime_owner):
                with pytest.raises(RuntimeError, match="policy"):
                    await h.models.invoke(h.request.model_key, dto_request(h))
            assert requests == []
            h.models.set_framework_model_call_policy(h.policy)
            await execute(h)
            with framework_owner_scope(h.owner.runtime_owner):
                with pytest.raises(FrameworkModelBudgetExceeded, match="calls"):
                    await h.models.invoke(h.request.model_key, dto_request(h))
            assert len(requests) == 1


@pytest.mark.asyncio
async def test_dto_policy_does_not_charge_unbound_legacy_call(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            before = await h.calls.snapshot(h.binding)
            result = await h.models.invoke(h.request.model_key, dto_request(h))
            assert result.text == "fixture answer" and len(requests) == 1
            assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_claimed_input_semantic_enrichment_inherits_captured_owner_and_budget(admission):
    from src.application.intent.semantic_intent_service import SemanticIntentService
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from src.runtime.execution_context import current_framework_owner
    from tests.test_framework_chat_models import _reply

    h = admission
    candidate = {"target_kind": "ui", "objectives": ["ui_automation"], "requested_actions": ["read"],
        "required_capabilities": ["ui.automation"], "candidate_mode_key": "ui_automation",
        "confidence": 0.91, "needs_clarification": False, "evidence": ["local HTTP fixture"]}
    reply = _reply("openai_chat_completions")
    reply["choices"][0]["message"]["content"] = json.dumps(candidate)
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        config = _config("openai_chat_completions", url)

        class Registry:
            def get_runtime_config(self, key):
                assert key == config.key
                return config

        models = ModelRuntimeService(Registry(), h.settings)
        models.set_framework_model_call_policy(FrameworkModelCallPolicy(calls=h.calls,
            model_config_resolver=models.get_model_config))
        h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
        h.command = make_command(h, h.binding)
        h.payload = h.payload.model_copy(update={"model_key": config.key})
        h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
        task = await submit(h)
        owner = await h.tasks.claim(task.id, worker_id="semantic-owner", expected_binding=h.binding)
        await h.tasks.begin(owner, resource_id="semantic-fixture", fencing_token=1)
        orchestrator = InputOrchestratorService(ModeRegistry(), SemanticIntentService(models,
            deterministic_confidence_threshold=1.1))
        h.sessions._input_orchestrator_service = orchestrator
        monitor = asyncio.create_task(asyncio.Event().wait())
        try:
            await h.sessions.execute_claimed_message(owner, ownership_monitor=monitor)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        snapshot = await h.calls.snapshot(h.binding)
        assert len(requests) == snapshot.reserved_calls == 1
        assert snapshot.actual_tokens == 10 and snapshot.calls[0].owner_epoch == owner.runtime_owner.owner_epoch
        assert current_framework_owner() is None


@pytest.mark.asyncio
async def test_governed_dto_rejects_implicit_retry_and_foreign_turn_before_http(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            with framework_owner_scope(h.owner.runtime_owner):
                with pytest.raises(ValueError, match="retries"):
                    await h.models.invoke(h.request.model_key, dto_request(h),
                        transport_options=ModelTransportOptions(timeout_seconds=5, max_retries=1))
                request = dto_request(h)
                request.trace_context["turn_id"] = "other-turn"
                with pytest.raises(ValueError, match="identity"):
                    await h.models.invoke(h.request.model_key, request)
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
async def test_governed_dto_bypasses_existing_langchain_cache(harness):
    from langchain_core.caches import InMemoryCache
    from langchain_core.globals import get_llm_cache, set_llm_cache

    original_cache = get_llm_cache()
    set_llm_cache(InMemoryCache())
    try:
        with _provider("openai_chat_completions") as (url, requests):
            async with governed(harness, _config("openai_chat_completions", url)) as h:
                h.settings.model.langchain_model_adapter_enabled = True
                h.models.set_framework_model_call_policy(h.policy)
                request = dto_request(h)
                await h.models.invoke(h.request.model_key, request,
                    transport_options=ModelTransportOptions(timeout_seconds=5, max_retries=0))
                with framework_owner_scope(h.owner.runtime_owner):
                    await h.models.invoke(h.request.model_key, request,
                        transport_options=ModelTransportOptions(timeout_seconds=5, max_retries=0))
                assert len(requests) == 2
                assert (await h.calls.snapshot(h.binding)).actual_tokens == 10
    finally:
        set_llm_cache(original_cache)


@pytest.mark.parametrize("usage,transport,expected", [
    ({}, "openai_chat_completions", None),
    ({"input_tokens": 3}, "anthropic_messages", None),
    ({"input_tokens": 0, "output_tokens": 0}, "anthropic_messages", (0, 0, 0)),
    ({"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 5}, "anthropic_messages", (22, 3, 25)),
    ({"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 15},
        "google_gemini_generate_content", (7, 3, 15)),
    ({"input_tokens": 7, "output_tokens": 3, "total_tokens": 9}, "openai_responses", None),
])
def test_dto_usage_preserves_missing_counters_cache_and_thinking(usage, transport, expected):
    from src.application.models.framework_model_call_policy import dto_model_usage

    actual = dto_model_usage(ModelInvocationResult(text="fixture", response_summary={"usage": usage}), transport=transport)
    assert (tuple((actual.input_tokens, actual.output_tokens, actual.total_tokens)) if actual else None) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_streaming_dto_cancellation_keeps_unknown_attempt_and_reserved_budget(harness, path):
    from tests.test_model_sdk_fault_http import fault_gateway

    transport = "openai_chat_completions" if path == "langchain_chat" else path
    with fault_gateway(transport, "cancel") as (url, requests, replies, errors):
        async with governed(harness, _config(transport, url)) as h:
            h.settings.model.langchain_model_adapter_enabled = path == "langchain_chat"
            h.models.set_framework_model_call_policy(h.policy)
            received = asyncio.Event()

            async def receive(_delta):
                received.set()

            with framework_owner_scope(h.owner.runtime_owner):
                async with h.models.stream_handler(receive):
                    task = asyncio.create_task(h.models.invoke(h.request.model_key, dto_request(h)))
                    try:
                        await asyncio.wait_for(received.wait(), 10)
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            snapshot = await h.calls.snapshot(h.binding)
            assert len(requests) == snapshot.reserved_calls == snapshot.unknown_calls == 1
            assert snapshot.charged_tokens == snapshot.calls[0].intent.reserved_tokens
            assert snapshot.calls[0].outcome_code == "request_cancelled" and not errors


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 1])
async def test_unbound_google_explicit_retry_options_count_total_actual_requests(retries):
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.core.config import get_settings

    with _provider("google_gemini_generate_content", status=503) as (url, requests):
        config = _config("google_gemini_generate_content", url)

        class Registry:
            def get_runtime_config(self, key):
                assert key == config.key
                return config

        models = ModelRuntimeService(Registry(), get_settings())
        result = await models.invoke(config.key, ModelInvocationRequest(system_prompt="fixture",
            messages=[{"role": "user", "content": "fixture request"}]),
            transport_options=ModelTransportOptions(timeout_seconds=5, max_retries=retries))
        assert result.response_summary["mode"] == "http_error"
        assert len(requests) == result.response_summary["http_request_attempt_count"] == retries + 1

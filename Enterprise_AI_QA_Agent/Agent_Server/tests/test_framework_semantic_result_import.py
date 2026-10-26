"""Consume a settled semantic DTO through the application without a second send."""
from __future__ import annotations

import json

import pytest

from src.application.intent.semantic_intent_service import SemanticIntentService
from src.runtime.execution_context import framework_owner_scope
from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.intent import IntentDecision
from src.schemas.semantic_input import PreparedSemanticInput
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _reply
from tests.test_framework_model_policy import governed
from tests.test_framework_model_result_import import wire_evidence


def semantic_reply(transport, *, valid=True):
    response = _reply(transport)
    text = json.dumps({"target_kind": "ui", "objectives": ["ui_automation"],
        "requested_actions": ["read"], "required_capabilities": ["ui.automation"],
        "candidate_mode_key": "ui_automation", "confidence": 0.91,
        "needs_clarification": False, "evidence": ["local HTTP fixture"]}) if valid else "invalid semantic response"
    if transport == "openai_chat_completions":
        response["choices"][0]["message"]["content"] = text
    elif transport == "openai_responses":
        response["output"][0]["content"][0]["text"] = text
    elif transport == "anthropic_messages":
        response["content"][0]["text"] = text
    else:
        response["candidates"][0]["content"]["parts"][0]["text"] = text
    return response


def prepared_input(h, *, baseline=None):
    service = SemanticIntentService(h.models, deterministic_confidence_threshold=1.1)
    prepared = service.prepare(message="Inspect this interface", baseline=baseline or IntentDecision(),
        model_key=h.request.model_key, freeze_runtime=True,
        trace_context={"session_id": h.session_id, "turn_id": h.binding.turn_id,
            "logical_call_id": "input_orchestration:semantic_intent:v1"})
    return service, prepared


def restore_arguments(prepared, call):
    return {"expected": call, "expected_parameters": prepared.invocation_parameters,
        "expected_runtime_parameters": prepared.runtime_parameters,
        "transport_options": prepared.transport_options}


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_completed_semantic_response_uses_original_assets_and_never_sends_again(harness, monkeypatch, transport):
    with _provider(transport, first_reply=semantic_reply(transport)) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            service, prepared = prepared_input(h)
            with framework_owner_scope(h.owner.runtime_owner):
                original = await service.enrich_prepared(prepared)
                before = await h.calls.snapshot(h.binding)
                call, = before.calls
                assert original.candidate_mode_key == "ui_automation"

                from src.application.models.model_runtime_service import ModelRuntimeService

                # A replacement service has no original in-process response.
                h.models = ModelRuntimeService(h.registry, h.settings)
                h.models.set_framework_model_call_policy(h.policy)
                service = SemanticIntentService(h.models, enabled=False)

                async def no_send(*args, **kwargs):
                    pytest.fail("Completed semantic import must not authenticate or invoke the provider")

                monkeypatch.setattr(h.models, "invoke", no_send)
                monkeypatch.setattr(h.models, "_resolve_auth_token", no_send)
                for name in ("_system_prompt", "_resolve_model_key", "_should_invoke"):
                    monkeypatch.setattr(service, name, lambda *args: pytest.fail("Frozen preparation must not be rebuilt"))
                for _ in range(2):
                    restored = await service.enrich_prepared(prepared, completed_call=call)
                    assert restored == original
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [False, True])
async def test_reading_completed_stream_does_not_emit_live_tokens_or_repeat_http(harness, monkeypatch, adapter):
    transport = "openai_chat_completions"
    with _provider(transport) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            h.settings.model.langchain_model_adapter_enabled = adapter
            wire_evidence(h, monkeypatch)
            _, prepared = prepared_input(h)
            received = []

            async def chunk(value):
                received.append(value)

            with framework_owner_scope(h.owner.runtime_owner):
                async with h.models.stream_handler(chunk):
                    original = await h.models.invoke(prepared.model_key, prepared.request,
                        expected_parameters=prepared.invocation_parameters,
                        expected_runtime_parameters=prepared.runtime_parameters,
                        transport_options=prepared.transport_options)
                    assert received
                    before = await h.calls.snapshot(h.binding)
                    received.clear()
                    restored = await h.models.restore_completed(prepared.model_key, prepared.request,
                        **restore_arguments(prepared, before.calls[0]))
            assert restored.text == original.text and restored.raw_response == original.raw_response
            assert received == [] and len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_restored_semantic_result_preserves_existing_fallback_and_protected_intent(harness, monkeypatch, protected):
    transport = "openai_chat_completions"
    with _provider(transport, first_reply=semantic_reply(transport, valid=protected)) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            baseline = IntentDecision(objectives=["security"], candidate_mode_key="security_testing") if protected else IntentDecision()
            service, prepared = prepared_input(h, baseline=baseline)
            with framework_owner_scope(h.owner.runtime_owner):
                original = await service.enrich_prepared(prepared)
                before = await h.calls.snapshot(h.binding)
                restored = await service.enrich_prepared(prepared, completed_call=before.calls[0])
            assert restored == original
            assert restored.candidate_mode_key == baseline.candidate_mode_key
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["request", "model", "runtime", "evidence", "owner", "unsettled"])
async def test_semantic_import_failure_cannot_turn_into_fallback_or_another_request(harness, monkeypatch, changed):
    transport = "openai_chat_completions"
    with _provider(transport, first_reply=semantic_reply(transport)) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            service, prepared = prepared_input(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await service.enrich_prepared(prepared)
                before = await h.calls.snapshot(h.binding)
                call = before.calls[0]
                if changed == "request":
                    prepared.request.system_prompt += " changed after settlement"
                elif changed == "model":
                    h.registry.current = h.registry.current.model_copy(update={"temperature": 0.7})
                elif changed == "runtime":
                    h.settings.model.llm_request_timeout_seconds += 1
                elif changed == "evidence":
                    async def unavailable(*args):
                        raise ConnectionError("injected original response storage failure")
                    monkeypatch.setattr(h.evidence, "read", unavailable)
                elif changed == "owner":
                    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
                else:
                    call = call.model_copy(update={"phase": "invoking", "settled_at": None, "settled_response_id": None})
                with pytest.raises((ValueError, ConnectionError, ContinuationLeaseLostError, FrameworkModelRecoveryRequired)):
                    await service.enrich_prepared(prepared, completed_call=call)
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["skip", "unfrozen"])
async def test_completed_semantic_result_requires_its_frozen_request(harness, monkeypatch, preparation):
    transport = "openai_chat_completions"
    with _provider(transport, first_reply=semantic_reply(transport)) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            service, prepared = prepared_input(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await service.enrich_prepared(prepared)
                before = await h.calls.snapshot(h.binding)
                if preparation == "skip":
                    prepared = PreparedSemanticInput(baseline=prepared.baseline, skip_reason="deterministic")
                else:
                    prepared = prepared.model_copy(update={"invocation_parameters": None,
                        "runtime_parameters": None, "transport_options": None})
                with pytest.raises(ValueError, match="[Ff]rozen"):
                    await service.enrich_prepared(prepared, completed_call=before.calls[0])
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["owner", "policy", "parameters", "runtime", "model", "retries"])
async def test_model_result_read_requires_execution_authority_and_original_contract(harness, monkeypatch, missing):
    transport = "openai_chat_completions"
    with _provider(transport, first_reply=semantic_reply(transport)) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            service, prepared = prepared_input(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await service.enrich_prepared(prepared)
            before = await h.calls.snapshot(h.binding)
            arguments = restore_arguments(prepared, before.calls[0])
            if missing == "policy":
                h.models.set_framework_model_call_policy(None)
            elif missing == "parameters":
                arguments["expected_parameters"] = None
            elif missing == "runtime":
                arguments["expected_runtime_parameters"] = None
            elif missing == "model":
                monkeypatch.setattr(h.models, "get_model_config", lambda key: None)
            elif missing == "retries":
                arguments["transport_options"] = prepared.transport_options.model_copy(update={"max_retries": 1})
            with framework_owner_scope(None if missing == "owner" else h.owner.runtime_owner):
                with pytest.raises((ValueError, RuntimeError)):
                    await h.models.restore_completed(prepared.model_key, prepared.request, **arguments)
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before

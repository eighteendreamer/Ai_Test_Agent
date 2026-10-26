"""Complete the application phase from its original semantic input/result."""
from __future__ import annotations

import json

import pytest

from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.registry.modes import ModeRegistry
from src.runtime.execution_context import framework_owner_scope
from src.schemas.intent import IntentDecision
from src.schemas.semantic_input import PreparedSemanticInput
from src.schemas.session import SendMessageRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider
from tests.test_framework_model_policy import governed
from tests.test_framework_model_result_import import wire_evidence
from tests.test_framework_semantic_input import Runtime
from tests.test_framework_semantic_result_import import semantic_reply
from tests.test_intent_safety_routing import _session


def frozen_input(session, payload, semantic):
    return semantic.prepare(message=" ".join(payload.content.split()), baseline=IntentDecision(),
        model_key=payload.model_key, freeze_runtime=True,
        trace_context={"session_id": session.id, "turn_id": "original-turn", "trace_id": "original-trace",
            "logical_call_id": "input_orchestration:semantic_intent:v1"})


@pytest.mark.asyncio
async def test_prepared_orchestration_skips_reclassification_and_preserves_execution_identity(monkeypatch):
    runtime = Runtime()
    semantic = SemanticIntentService(runtime)
    service = InputOrchestratorService(ModeRegistry(), semantic)
    session = _session()
    payload = SendMessageRequest(content=" review  fixture ", model_key=runtime.config.key)
    prepared = frozen_input(session, payload, semantic)

    def never(*args, **kwargs):
        pytest.fail("frozen orchestration must not rebuild semantic preparation or baseline")

    monkeypatch.setattr(service._intent_recognition_service, "recognize", never)
    monkeypatch.setattr(semantic, "prepare", never)
    execution = await service.orchestrate_prepared(session, payload, prepared,
        turn_id="original-turn", trace_id="original-trace")
    assert execution.turn_id == "original-turn" and execution.trace_id == "original-trace"
    assert execution.context["intent_decision"]["candidate_mode_key"] == "code_review"
    assert len(runtime.requests) == 1 and runtime.requests[0][1] == prepared.request


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
@pytest.mark.parametrize("skip", ["deterministic", "model_unavailable", "service_unavailable"])
async def test_saved_skip_survives_service_availability_changes_without_model_execution(monkeypatch, skip, available):
    service = InputOrchestratorService(ModeRegistry(), SemanticIntentService(Runtime()) if available else None)
    prepared = PreparedSemanticInput(baseline=IntentDecision(evidence=["original baseline"]), skip_reason=skip)

    async def never(*args, **kwargs):
        pytest.fail("a persisted skip must not consult the new semantic service")

    if available:
        monkeypatch.setattr(service._semantic_intent_service, "enrich_prepared", never)
    execution = await service.orchestrate_prepared(_session(), SendMessageRequest(content="review fixture"),
        prepared, turn_id="original-turn", trace_id="original-trace")
    assert execution.context["intent_decision"] == prepared.baseline.model_dump(mode="python")


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["session", "turn", "trace", "content", "unfrozen", "missing_service",
    "empty_turn", "empty_trace"])
async def test_prepared_orchestration_rejects_different_identity_before_model_or_projection(monkeypatch, changed):
    runtime = Runtime()
    semantic = SemanticIntentService(runtime)
    service = InputOrchestratorService(ModeRegistry(), semantic)
    session = _session()
    payload = SendMessageRequest(content="review fixture", model_key=runtime.config.key)
    prepared = frozen_input(session, payload, semantic)
    identities = {"turn_id": "original-turn", "trace_id": "original-trace"}
    if changed == "session":
        session.id = "different-session"
    elif changed in {"turn", "trace"}:
        identities[changed + "_id"] = "different-identity"
    elif changed == "content":
        payload.content = "different input"
    elif changed == "unfrozen":
        prepared = prepared.model_copy(update={"invocation_parameters": None,
            "runtime_parameters": None, "transport_options": None})
    elif changed == "missing_service":
        service.set_semantic_intent_service(None)
    else:
        identities[changed.removeprefix("empty_") + "_id"] = ""
    monkeypatch.setattr(service, "orchestrate", lambda *args, **kwargs: pytest.fail("must validate before projection"))
    with pytest.raises(ValueError):
        await service.orchestrate_prepared(session, payload, prepared, **identities)
    assert runtime.requests == []


@pytest.mark.asyncio
async def test_prepared_semantic_failure_cannot_be_projected_as_baseline(monkeypatch):
    runtime = Runtime()
    semantic = SemanticIntentService(runtime)
    service = InputOrchestratorService(ModeRegistry(), semantic)
    session = _session()
    payload = SendMessageRequest(content="review fixture", model_key=runtime.config.key)
    prepared = frozen_input(session, payload, semantic)

    async def fail(*args, **kwargs):
        raise ConnectionError("original settlement unavailable")

    monkeypatch.setattr(semantic, "enrich_prepared", fail)
    monkeypatch.setattr(service, "orchestrate", lambda *args, **kwargs: pytest.fail("failure must not enter projection"))
    with pytest.raises(ConnectionError, match="settlement unavailable"):
        await service.orchestrate_prepared(session, payload, prepared,
            turn_id="original-turn", trace_id="original-trace")
    assert runtime.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_application_projection_consumes_original_settlement_after_service_restart(harness, monkeypatch, transport):
    with _provider(transport, first_reply=semantic_reply(transport)) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            semantic = SemanticIntentService(h.models, deterministic_confidence_threshold=1.1)
            service = InputOrchestratorService(ModeRegistry(), semantic)
            session = await h.store.get_session(h.session_id)
            payload = SendMessageRequest(content="Inspect this interface", model_key=h.request.model_key)
            saved = []

            async def freeze(prepared):
                saved.append(PreparedSemanticInput.model_validate_json(prepared.model_dump_json()))
                return saved[-1]

            with framework_owner_scope(h.owner.runtime_owner):
                original = await service.orchestrate_async(session, payload, turn_id=h.binding.turn_id,
                    trace_id="original-trace", record_semantic_input=freeze)
                before = await h.calls.snapshot(h.binding)
                replacement = InputOrchestratorService(ModeRegistry(), SemanticIntentService(h.models, enabled=False))

                async def no_send(*args, **kwargs):
                    pytest.fail("completed application projection must not send or reorchestrate")

                monkeypatch.setattr(replacement, "orchestrate_async", no_send)
                monkeypatch.setattr(h.models, "invoke", no_send)
                for _ in range(2):
                    restored = await replacement.orchestrate_prepared(session, payload, saved[0],
                        turn_id=h.binding.turn_id, trace_id="original-trace", completed_call=before.calls[0])
                    assert restored == original
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before
            assert json.loads(saved[0].request.messages[0]["content"])["user_input"] == payload.content

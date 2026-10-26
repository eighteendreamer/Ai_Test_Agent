from __future__ import annotations

import json

import pytest

from src.application.intent.semantic_intent_service import SemanticIntentService
from src.schemas.intent import IntentDecision
from src.schemas.model_config import ModelInvocationResult
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import selected, run, stage


def wire_semantic(h, url, transport="openai_chat_completions"):
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from tests.test_runtime_binding_persistence import make_command

    config = _config(transport, url)

    class Registry:
        def get_runtime_config(self, key):
            assert key == config.key
            return config

    models = ModelRuntimeService(Registry(), h.settings)
    models.set_framework_model_call_policy(FrameworkModelCallPolicy(calls=h.calls, model_config_resolver=models.get_model_config))
    h.semantic = SemanticIntentService(models, deterministic_confidence_threshold=1.1)
    h.sessions._input_orchestrator_service = InputOrchestratorService(ModeRegistry(), h.semantic)
    h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
    h.command = make_command(h, h.binding)
    h.payload = h.payload.model_copy(update={"model_key": config.key})
    h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
    return models


class Runtime:
    def __init__(self):
        self.config = _config("openai_chat_completions", "http://127.0.0.1:1/v1")
        self.runtime = {"transport_options": {"timeout_seconds": 25.0, "max_retries": 0}, "sdk": "fixture"}
        self.requests = []

    def get_model_config(self, key):
        assert key == self.config.key
        return self.config

    def get_invocation_runtime_parameters(self, key, *, transport_options=None):
        return self.runtime

    def governed_transport_options(self):
        from src.schemas.model_config import ModelTransportOptions

        return ModelTransportOptions(timeout_seconds=25, max_retries=0)

    async def invoke(self, key, request, **kwargs):
        self.requests.append((key, request, kwargs))
        return ModelInvocationResult(text=json.dumps({"candidate_mode_key": "code_review", "confidence": 0.99}))


@pytest.mark.asyncio
async def test_saved_semantic_input_is_consumed_without_rebuilding_prompt_or_selection(monkeypatch):
    runtime = Runtime()
    service = SemanticIntentService(runtime)
    baseline = IntentDecision(evidence=["original baseline"])
    prepared = service.prepare(message="review fixture", baseline=baseline, model_key=runtime.config.key,
        trace_context={"session_id": "session", "turn_id": "turn", "logical_call_id": "semantic-position"},
        freeze_runtime=True)
    saved = type(prepared).model_validate_json(prepared.model_dump_json())
    baseline.evidence.append("mutated caller")
    monkeypatch.setattr(service, "_system_prompt", lambda: pytest.fail("must consume the saved prompt"))
    monkeypatch.setattr(service, "_resolve_model_key", lambda key: pytest.fail("must consume the saved model"))
    result = await service.enrich_prepared(saved)
    assert result.candidate_mode_key == "code_review" and "mutated caller" not in result.evidence
    _, request, options = runtime.requests[0]
    assert request == saved.request and request.tools == []
    assert options["expected_parameters"] == saved.invocation_parameters
    assert options["expected_runtime_parameters"] == saved.runtime_parameters
    assert options["transport_options"].max_retries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["model", "runtime"])
async def test_changed_frozen_semantic_configuration_is_not_hidden_as_baseline_fallback(changed):
    runtime = Runtime()
    service = SemanticIntentService(runtime)
    prepared = service.prepare(message="review fixture", baseline=IntentDecision(), model_key=runtime.config.key,
        freeze_runtime=True)
    if changed == "model":
        runtime.config = runtime.config.model_copy(update={"temperature": 0.8})
    else:
        runtime.runtime = {"sdk": "changed"}
    with pytest.raises(ValueError, match="changed"):
        await service.enrich_prepared(prepared)
    assert runtime.requests == []


@pytest.mark.asyncio
async def test_saved_skip_cannot_turn_into_a_model_call_when_policy_changes():
    runtime = Runtime()
    service = SemanticIntentService(runtime, enabled=False)
    prepared = service.prepare(message="review fixture", baseline=IntentDecision(), model_key=runtime.config.key,
        freeze_runtime=True)
    service._enabled = True
    assert prepared.skip_reason == "deterministic" and prepared.request is None
    assert await service.enrich_prepared(prepared) == prepared.baseline
    assert runtime.requests == []


@pytest.mark.parametrize("changed", ["baseline", "model", "tools", "missing_settings", "skip", "schema"])
def test_semantic_input_contract_rejects_contradictory_saved_facts(changed):
    runtime = Runtime()
    prepared = SemanticIntentService(runtime).prepare(message="review fixture", baseline=IntentDecision(),
        model_key=runtime.config.key, freeze_runtime=True)
    record = prepared.model_dump(mode="json")
    if changed == "baseline":
        record["baseline"]["confidence"] = 0.7
    elif changed == "model":
        record["model_key"] = "another-model"
    elif changed == "tools":
        record["request"]["tools"] = [{"name": "should-not-execute"}]
    elif changed == "missing_settings":
        record["runtime_parameters"] = None
    elif changed == "skip":
        record["skip_reason"] = "deterministic"
    else:
        record["schema_version"] = 2
    with pytest.raises(ValueError):
        type(prepared).model_validate(record)


@pytest.mark.asyncio
async def test_frozen_invocation_failure_stays_visible():
    runtime = Runtime()
    service = SemanticIntentService(runtime)
    prepared = service.prepare(message="review fixture", baseline=IntentDecision(), model_key=runtime.config.key,
        freeze_runtime=True)

    async def fail(*args, **kwargs):
        raise ConnectionError("injected evidence write failure")

    runtime.invoke = fail
    with pytest.raises(ConnectionError, match="evidence write"):
        await service.enrich_prepared(prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_actual_semantic_request_is_persisted_before_any_model_reservation(admission, monkeypatch, transport):
    from src.runtime.framework_message_stages import FrameworkMessageStages

    h = admission
    with _provider(transport) as (url, requests):
        wire_semantic(h, url, transport)
        task = await selected(h)
        reserve = h.calls.prepare_initial
        captured = []
        freeze = FrameworkMessageStages.freeze_semantic_input

        async def change_service_after_freeze(self, owner, prepared):
            saved = await freeze(self, owner, prepared)
            monkeypatch.setattr(h.semantic, "_system_prompt", lambda: pytest.fail("saved prompt must be used"))
            monkeypatch.setattr(h.semantic, "_should_invoke", lambda *args: pytest.fail("saved selection must be used"))
            return saved

        async def require_saved(owner, intent):
            saved = await stage(h, task)
            prepared = saved.source_manifest["semantic_input"]
            assert saved.phase == "orchestrating" and requests == []
            assert prepared["request"]["trace_context"]["turn_id"] == task.turn_id
            assert prepared["invocation_parameters"]["key"] == intent.model_key
            assert prepared["transport_options"]["max_retries"] == 0
            captured.append(prepared)
            return await reserve(owner, intent)

        monkeypatch.setattr(h.calls, "prepare_initial", require_saved)
        monkeypatch.setattr(FrameworkMessageStages, "freeze_semantic_input", change_service_after_freeze)
        await run(h, task)
        assert len(requests) == 1 and len(captured) == 1
        prepared = captured[0]
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)

        wire_texts = list(strings(requests[0]["body"]))
        assert prepared["request"]["system_prompt"] in wire_texts
        assert prepared["request"]["messages"][0]["content"] in wire_texts
        assert (await stage(h, task)).source_manifest["semantic_input"] == prepared


@pytest.mark.asyncio
async def test_semantic_input_database_failure_does_not_reserve_or_send(admission):
    from psycopg.errors import CheckViolation
    from sqlalchemy.exc import IntegrityError
    from src.infrastructure.postgres_runtime import postgres_connect

    h = admission
    with _provider("openai_chat_completions") as (url, requests):
        wire_semantic(h, url)
        task = await selected(h)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {h.tasks._tasks}_message_stage ADD CONSTRAINT reject_semantic "
                "CHECK (NOT ((record->'source_manifest') ? 'semantic_input'))")
        with pytest.raises(IntegrityError) as failed:
            await run(h, task)
        assert isinstance(failed.value.orig, CheckViolation)
        saved = await stage(h, task)
        assert saved.phase == "orchestrating" and "semantic_input" not in saved.source_manifest
        assert (await h.calls.snapshot(h.binding)).reserved_calls == 0
        assert requests == [] and h.runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [False, True])
async def test_semantic_skip_is_saved_as_an_explicit_decision(admission, disabled):
    h = admission
    with _provider("openai_chat_completions") as (url, requests):
        if disabled:
            wire_semantic(h, url)
            h.semantic._enabled = False
        task = await selected(h)
        await run(h, task)
        saved = (await stage(h, task)).source_manifest["semantic_input"]
        assert saved["skip_reason"] == ("deterministic" if disabled else "service_unavailable")
        assert saved["request"] is None and saved["baseline"]
        assert (await h.calls.snapshot(h.binding)).reserved_calls == 0 and requests == []


@pytest.mark.asyncio
async def test_prepared_semantic_input_is_immutable_owner_fenced_and_identity_bound(admission):
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
    from src.runtime.store import ContinuationLeaseLostError

    h = admission
    with _provider("openai_chat_completions") as (url, requests):
        wire_semantic(h, url)
        task = await selected(h)
        stages = FrameworkMessageStages(h.tasks)
        session = await h.store.get_session(h.session_id)
        await stages.begin(task.runtime_owner, message_source_manifest(session, h.binding, h.payload))
        prepared = h.semantic.prepare(message=h.payload.content, baseline=IntentDecision(), model_key=h.payload.model_key,
            trace_context={"session_id": h.session_id, "turn_id": task.turn_id, "trace_id": task.trace_id,
                "logical_call_id": "input_orchestration:semantic_intent:v1"}, freeze_runtime=True)
        wrong = prepared.model_copy(deep=True)
        wrong.request.trace_context["turn_id"] = "another-turn"
        with pytest.raises(RuntimeError, match="identity"):
            await stages.freeze_semantic_input(task.runtime_owner, wrong)
        assert "semantic_input" not in (await stage(h, task)).source_manifest
        assert await stages.freeze_semantic_input(task.runtime_owner, prepared) == prepared
        frozen = await stage(h, task)
        assert await stages.freeze_semantic_input(task.runtime_owner, prepared) == prepared
        assert await stage(h, task) == frozen
        changed = prepared.model_copy(deep=True)
        changed.request.system_prompt = "changed after freezing"
        with pytest.raises(RuntimeError, match="immutable"):
            await stages.freeze_semantic_input(task.runtime_owner, changed)
        assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
        with pytest.raises(ContinuationLeaseLostError):
            await stages.freeze_semantic_input(task.runtime_owner, prepared)
        assert await stage(h, task) == frozen and requests == []


@pytest.mark.asyncio
async def test_semantic_input_cannot_be_reconstructed_after_model_reservation(admission):
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
    from tests.test_framework_model_calls import intent

    h = admission
    task = await selected(h)
    stages = FrameworkMessageStages(h.tasks)
    await stages.begin(task.runtime_owner, message_source_manifest(await h.store.get_session(h.session_id), h.binding, h.payload))
    await h.calls.prepare_initial(task.runtime_owner, intent(position_version=1))
    from src.schemas.semantic_input import PreparedSemanticInput

    with pytest.raises(RuntimeError, match="before model reservation"):
        await stages.freeze_semantic_input(task.runtime_owner,
            PreparedSemanticInput(baseline=IntentDecision(), skip_reason="service_unavailable"))
    assert "semantic_input" not in (await stage(h, task)).source_manifest


@pytest.mark.asyncio
async def test_runtime_submission_cannot_bypass_required_semantic_preparation(admission, monkeypatch):
    h = admission
    task = await selected(h)

    async def bypass(session, payload, **kwargs):
        # A callback integration regression must not silently reach Runtime.
        return h.sessions._input_orchestrator_service.orchestrate(session, payload,
            turn_id=kwargs["turn_id"], trace_id=kwargs["trace_id"])

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", bypass)
    with pytest.raises(RuntimeError, match="prepared semantic input"):
        await run(h, task)
    assert (await stage(h, task)).phase == "orchestrating"
    assert h.runtime.calls == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0

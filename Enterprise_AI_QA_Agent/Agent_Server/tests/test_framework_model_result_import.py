from __future__ import annotations

import asyncio

import pytest

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.models.framework_model_evidence import FrameworkModelEvidence
from src.runtime.execution_context import framework_owner_scope
from tests.test_agent_task_dispatch import harness
from tests.test_artifact_storage_service import _RustFSClient
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider
from tests.test_framework_model_dto import PATHS, dto_request
from tests.test_framework_model_policy import governed
from tests.test_framework_model_calls import model_calls_harness, begin, intent, policy, usage
from tests.test_framework_model_evidence import reference
from tests.test_framework_approval_continuation import continuation_harness, claim_decision
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness


@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_fact_digest_does_not_gain_a_new_null_settlement_field(schema_version):
    from datetime import datetime, timezone
    from src.schemas.framework_model_call import FrameworkModelCallRecord
    from tests.test_framework_model_calls import intent

    record = FrameworkModelCallRecord(schema_version=schema_version, binding_id="fixture-binding", intent=intent(),
        owner_epoch=1, owner_task_id="task", owner_attempt_id="attempt", phase="ready", created_at=datetime.now(timezone.utc))
    assert "settled_response_id" not in record.model_dump(mode="json")


def wire_evidence(h, monkeypatch):
    objects = ArtifactStorageService(h.settings)
    client = _RustFSClient()
    monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
    h.evidence = FrameworkModelEvidence(objects=objects)
    h.policy._evidence = h.evidence
    h.models.set_framework_model_call_policy(h.policy)
    return client


def invocation_options(h):
    transport = h.models.governed_transport_options()
    return {"transport_options": transport.model_dump(mode="json"), "streaming": False,
        "langchain_adapter": h.models.get_invocation_runtime_parameters(h.request.model_key)["langchain_model_adapter_enabled"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_summary_dto_restoration_keeps_original_purpose_without_another_send(harness, monkeypatch, path):
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.schemas.model_config import model_invocation_parameters

    transport = "openai_chat_completions" if path == "langchain_chat" else path
    with _provider(transport) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            h.settings.model.langchain_model_adapter_enabled = path == "langchain_chat"
            wire_evidence(h, monkeypatch)
            request = dto_request(h, logical="summary:fixture:v1")
            options = h.models.governed_transport_options()
            contract = {"expected_parameters": model_invocation_parameters(h.registry.current),
                "expected_runtime_parameters": h.models.get_invocation_runtime_parameters(
                    h.request.model_key, transport_options=options), "transport_options": options}
            with framework_owner_scope(h.owner.runtime_owner):
                original = await h.models.invoke(h.request.model_key, request, purpose="summary",
                    **contract)
            before = await h.calls.snapshot(h.binding)
            call, = before.calls
            assert call.phase == "completed" and call.intent.purpose == "summary"
            assert before.reserved_calls == 1 and before.actual_tokens == 10

            # A new runtime cannot depend on the original in-memory response.
            models = ModelRuntimeService(h.registry, h.settings)
            models.set_framework_model_call_policy(h.policy)

            async def no_send(*args, **kwargs):
                pytest.fail("Completed summary import must not authenticate or send again")

            monkeypatch.setattr(models, "_resolve_auth_token", no_send)
            monkeypatch.setattr(models, "_invoke_with_client", no_send)
            monkeypatch.setattr(models, "_invoke_with_langchain_adapter", no_send)
            # Trace/display metadata cannot silently select a different purpose.
            request.trace_context["purpose"] = "summary"
            with framework_owner_scope(h.owner.runtime_owner):
                with pytest.raises(ValueError, match="frozen request"):
                    await models.restore_completed(h.request.model_key, request, expected=call, **contract)
                with pytest.raises(ValueError, match="requires a purpose"):
                    await models.restore_completed(h.request.model_key, request, expected=call,
                        purpose=" ", **contract)
                for _ in range(2):
                    restored = await models.restore_completed(h.request.model_key, request,
                        expected=call, purpose=" summary ", **contract)
            direct = await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=call,
                config=h.registry.current, request=request, options=invocation_options(h), purpose="summary")
            assert restored.text == original.text and restored.raw_response == original.raw_response
            assert direct == restored
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_official_dto_completion_binds_the_response_and_import_does_not_call_provider(harness, monkeypatch, transport):
    with _provider(transport) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            wire_evidence(h, monkeypatch)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                original = await h.models.invoke(h.request.model_key, request)
            before = await h.calls.snapshot(h.binding)
            call, = before.calls
            observation, = call.response_observations
            assert call.settled_response_id == observation.observation_id
            assert call.schema_version == 3 and not observation.authoritative
            restored = await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=call,
                config=h.registry.current, request=request, options=invocation_options(h))
            assert restored.text == original.text and restored.raw_response == original.raw_response
            assert restored.request_payload == {}  # Display/request summaries do not reconstruct provider input.
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_competing_settlements_cannot_change_the_selected_response(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1)
    await h.calls.prepare_initial(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    observations = [await h.calls.append_response_observation(h.owner, selected.attempt_id,
        response=reference(content), response_kind="model_invocation_result") for content in (b'"first"', b'"second"')]
    results = await asyncio.gather(*(h.calls.complete(h.owner, selected.attempt_id, usage=usage(),
        response_id=item.observation_id) for item in observations), return_exceptions=True)
    assert sum(isinstance(result, ValueError) for result in results) == 1
    call = (await h.calls.snapshot(h.binding)).calls[0]
    assert call.settled_response_id in {item.observation_id for item in observations}
    from tests.test_framework_message_diagnostics import isolate_absent_stores

    isolate_absent_stores(h)
    diagnostic = await h.tasks.diagnose_framework_message(h.task.id, session_id=h.session_id)
    assert diagnostic.model_calls[0].settled_response_id == call.settled_response_id
    assert "model_settled_response_missing" not in diagnostic.issues
    assert not diagnostic.automatic_replay_allowed and not diagnostic.evidence_objects_verified
    assert await h.calls.complete(h.owner, selected.attempt_id, usage=usage(), response_id=call.settled_response_id) == call
    assert await h.calls.verify_completed(h.owner, expected=call) == call


@pytest.mark.asyncio
async def test_missing_response_binding_rolls_back_the_entire_settlement(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1)
    await h.calls.prepare_initial(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ValueError, match="observation"):
        await h.calls.complete(h.owner, selected.attempt_id, usage=usage(), response_id="a" * 64)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_later_candidate_never_replaces_the_settled_response(harness, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            wire_evidence(h, monkeypatch)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                result = await h.models.invoke(h.request.model_key, request)
            call = (await h.calls.snapshot(h.binding)).calls[0]
            candidate = await h.evidence.store_response(h.owner.runtime_owner, call.intent,
                result.model_copy(update={"text": "unselected later response"}), response_kind="model_invocation_result")
            await h.calls.append_response_observation(h.owner.runtime_owner, call.intent.attempt_id,
                response=candidate, response_kind="model_invocation_result")
            before = await h.calls.snapshot(h.binding)
            assert len(before.calls[0].response_observations) == 2
            restored = await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=before.calls[0],
                config=h.registry.current, request=request, options=invocation_options(h))
            assert restored.text == "fixture answer" and len(requests) == 1
            assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["input", "options", "model", "position"])
async def test_import_requires_the_original_frozen_input(harness, monkeypatch, changed):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            wire_evidence(h, monkeypatch)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await h.models.invoke(h.request.model_key, request)
            before = await h.calls.snapshot(h.binding)
            options = invocation_options(h)
            if changed == "input":
                request = request.model_copy(deep=True)
                request.system_prompt = "changed after completion"
            elif changed == "options":
                options["transport_options"]["timeout_seconds"] += 1
            elif changed == "model":
                h.registry.current = h.registry.current.model_copy(update={"temperature": 0.9})
            else:
                request = request.model_copy(deep=True)
                request.trace_context["logical_call_id"] = "another-position"
            with pytest.raises(ValueError, match="frozen request"):
                await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=before.calls[0],
                    config=h.registry.current, request=request, options=options)
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["owner", "proof"])
async def test_import_rechecks_owner_and_proof_after_async_object_reads(harness, monkeypatch, changed):
    from src.runtime.store import ContinuationLeaseLostError
    from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            wire_evidence(h, monkeypatch)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                result = await h.models.invoke(h.request.model_key, request)
            before = await h.calls.snapshot(h.binding)
            call = before.calls[0]
            read = h.evidence.read_response

            async def change_after_read(*args):
                payload = await read(*args)
                if changed == "owner":
                    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
                else:
                    other = await h.evidence.store_response(h.owner.runtime_owner, call.intent,
                        result.model_copy(update={"text": "late evidence"}), response_kind="model_invocation_result")
                    await h.calls.append_response_observation(h.owner.runtime_owner, call.intent.attempt_id,
                        response=other, response_kind="model_invocation_result")
                return payload

            monkeypatch.setattr(h.evidence, "read_response", change_after_read)
            with pytest.raises(ContinuationLeaseLostError if changed == "owner" else FrameworkModelRecoveryRequired):
                await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=call,
                    config=h.registry.current, request=request, options=invocation_options(h))
            assert len(requests) == 1
            after = await h.calls.snapshot(h.binding)
            assert after.charged_tokens == before.charged_tokens and after.reserved_calls == before.reserved_calls
            assert after.calls[0].settled_response_id == call.settled_response_id


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["serializer", "kind", "identity", "usage", "data"])
async def test_checksum_valid_response_still_requires_semantic_and_codec_validation(harness, monkeypatch, changed):
    from src.application.models.framework_model_call_policy import canonical_model_input

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            wire_evidence(h, monkeypatch)
            store = h.evidence.store_response

            async def store_other(owner, intent, result, *, response_kind):
                original = await store(owner, intent, result, response_kind=response_kind)
                payload = await h.evidence.read(original)
                if changed == "serializer":
                    payload["serializer"]["version"] = "2"
                elif changed == "kind":
                    payload["kind"] = "unknown_result"
                elif changed == "identity":
                    payload["logical_call_id"] = "other-position"
                elif changed == "usage":
                    payload["data"]["response_summary"]["usage"]["total_tokens"] += 1
                else:
                    payload["data"]["unknown_future_field"] = "must not be silently dropped"
                return await h.evidence._store(owner, intent, canonical_model_input(payload), "fixture-response")

            monkeypatch.setattr(h.evidence, "store_response", store_other)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await h.models.invoke(h.request.model_key, request)
            before = await h.calls.snapshot(h.binding)
            with pytest.raises(ValueError):
                await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=before.calls[0],
                    config=h.registry.current, request=request, options=invocation_options(h))
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_import_validates_actual_object_bytes(harness, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            client = wire_evidence(h, monkeypatch)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await h.models.invoke(h.request.model_key, request)
            before = await h.calls.snapshot(h.binding)
            key = next(key for key, (content, _) in client.objects.items() if b'"kind":"model_invocation_result"' in content)
            content, content_type = client.objects[key]
            client.objects[key] = (content.replace(b"fixture answer", b"tampered reply"), content_type)
            with pytest.raises(ValueError, match="digest"):
                await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=before.calls[0],
                    config=h.registry.current, request=request, options=invocation_options(h))
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["invoking", "unknown", "completed"])
async def test_lost_settlement_ack_is_read_from_recreated_stores_without_resending(harness, monkeypatch, phase):
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls, FrameworkModelRecoveryRequired

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            client = wire_evidence(h, monkeypatch)
            complete = h.calls.complete
            request = dto_request(h)

            async def lose_ack(*args, **kwargs):
                if phase == "completed":
                    await complete(*args, **kwargs)
                raise ConnectionError("injected settlement acknowledgement loss")

            with monkeypatch.context() as patch:
                patch.setattr(h.calls, "complete", lose_ack)
                with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ConnectionError, match="acknowledgement"):
                    await h.models.invoke(h.request.model_key, request)
            calls = PostgresFrameworkModelCalls(h.settings)
            objects = ArtifactStorageService(h.settings)
            monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
            restored_policy = FrameworkModelCallPolicy(calls=calls, model_config_resolver=h.models.get_model_config,
                evidence=FrameworkModelEvidence(objects=objects))
            if phase == "unknown":
                current = (await calls.snapshot(h.binding)).calls[0]
                await calls.mark_unknown(h.owner.runtime_owner, current.intent.attempt_id, code="response_unconfirmed")
            before = await calls.snapshot(h.binding)
            call, = before.calls
            assert call.phase == phase
            if phase == "completed":
                restored = await restored_policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=call,
                    config=h.registry.current, request=request, options=invocation_options(h))
                assert restored.text == "fixture answer" and call.settled_response_id
            else:
                assert call.response_observations and call.settled_response_id is None
                with pytest.raises(FrameworkModelRecoveryRequired, match="unavailable"):
                    await restored_policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=call,
                        config=h.registry.current, request=request, options=invocation_options(h))
            assert len(requests) == 1 and await calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_reading_a_completed_response_never_invents_missing_usage(harness, monkeypatch):
    from tests.test_framework_chat_models import _reply

    reply = _reply("openai_chat_completions")
    reply.pop("usage")
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            wire_evidence(h, monkeypatch)
            request = dto_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                await h.models.invoke(h.request.model_key, request)
            before = await h.calls.snapshot(h.binding)
            assert before.calls[0].usage is None and before.charged_tokens == before.calls[0].intent.reserved_tokens
            restored = await h.policy.restore_completed_dto(owner=h.owner.runtime_owner, expected=before.calls[0],
                config=h.registry.current, request=request, options=invocation_options(h))
            assert restored.text == "fixture answer"
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_historical_completed_observation_is_not_implicitly_promoted(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired

    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1)
    await h.calls.prepare_initial(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    await h.calls.append_response_observation(h.owner, selected.attempt_id,
        response=reference(), response_kind="model_invocation_result")
    await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
    before = await h.calls.snapshot(h.binding)
    assert before.calls[0].schema_version == 1 and before.calls[0].settled_response_id is None
    with pytest.raises(FrameworkModelRecoveryRequired, match="unavailable"):
        await h.calls.verify_completed(h.owner, expected=before.calls[0])
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_replacement_owner_reads_original_settlement_without_claiming_its_observer(continuation_harness):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.runtime.store import ContinuationLeaseLostError

    h = continuation_harness
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    try:
        original = await claim_decision(h)
        await calls.freeze(original.runtime_owner, policy(h))
        selected = intent(position_version=1)
        await calls.prepare_initial(original.runtime_owner, selected)
        await calls.start(original.runtime_owner, selected.attempt_id)
        observation = await calls.append_response_observation(original.runtime_owner, selected.attempt_id,
            response=reference(), response_kind="model_invocation_result")
        call = await calls.complete(original.runtime_owner, selected.attempt_id, usage=usage(),
            response_id=observation.observation_id)
        before = await calls.snapshot(h.binding)
        assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
        await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
        terminal = await h.tasks.get(original.id)
        reconcile = await h.tasks.ensure_framework_reconciliation(original.id)
        current = await h.tasks.claim(reconcile.id, worker_id="result-reader", expected_binding=h.binding)
        await h.tasks.begin(current, resource_id="result-reader", fencing_token=3)
        assert current.runtime_owner.owner_epoch > original.runtime_owner.owner_epoch
        assert await calls.verify_completed(current.runtime_owner, expected=call) == call
        with pytest.raises(ContinuationLeaseLostError):
            await calls.verify_completed(original.runtime_owner, expected=call)
        assert await calls.snapshot(h.binding) == before and await h.tasks.get(original.id) == terminal
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {calls.calls_table}")
            cur.execute(f"DROP TABLE {calls.budgets_table}")

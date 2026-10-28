from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_calls import model_calls_harness, begin, intent, policy, usage


def reference(content=b'{"fixture":"response"}', *, uri=None):
    from src.schemas.framework_model_call import FrameworkModelObject

    return FrameworkModelObject(storage_uri=uri or f"rustfs://fixture/{uuid4().hex}.json",
        sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))


async def sent(h):
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    request = reference(b'{"fixture":"request"}')
    selected = intent(request_sha256=request.sha256)
    prepared = await h.calls.prepare(h.owner, selected, request=request)
    assert prepared.request == request
    await h.calls.start(h.owner, selected.attempt_id)
    return selected


@pytest.mark.asyncio
async def test_request_asset_is_bound_to_intent_and_survives_store_restart(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = model_calls_harness
    await sent(h)
    saved, = (await PostgresFrameworkModelCalls(h.settings).snapshot(h.binding)).calls
    assert saved.request.sha256 == saved.intent.request_sha256
    assert "owner_token" not in saved.model_dump_json()
    assert h.owner.owner_token not in saved.model_dump_json()


@pytest.mark.asyncio
async def test_request_hash_and_existing_asset_cannot_change(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    with pytest.raises(ValueError, match="request"):
        await h.calls.prepare(h.owner, intent(), request=reference())
    assert (await h.calls.snapshot(h.binding)).reserved_calls == 0
    request = reference()
    selected = intent(request_sha256=request.sha256)
    saved = await h.calls.prepare(h.owner, selected, request=request)
    assert await h.calls.prepare(h.owner, selected, request=request) == saved
    for changed in (None, request.model_copy(update={"storage_uri": "rustfs://fixture/changed.json"})):
        with pytest.raises(ValueError, match="request"):
            await h.calls.prepare(h.owner, selected, request=changed)


@pytest.mark.asyncio
async def test_late_response_is_append_only_deduplicated_and_does_not_settle_usage(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = model_calls_harness
    selected = await sent(h)
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    before = await h.calls.snapshot(h.binding)
    response = reference()
    observations = await asyncio.gather(*(h.calls.append_response_observation(h.owner,
        selected.attempt_id, response=response, response_kind="langchain_messages") for _ in range(8)))
    assert all(item == observations[0] for item in observations)
    assert observations[0].authoritative is False
    restarted = PostgresFrameworkModelCalls(h.settings)
    after = await restarted.snapshot(h.binding)
    call, = after.calls
    assert call.phase == "invoking" and call.usage is None
    assert after.charged_tokens == before.charged_tokens and after.reserved_calls == before.reserved_calls
    assert call.response_observations == [observations[0]]
    with pytest.raises(ContinuationLeaseLostError):
        await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
    # A duplicate object upload can have a different URI but identical bytes.
    assert await restarted.append_response_observation(h.owner, selected.attempt_id,
        response=response.model_copy(update={"storage_uri": "rustfs://fixture/duplicate.json"}),
        response_kind="langchain_messages") == observations[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["owner_epoch", "task_id", "attempt_id", "owner_token", "binding"])
async def test_observation_requires_original_execution_identity(model_calls_harness, changed):
    h = model_calls_harness
    selected = await sent(h)
    value = h.owner.owner_epoch + 1 if changed == "owner_epoch" else "different-observer"
    if changed == "binding":
        value = h.binding.model_copy(update={"prompt_version": "different-prompt"})
    forged = h.owner.model_copy(update={changed: value})
    with pytest.raises((ValueError, ContinuationLeaseLostError)):
        await h.calls.append_response_observation(forged, selected.attempt_id,
            response=reference(), response_kind="langchain_messages")
    assert (await h.calls.snapshot(h.binding)).calls[0].response_observations == []


@pytest.mark.asyncio
async def test_unsent_attempt_cannot_receive_response_evidence(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    request = reference()
    selected = intent(request_sha256=request.sha256)
    await h.calls.prepare(h.owner, selected)
    with pytest.raises(ValueError, match="sent"):
        await h.calls.append_response_observation(h.owner, selected.attempt_id,
            response=reference(), response_kind="langchain_messages")


@pytest.mark.asyncio
async def test_observation_and_authoritative_settlement_cannot_overwrite_each_other(model_calls_harness):
    h = model_calls_harness
    selected = await sent(h)
    responses = [reference(json.dumps({"fixture": index}).encode()) for index in range(6)]
    await asyncio.gather(h.calls.complete(h.owner, selected.attempt_id, usage=usage()),
        *(h.calls.append_response_observation(h.owner, selected.attempt_id, response=response,
            response_kind="model_invocation_result") for response in responses))
    after = await h.calls.snapshot(h.binding)
    call, = after.calls
    assert call.phase == "completed" and after.actual_tokens == 70
    assert {item.response.sha256 for item in call.response_observations} == {item.sha256 for item in responses}
    assert all(not item.authoritative for item in call.response_observations)


@pytest.mark.asyncio
async def test_historical_record_without_observer_hash_cannot_accept_new_observations(model_calls_harness):
    h = model_calls_harness
    selected = await sent(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.calls_table} SET record=record-'observer_token_sha256' "
            "WHERE binding_id=%s AND attempt_id=%s", (h.binding.binding_id, selected.attempt_id))
    with pytest.raises(ValueError, match="observer"):
        await h.calls.append_response_observation(h.owner, selected.attempt_id,
            response=reference(), response_kind="langchain_messages")


@pytest.mark.asyncio
async def test_legacy_attempt_without_request_asset_is_not_rewritten_on_recovery(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    request = reference()
    selected = intent(request_sha256=request.sha256)
    await h.calls.prepare(h.owner, selected)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.calls_table} SET record=record-'request' "
            "WHERE binding_id=%s AND attempt_id=%s", (h.binding.binding_id, selected.attempt_id))
    recovered = await h.calls.prepare(h.owner, selected, request=request)
    assert recovered.request is None


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [
    "openai_chat_completions", "openai_responses", "google_gemini_generate_content",
])
async def test_official_response_evidence_survives_expired_owner_and_completion_ack_loss(
    harness, monkeypatch, transport
):
    from src.application.models.framework_model_evidence import FrameworkModelEvidence
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from tests.test_artifact_storage_service import _RustFSClient
    from tests.test_framework_model_policy import governed, execute
    from tests.test_framework_chat_models import _config, _provider

    with _provider(transport) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            client = _RustFSClient()
            objects = ArtifactStorageService(h.settings)
            monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
            h.policy._evidence = FrameworkModelEvidence(objects=objects)
            complete = h.calls.complete

            async def expire_before_complete(*args, **kwargs):
                assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
                return await complete(*args, **kwargs)

            monkeypatch.setattr(h.calls, "complete", expire_before_complete)
            with pytest.raises(ContinuationLeaseLostError, match="owner is no longer active"):
                await execute(h)
            call, = (await h.calls.snapshot(h.binding)).calls
            assert len(requests) == 1 and call.phase == "invoking" and call.usage is None
            assert call.request is not None
            observation, = call.response_observations
            assert not observation.authoritative
            wire, = call.wire_observations
            assert wire.request is not None and wire.response is not None
            assert not wire.request_body_truncated and not wire.response_body_truncated
            assert wire.response_complete is True
            assert b"fixture" in (await objects.read_object_uri(wire.response.storage_uri))["content"]
            request = await h.policy._evidence.read(call.request)
            response = await h.policy._evidence.read_response(h.binding, call.intent, observation.response)
            assert request["messages"][-1]["data"]["content"] == "fixture request"
            content = response["data"]["messages"][0]["data"]["content"]
            if isinstance(content, list):
                assert any(part.get("text") == "fixture answer" for part in content if isinstance(part, dict))
            else:
                assert content == "fixture answer"
            assert response["source"] == "sdk_application"


@pytest.mark.asyncio
async def test_response_codec_preserves_native_blocks_and_rejects_tampered_assets(tmp_path):
    from langchain.agents.middleware.types import ModelResponse
    from langchain_core.messages import AIMessage, messages_from_dict
    from src.application.models.framework_model_evidence import FrameworkModelEvidence
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from tests.test_artifact_storage_service import _RustFSClient, _settings
    from tests.test_runtime_binding_persistence import make_binding
    from src.schemas.runtime_binding import RuntimeExecutionOwner

    client = _RustFSClient()
    objects = ArtifactStorageService(_settings(tmp_path))
    objects._rustfs_client = lambda: client
    evidence = FrameworkModelEvidence(objects=objects)
    binding = make_binding("fixture-session", "fixture-turn")
    owner = RuntimeExecutionOwner(binding=binding, owner_epoch=1, owner_token="fixture-lease",
        task_id="fixture-task", attempt_id="fixture-attempt", worker_id="fixture-worker")
    selected = intent()
    message = AIMessage(id="native-id", content=[{"type": "thinking", "thinking": "fixture reasoning",
        "signature": "fixture-signature"}], additional_kwargs={"native": {"thought_signature": "fixture"}},
        response_metadata={"finish_reason": "tool_calls"},
        usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        tool_calls=[{"id": "native-call", "name": "fixture_tool", "args": {"value": "test"}}])
    asset = await evidence.store_response(owner, selected, ModelResponse(result=[message]),
        response_kind="langchain_messages")
    saved = await evidence.read_response(binding, selected, asset)
    assert messages_from_dict(saved["data"]["messages"]) == [message]
    assert b"fixture-lease" not in (await objects.read_object_uri(asset.storage_uri))["content"]
    with pytest.raises(ValueError, match="identity"):
        await evidence.read_response(binding, selected.model_copy(update={"attempt_id": "other"}), asset)
    key = next(iter(client.objects))
    original, content_type = client.objects[key]
    client.objects[key] = (original.replace(b"native-id", b"tamper-id"), content_type)
    with pytest.raises(ValueError, match="digest"):
        await evidence.read(asset)

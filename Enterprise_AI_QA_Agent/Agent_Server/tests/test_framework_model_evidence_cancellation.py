"""Model evidence uploads and their ledger references share one cancellation tail."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import threading
from types import SimpleNamespace

import pytest

from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.models.framework_model_call_policy import FrameworkExecutionDeadlineExceeded
from src.runtime.execution_context import framework_owner_scope
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import _config, _provider
from tests.test_framework_execution_deadline import set_deadline, wait_for_database_deadline
from tests.test_framework_model_dto import dto_request
from tests.test_framework_model_policy import execute, governed
from tests.test_framework_model_result_import import wire_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["request.json", "response.json", "wire-request.bin", "wire-response.bin"])
@pytest.mark.parametrize("outcome", ["returned", "failed", "ack_lost"])
async def test_policy_unit_drains_evidence_before_propagating_original_cancellation(
    tmp_path, monkeypatch, caplog, stage, outcome,
):
    # This unit uses ledger/send doubles. The real PG/SDK/HTTP matrix below is
    # still required; these assertions do not replace its ownership checks.
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    from langchain_core.messages import AIMessage
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from src.application.model_clients.http_observation import current_provider_call_observation
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.application.models.framework_model_evidence import FrameworkModelEvidence
    from src.schemas.framework_model_call import FrameworkModelCallRecord
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    from tests.test_artifact_storage_service import _RustFSClient, _settings
    from tests.test_framework_model_calls import intent, usage
    from tests.test_runtime_binding_persistence import make_binding

    binding = make_binding("fixture-session", "fixture-turn")
    owner = RuntimeExecutionOwner(binding=binding, owner_epoch=1, owner_token="fixture-lease",
        task_id="fixture-task", attempt_id="fixture-attempt", worker_id="fixture-worker")
    envelope = b'{"fixture":"request"}'
    selected = intent(request_sha256=hashlib.sha256(envelope).hexdigest())
    record = FrameworkModelCallRecord(binding_id=binding.binding_id, intent=selected, owner_epoch=owner.owner_epoch,
        owner_task_id=owner.task_id, owner_attempt_id=owner.attempt_id, phase="ready", created_at=datetime.now(timezone.utc))
    calls = SimpleNamespace(prepare_initial=AsyncMock(return_value=record), attach_request=AsyncMock(),
        start=AsyncMock(return_value=(record, True)), append_response_observation=AsyncMock(),
        append_wire_observation=AsyncMock(), complete=AsyncMock(), mark_unknown=AsyncMock())
    client = _RustFSClient()
    objects = ArtifactStorageService(_settings(tmp_path))
    monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
    policy = FrameworkModelCallPolicy(calls=calls, model_config_resolver=lambda _: None,
        evidence=FrameworkModelEvidence(objects=objects))
    entered, finished, release, selected_uploads = pause_upload(client, monkeypatch, stage=stage, outcome=outcome)
    sends = []

    async def send():
        sends.append(True)
        observation = current_provider_call_observation()
        observation._request_body = b'{"fixture":"wire request"}'
        observation._response_body.extend(b'{"fixture":"wire response"}')
        observation._attempt_sequence = 1
        observation._response_complete = True
        return AIMessage(content="fixture response", usage_metadata={"input_tokens": 3, "output_tokens": 7, "total_tokens": 10})

    task = asyncio.create_task(policy._invoke_intent(owner=owner, intent=selected, send=send, usage_reader=lambda _: usage(10),
        envelope=envelope, response_kind="langchain_messages"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel("first controlled evidence cancellation")
        done, _ = await asyncio.wait({task}, timeout=.1)
        abandoned = task in done
        task.cancel("second controlled evidence cancellation")
        done, _ = await asyncio.wait({task}, timeout=.1)
        abandoned |= task in done
        release.set()
        with pytest.raises(asyncio.CancelledError, match="first controlled evidence cancellation"):
            await task
        await asyncio.wait_for(finished.wait(), 5)
        assert not abandoned, "Cancelled policy abandoned the still-running object upload"
        assert len(sends) == (stage != "request.json")
        assert calls.attach_request.await_count == (stage != "request.json" or outcome == "returned")
        assert calls.append_response_observation.await_count == (stage.startswith("wire-") or stage == "response.json" and outcome == "returned")
        assert calls.append_wire_observation.await_count == (stage.startswith("wire-") and outcome == "returned")
        calls.complete.assert_not_awaited()
        calls.mark_unknown.assert_not_awaited()
        drained, = [record for record in caplog.records if record.getMessage() == "framework_model_evidence_drained_after_cancel"]
        assert drained.cancellation_count == 2 and drained.outcome == ("returned" if outcome == "returned" else "failed")
        assert len(selected_uploads) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if selected_uploads:
            await asyncio.wait_for(finished.wait(), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["request", "response", "wire-request", "wire-response"])
@pytest.mark.parametrize("outcome", ["failed", "ack_lost"])
async def test_unconfirmed_upload_has_identity_for_readback_without_claiming_rollback(
    tmp_path, monkeypatch, caplog, stage, outcome,
):
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from src.application.models.framework_model_evidence import FrameworkModelEvidence
    from tests.test_artifact_storage_service import _RustFSClient, _settings

    client = _RustFSClient()
    objects = ArtifactStorageService(_settings(tmp_path))
    monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
    put, attempted = client.put_object, []

    def uncertain(**kwargs):
        attempted.append(kwargs)
        if outcome == "ack_lost":
            put(**kwargs)
        raise ConnectionError("controlled upload acknowledgement failure")

    monkeypatch.setattr(client, "put_object", uncertain)
    evidence = FrameworkModelEvidence(objects=objects)
    owner = SimpleNamespace(binding=SimpleNamespace(binding_id="fixture-binding"))
    intent = SimpleNamespace(attempt_id="fixture-attempt")
    content = b'{"private_fixture":"not_for_diagnostics"}'
    store = evidence._store_bytes if stage.startswith("wire-") else evidence._store
    with pytest.raises(RuntimeError):
        await store(owner, intent, content, stage)
    unconfirmed, = [record for record in caplog.records
        if record.getMessage() == "framework_model_evidence_storage_unconfirmed"]
    uploaded, = attempted
    assert unconfirmed.binding_id == owner.binding.binding_id and unconfirmed.model_attempt_id == intent.attempt_id
    assert unconfirmed.evidence_stage == stage and unconfirmed.error_type == "RuntimeError"
    assert unconfirmed.object_prefix == uploaded["Key"].rsplit("/", 1)[0]
    assert unconfirmed.content_sha256 == hashlib.sha256(content).hexdigest()
    assert unconfirmed.size_bytes == len(content)
    assert "private_fixture" not in caplog.text and "not_for_diagnostics" not in caplog.text
    assert ((uploaded["Bucket"], uploaded["Key"]) in client.objects) is (outcome == "ack_lost")


def pause_upload(client, monkeypatch, *, stage, outcome):
    """Pause a real ArtifactStorageService worker, not its async return value."""
    loop = asyncio.get_running_loop()
    entered, finished, release = asyncio.Event(), asyncio.Event(), threading.Event()
    put, selected = client.put_object, []

    def upload(**kwargs):
        if kwargs["Key"].rsplit("/", 1)[-1] != stage or selected:
            return put(**kwargs)
        selected.append(kwargs)
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(15), "Test did not release the pending object upload"
            if outcome == "failed":
                raise ConnectionError("controlled object failure before write")
            put(**kwargs)
            if outcome == "ack_lost":
                raise ConnectionError("controlled object acknowledgement loss after write")
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(client, "put_object", upload)
    return entered, finished, release, selected


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["graph", "dto"])
@pytest.mark.parametrize("stage", ["request.json", "response.json", "wire-request.bin", "wire-response.bin"])
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
@pytest.mark.parametrize("outcome", ["returned", "failed", "ack_lost"])
async def test_evidence_cancellation_retains_confirmed_references_without_continuing(
    harness, monkeypatch, caplog, path, stage, stop, outcome,
):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            client = wire_evidence(h, monkeypatch)
            if stop == "deadline":
                set_deadline(h, 3)
            entered, finished, release, selected = pause_upload(client, monkeypatch, stage=stage, outcome=outcome)

            async def invoke():
                with framework_owner_scope(h.owner.runtime_owner):
                    return await (execute(h) if path == "graph" else
                        h.models.invoke(h.request.model_key, dto_request(h)))

            task = asyncio.create_task(invoke())
            try:
                await asyncio.wait_for(entered.wait(), 10)
                before = await h.calls.snapshot(h.binding)
                if stop == "cancel":
                    task.cancel("first controlled evidence cancellation")
                done, _ = await asyncio.wait({task}, timeout=5 if stop == "deadline" else .5)
                abandoned_upload = task in done
                if stop == "cancel":
                    task.cancel("second controlled evidence cancellation")
                    done, _ = await asyncio.wait({task}, timeout=.5)
                    abandoned_upload |= task in done
                assert not finished.is_set()
                release.set()
                error, result = None, None
                try:
                    result = await task
                except (Exception, asyncio.CancelledError) as exc:
                    error = exc
                await asyncio.wait_for(finished.wait(), 5)
                # invoke_dto deliberately disarms its timer once a reliable
                # provider result has arrived (§129). Evidence may then finish
                # after expiry; do not reinterpret this as a cancelled send.
                reliable_dto = path == "dto" and stop == "deadline" and stage != "request.json"
                completed = reliable_dto and outcome == "returned"
                if reliable_dto:
                    if completed:
                        assert error is None and result.text == "fixture answer"
                    else:
                        assert isinstance(error, RuntimeError)
                else:
                    assert isinstance(error, FrameworkExecutionDeadlineExceeded if stop == "deadline" else asyncio.CancelledError)
                if stop == "cancel":
                    assert str(error) == "first controlled evidence cancellation"
                snapshot = await h.calls.snapshot(h.binding)
                call, = snapshot.calls
                assert snapshot.policy == before.policy and snapshot.reserved_calls == 1
                assert snapshot.actual_tokens == (10 if completed else 0) and snapshot.unknown_calls == 0
                assert (call.usage is not None) is completed and (call.settled_response_id is not None) is completed
                assert snapshot.charged_tokens == (10 if completed else before.charged_tokens)
                assert call.phase == ("completed" if completed else "ready" if stage == "request.json" else "invoking")
                assert len(requests) == (0 if stage == "request.json" else 1)
                assert not abandoned_upload, "Control returned while its object upload could still take effect"
                confirmed = outcome == "returned"
                assert (call.request is not None) is (stage != "request.json" or confirmed)
                assert len(call.response_observations) == (stage.startswith("wire-") or stage == "response.json" and confirmed)
                assert len(call.wire_observations) == (confirmed and (stage.startswith("wire-") or reliable_dto))
                if call.request is not None:
                    await h.evidence.read(call.request)
                for item in call.response_observations:
                    assert item.authoritative is False
                    await h.evidence.read_response(h.binding, call.intent, item.response)
                for item in call.wire_observations:
                    assert item.authoritative is False
                    assert item.request is not None and item.response is not None and item.response_complete
                    await h.evidence._objects.read_object_uri(item.request.storage_uri)
                    await h.evidence._objects.read_object_uri(item.response.storage_uri)
                drained = [record for record in caplog.records
                    if record.getMessage() == "framework_model_evidence_drained_after_cancel"]
                if reliable_dto:
                    assert drained == []
                else:
                    item, = drained
                    assert item.binding_id == h.binding.binding_id and item.model_attempt_id == call.intent.attempt_id
                    assert item.evidence_stage == ("wire" if stage.startswith("wire-") else stage.split(".")[0])
                    assert item.cancellation_count == (2 if stop == "cancel" else 1)
                    assert item.outcome == ("returned" if confirmed else "failed")
                if not confirmed:
                    unconfirmed, = [record for record in caplog.records
                        if record.getMessage() == "framework_model_evidence_storage_unconfirmed"]
                    uploaded, = selected
                    assert unconfirmed.evidence_stage == stage.rsplit(".", 1)[0]
                    assert unconfirmed.object_prefix == uploaded["Key"].rsplit("/", 1)[0]
                    assert unconfirmed.content_sha256 == hashlib.sha256(uploaded["Body"]).hexdigest()
                    assert unconfirmed.size_bytes == len(uploaded["Body"])
                    assert ((uploaded["Bucket"], uploaded["Key"]) in client.objects) is (outcome == "ack_lost")
                if stop == "deadline":
                    if not reliable_dto:
                        assert read_model_budget_material(h.binding, error.root_model_budget) == snapshot
                    await wait_for_database_deadline(h)
                    with pytest.raises(FrameworkExecutionDeadlineExceeded):
                        await invoke()
                elif stage != "request.json":
                    with pytest.raises((RuntimeError, ValueError)):
                        await (execute(h, replace(h.request, continue_from_checkpoint=True)) if path == "graph" else invoke())
                assert len(requests) == (0 if stage == "request.json" else 1)
                assert await h.calls.snapshot(h.binding) == snapshot
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if selected:
                    await asyncio.wait_for(finished.wait(), 5)

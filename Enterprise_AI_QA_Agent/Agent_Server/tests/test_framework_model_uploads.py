"""Durable upload identities are forensic facts, never model-send permission."""
from __future__ import annotations

import hashlib
import asyncio
import json
from dataclasses import replace
from pathlib import Path
import multiprocessing
from uuid import uuid4

import pytest

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.models.framework_model_evidence import FrameworkModelEvidence
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls, FrameworkModelRecoveryRequired
from tests.test_agent_task_dispatch import harness
from tests.test_artifact_storage_service import _RustFSClient, _settings
from tests.test_framework_model_calls import model_calls_harness, begin, intent, policy


def upload_target(h, selected, stage, upload_id, content=b'{"fixture":"upload"}'):
    from src.schemas.framework_model_call import FrameworkModelObject

    prefix = f"framework-models/{hashlib.sha256(h.binding.binding_id.encode()).hexdigest()}"
    prefix += f"/{hashlib.sha256(selected.attempt_id.encode()).hexdigest()}/{upload_id}"
    extension = "bin" if stage.startswith("wire-") else "json"
    return FrameworkModelObject(storage_uri=f"rustfs://{h.settings.storage.rustfs_bucket}/{prefix}/{stage}.{extension}",
        sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))


async def admitted(h, *, stage="request", begin_execution=True):
    if begin_execution:
        await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1, request_sha256=hashlib.sha256(b'{"fixture":"upload"}').hexdigest())
    await h.calls.prepare_initial(h.owner, selected)
    if stage != "request":
        await h.calls.start(h.owner, selected.attempt_id)
    return selected


def evidence_store(h, monkeypatch):
    objects, client = ArtifactStorageService(h.settings), _RustFSClient()
    monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
    return FrameworkModelEvidence(objects=objects, writes=h.calls), objects, client


@pytest.mark.asyncio
async def test_storage_description_freezes_the_actual_target_without_io(tmp_path):
    service = ArtifactStorageService(_settings(tmp_path))
    client = _RustFSClient()
    service._rustfs_client = lambda: client
    arguments = dict(content=b"fixture", filename="a b.json", object_prefix="x y/z", content_type="application/json")
    description = service.describe_uploaded_bytes(**arguments)
    assert client.objects == {} and client.buckets == set()
    saved = await service.store_uploaded_bytes(**arguments)
    assert saved == description and saved["object_name"] == "x_y/z/a_b.json"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["request", "response", "wire-request", "wire-response"])
async def test_upload_identity_is_immutable_restart_readable_and_not_a_budget_fact(model_calls_harness, stage):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1, request_sha256=hashlib.sha256(b'{"fixture":"upload"}').hexdigest())
    await h.calls.prepare_initial(h.owner, selected)
    if stage != "request":
        await h.calls.start(h.owner, selected.attempt_id)
    before = await h.calls.snapshot(h.binding)
    upload_id = uuid4().hex
    target = upload_target(h, selected, stage, upload_id)
    saved = await h.calls.prepare_evidence_upload(h.owner, selected, upload_id=upload_id, stage=stage, target=target)
    assert saved.target == target and saved.intent == selected and saved.stage == stage
    assert saved.authoritative is False and saved.binding_fingerprint == h.binding.fingerprint
    assert h.owner.owner_token not in saved.model_dump_json()
    assert await h.calls.prepare_evidence_upload(h.owner, selected, upload_id=upload_id, stage=stage, target=target) == saved
    restarted = PostgresFrameworkModelCalls(h.settings)
    assert await restarted.evidence_uploads(h.binding, attempt_id=selected.attempt_id) == [saved]
    assert await restarted.get_evidence_upload(h.binding, upload_id) == saved
    assert await restarted.snapshot(h.binding) == before
    with pytest.raises(ValueError, match="identity"):
        await h.calls.prepare_evidence_upload(h.owner, selected, upload_id=upload_id, stage=stage,
            target=target.model_copy(update={"sha256": "f" * 64}))
    assert await restarted.evidence_uploads(h.binding) == [saved]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["owner_token", "owner_epoch", "binding", "intent", "target"])
async def test_upload_intent_rejects_forged_origins_and_foreign_object_targets(model_calls_harness, changed):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1, request_sha256=hashlib.sha256(b'{"fixture":"upload"}').hexdigest())
    await h.calls.prepare_initial(h.owner, selected)
    upload_id = uuid4().hex
    owner, chosen, target = h.owner, selected, upload_target(h, selected, "request", upload_id)
    if changed == "owner_token":
        owner = owner.model_copy(update={"owner_token": "forged-fixture"})
    elif changed == "owner_epoch":
        owner = owner.model_copy(update={"owner_epoch": owner.owner_epoch + 1})
    elif changed == "binding":
        owner = owner.model_copy(update={"binding": h.binding.model_copy(update={"prompt_version": "foreign"})})
    elif changed == "intent":
        chosen = selected.model_copy(update={"request_sha256": "f" * 64})
    else:
        target = target.model_copy(update={"storage_uri": "rustfs://foreign/foreign.json"})
    with pytest.raises((ValueError, RuntimeError)):
        await h.calls.prepare_evidence_upload(owner, chosen, upload_id=upload_id, stage="request", target=target)
    assert await h.calls.evidence_uploads(h.binding) == []


@pytest.mark.asyncio
async def test_unbound_request_upload_cannot_be_replaced_or_used_as_send_permission(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1, request_sha256=hashlib.sha256(b'{"fixture":"upload"}').hexdigest())
    await h.calls.prepare_initial(h.owner, selected)
    upload_id = uuid4().hex
    target = upload_target(h, selected, "request", upload_id)
    await h.calls.prepare_evidence_upload(h.owner, selected, upload_id=upload_id, stage="request", target=target)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(FrameworkModelRecoveryRequired, match="request_upload_requires_reconciliation"):
        await h.calls.start(h.owner, selected.attempt_id)
    replacement = uuid4().hex
    with pytest.raises(FrameworkModelRecoveryRequired, match="request_upload_requires_reconciliation"):
        await h.calls.prepare_evidence_upload(h.owner, selected, upload_id=replacement, stage="request",
            target=upload_target(h, selected, "request", replacement))
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["graph", "dto"])
@pytest.mark.parametrize("stage", ["request.json", "response.json", "wire-request.bin", "wire-response.bin"])
@pytest.mark.parametrize("outcome", ["failed", "ack_lost"])
async def test_upload_target_is_committed_before_put_and_readback_never_replays(
    harness, monkeypatch, path, stage, outcome,
):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.execution_context import framework_owner_scope
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_framework_model_dto import dto_request
    from tests.test_framework_model_policy import execute, governed

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            client = _RustFSClient()
            objects = ArtifactStorageService(h.settings)
            monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
            evidence = FrameworkModelEvidence(objects=objects, writes=h.calls)
            h.policy._evidence = evidence
            h.models.set_framework_model_call_policy(h.policy)
            put, observed = client.put_object, []

            def uncertain(**kwargs):
                if kwargs["Key"].rsplit("/", 1)[-1] != stage:
                    return put(**kwargs)
                with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT record FROM {h.calls.uploads_table} WHERE binding_id=%s", (h.binding.binding_id,))
                    observed.extend(row["record"] for row in cur.fetchall()
                        if row["record"]["target"]["storage_uri"] == f"rustfs://{kwargs['Bucket']}/{kwargs['Key']}")
                if outcome == "ack_lost":
                    put(**kwargs)
                raise ConnectionError("controlled unknown upload result")

            monkeypatch.setattr(client, "put_object", uncertain)

            async def invoke():
                with framework_owner_scope(h.owner.runtime_owner):
                    return await (execute(h) if path == "graph" else h.models.invoke(h.request.model_key, dto_request(h)))

            with pytest.raises(RuntimeError):
                await invoke()
            assert len(observed) == 1, "Object upload began without its committed forensic identity"
            original = await h.calls.snapshot(h.binding)
            restarted = PostgresFrameworkModelCalls(h.settings)
            uploads = await restarted.evidence_uploads(h.binding)
            selected, = [item for item in uploads if item.stage == stage.rsplit(".", 1)[0]]
            assert selected.model_dump(mode="json") == observed[0]
            reader = FrameworkModelEvidence(objects=objects, writes=restarted)
            if outcome == "ack_lost":
                readback = await reader.read_upload(h.binding, selected.upload_id)
                assert readback.upload == selected and readback.authoritative is False
                assert hashlib.sha256(readback.content).hexdigest() == selected.target.sha256
            else:
                with pytest.raises(RuntimeError):
                    await reader.read_upload(h.binding, selected.upload_id)
            assert await restarted.snapshot(h.binding) == original
            assert await restarted.evidence_uploads(h.binding) == uploads
            assert original.actual_tokens == original.unknown_calls == 0
            assert len(requests) == (stage != "request.json")
            if stage == "request.json":
                with pytest.raises((RuntimeError, ValueError)):
                    await (execute(h, replace(h.request, continue_from_checkpoint=True)) if path == "graph" else invoke())
                assert await restarted.evidence_uploads(h.binding) == uploads
                assert len(requests) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["origin", "bucket"])
async def test_readback_rejects_journal_identity_corruption_before_object_io(model_calls_harness, monkeypatch, changed):
    from src.infrastructure.postgres_runtime import postgres_connect

    h = model_calls_harness
    selected = await admitted(h, stage="wire-request")
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence._store_bytes(h.owner, selected, b"fixture", "wire-request")
    saved, = await h.calls.evidence_uploads(h.binding)
    before = await h.calls.snapshot(h.binding)
    data = saved.model_dump(mode="json")
    if changed == "origin":
        data["origin"]["observer_token_sha256"] = "f" * 64
    else:
        data["target"]["storage_uri"] = data["target"]["storage_uri"].replace(
            f"rustfs://{h.settings.storage.rustfs_bucket}/", "rustfs://foreign/")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.uploads_table} SET record=%s::jsonb WHERE binding_id=%s",
            (json.dumps(data), h.binding.binding_id))
    reads = []
    get = client.get_object
    monkeypatch.setattr(client, "get_object", lambda **kwargs: reads.append(kwargs) or get(**kwargs))
    with pytest.raises(ValueError, match="identity"):
        await evidence.read_upload(h.binding, saved.upload_id)
    assert reads == [] and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_request_attachment_cannot_replace_the_frozen_upload_address(model_calls_harness):
    h = model_calls_harness
    selected = await admitted(h)
    upload_id = uuid4().hex
    target = upload_target(h, selected, "request", upload_id)
    await h.calls.prepare_evidence_upload(h.owner, selected, upload_id=upload_id, stage="request", target=target)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ValueError, match="identity"):
        await h.calls.attach_request(h.owner, selected.attempt_id,
            request=target.model_copy(update={"storage_uri": "rustfs://foreign/identical-digest.json"}))
    assert await h.calls.snapshot(h.binding) == before
    await h.calls.attach_request(h.owner, selected.attempt_id, request=target)
    assert (await h.calls.start(h.owner, selected.attempt_id))[1] is True


@pytest.mark.asyncio
async def test_upload_preparation_is_concurrently_idempotent(model_calls_harness):
    h = model_calls_harness
    selected = await admitted(h)
    upload_id = uuid4().hex
    arguments = dict(upload_id=upload_id, stage="request", target=upload_target(h, selected, "request", upload_id))
    before = await h.calls.snapshot(h.binding)
    saved = await asyncio.gather(*(h.calls.prepare_evidence_upload(h.owner, selected, **arguments) for _ in range(6)))
    assert all(item == saved[0] for item in saved)
    assert await h.calls.evidence_uploads(h.binding) == [saved[0]]
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rollback", "ack_lost"])
async def test_upload_intent_commit_uncertainty_never_starts_put(model_calls_harness, monkeypatch, outcome):
    from contextlib import contextmanager
    from src.runtime import postgres_framework_model_calls as ledger

    h = model_calls_harness
    selected = await admitted(h)
    evidence, objects, client = evidence_store(h, monkeypatch)
    before = await h.calls.snapshot(h.binding)
    connect = ledger.postgres_connect

    @contextmanager
    def uncertain_commit(settings):
        with connect(settings) as conn:
            yield conn
            if outcome == "rollback":
                raise ConnectionError("controlled upload intent rollback")
        raise ConnectionError("controlled upload intent commit acknowledgement loss")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "postgres_connect", uncertain_commit)
        with pytest.raises(ConnectionError):
            await evidence.store_request(h.owner, selected, b'{"fixture":"upload"}')
    assert client.objects == {} and client.buckets == set()
    assert len(await h.calls.evidence_uploads(h.binding)) == (outcome == "ack_lost")
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["commit", "rollback"])
async def test_cancellation_drains_upload_intent_without_put(model_calls_harness, monkeypatch, outcome):
    from tests.test_framework_execution_deadline import pause_admission_commit

    h = model_calls_harness
    selected = await admitted(h)
    evidence, objects, client = evidence_store(h, monkeypatch)
    before = await h.calls.snapshot(h.binding)
    entered, release = pause_admission_commit(h, monkeypatch, boundary="mutation", outcome=outcome)
    task = asyncio.create_task(evidence.store_request(h.owner, selected, b'{"fixture":"upload"}'))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("first upload intent stop")
        await asyncio.sleep(.05)
        task.cancel("second upload intent stop")
        await asyncio.sleep(.05)
        assert not task.done() and client.objects == {}
        release.set()
        with pytest.raises(asyncio.CancelledError, match="first upload intent stop"):
            await task
        assert len(await h.calls.evidence_uploads(h.binding)) == (outcome == "commit")
        assert client.objects == {} and client.buckets == set()
        assert await h.calls.snapshot(h.binding) == before
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["request", "response", "wire-request", "wire-response"])
async def test_expired_original_owner_only_prepares_late_sent_evidence(model_calls_harness, monkeypatch, stage):
    from src.runtime.store import ContinuationLeaseLostError

    h = model_calls_harness
    selected = await admitted(h, stage=stage)
    evidence, objects, client = evidence_store(h, monkeypatch)
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    before = await h.calls.snapshot(h.binding)
    content = b'{"fixture":"upload"}'
    if stage == "request":
        with pytest.raises(ContinuationLeaseLostError):
            await evidence.store_request(h.owner, selected, content)
        assert await h.calls.evidence_uploads(h.binding) == [] and client.objects == {}
    else:
        content = json.dumps(dict(schema_version=1, source="sdk_application", binding_id=h.binding.binding_id,
            binding_fingerprint=h.binding.fingerprint, attempt_id=selected.attempt_id,
            logical_call_id=selected.logical_call_id, request_sha256=selected.request_sha256)).encode()
        if stage.startswith("wire-"):
            await evidence._store_bytes(h.owner, selected, content, stage)
        else:
            await evidence._store(h.owner, selected, content, stage)
        saved, = await h.calls.evidence_uploads(h.binding)
        assert (await evidence.read_upload(h.binding, saved.upload_id)).content == content
        with pytest.raises(ValueError, match="identity"):
            await h.calls.prepare_evidence_upload(h.owner.model_copy(update={"owner_token": "foreign-fixture"}),
                selected, upload_id=uuid4().hex, stage=stage, target=saved.target)
        with pytest.raises(ContinuationLeaseLostError):
            await h.calls.complete(h.owner, selected.attempt_id, usage=None)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "bytes", "journal_drift", "unknown_upload", "binding"])
async def test_readback_failures_do_not_change_the_model_ledger(model_calls_harness, monkeypatch, failure):
    from src.infrastructure.postgres_runtime import postgres_connect

    h = model_calls_harness
    selected = await admitted(h, stage="wire-response")
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence._store_bytes(h.owner, selected, b"fixture-private-content", "wire-response")
    saved, = await h.calls.evidence_uploads(h.binding)
    assert "fixture-private-content" not in repr(await evidence.read_upload(h.binding, saved.upload_id))
    before = await h.calls.snapshot(h.binding)
    key, = client.objects
    if failure == "missing":
        client.objects.clear()
    elif failure == "bytes":
        client.objects[key] = (b"tampered", "application/octet-stream")
    elif failure == "journal_drift":
        get = client.get_object

        def drift(**kwargs):
            result = get(**kwargs)
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {h.calls.uploads_table} SET record=jsonb_set(record,'{{target,sha256}}',%s::jsonb) "
                    "WHERE binding_id=%s", (json.dumps("f" * 64), h.binding.binding_id))
            return result

        monkeypatch.setattr(client, "get_object", drift)
    binding = h.binding if failure != "binding" else h.binding.model_copy(update={"prompt_version": "foreign"})
    upload_id = saved.upload_id if failure != "unknown_upload" else uuid4().hex
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        await evidence.read_upload(binding, upload_id)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_response_readback_validates_original_envelope_not_just_digest(model_calls_harness, monkeypatch):
    h = model_calls_harness
    selected = await admitted(h, stage="response")
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence._store(h.owner, selected, b'{"source":"sdk_application","attempt_id":"foreign"}', "response")
    saved, = await h.calls.evidence_uploads(h.binding)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ValueError, match="identity"):
        await evidence.read_upload(h.binding, saved.upload_id)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_transfer_retains_predecessor_upload_but_does_not_admit_an_unbound_request(model_calls_harness, monkeypatch):
    from tests.test_framework_model_reservation_transfer import replacement

    h = model_calls_harness
    selected = await admitted(h, begin_execution=False)
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence.store_request(h.owner, selected, b'{"fixture":"upload"}')
    upload, = await h.calls.evidence_uploads(h.binding)
    original = (await h.calls.snapshot(h.binding)).calls[0]
    current = await replacement(h, h.task)
    await h.calls.transfer_ready(current.runtime_owner, expected=original)
    before = await h.calls.snapshot(h.binding)
    assert (await evidence.read_upload(h.binding, upload.upload_id)).upload == upload
    with pytest.raises(FrameworkModelRecoveryRequired, match="request_upload_requires_reconciliation"):
        await h.calls.start(current.runtime_owner, selected.attempt_id)
    assert await h.calls.snapshot(h.binding) == before
    assert await h.calls.evidence_uploads(h.binding) == [upload]


class FileUploadClient(_RustFSClient):
    """Cross-process bytes only; deliberately not a real S3/RustFS server."""

    def __init__(self, root):
        super().__init__()
        self.root = Path(root).resolve(strict=True)

    def path(self, bucket, key):
        return self.root / hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.path(Bucket, Key).write_bytes(Body)

    def get_object(self, *, Bucket, Key):
        from tests.test_artifact_storage_service import _Body

        content = self.path(Bucket, Key).read_bytes()
        return {"Body": _Body(content), "ContentType": "application/json", "ContentLength": len(content)}


def upload_worker_until_ack(settings_data, owner_data, intent_data, root, written, entered):
    import threading
    from src.core.config import Settings
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    from src.schemas.framework_model_call import FrameworkModelCallIntent

    settings = Settings.model_validate(settings_data)
    objects, client = ArtifactStorageService(settings), FileUploadClient(root)
    put = client.put_object

    def blocked_put(**kwargs):
        if written:
            put(**kwargs)
        entered.set()
        threading.Event().wait()

    client.put_object = blocked_put
    objects._rustfs_client = lambda: client
    evidence = FrameworkModelEvidence(objects=objects, writes=PostgresFrameworkModelCalls(settings))
    asyncio.run(evidence.store_request(RuntimeExecutionOwner.model_validate(owner_data),
        FrameworkModelCallIntent.model_validate(intent_data), b'{"fixture":"upload"}'))


@pytest.mark.asyncio
@pytest.mark.parametrize("written", [False, True])
async def test_durable_target_survives_real_uploader_termination_without_send_permission(
    model_calls_harness, monkeypatch, tmp_path, written,
):
    h = model_calls_harness
    selected = await admitted(h)
    assert await h.tasks.renew_framework_execution(h.task, 120)
    before = await h.calls.snapshot(h.binding)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=upload_worker_until_ack,
        args=(h.settings.model_dump(mode="json"), h.owner.model_dump(mode="json"),
            selected.model_dump(mode="json"), str(tmp_path), written, entered))
    try:
        process.start()
        assert await asyncio.to_thread(entered.wait, 35), f"Uploader did not reach PUT; exit={process.exitcode}"
        uploads = await h.calls.evidence_uploads(h.binding)
        assert len(uploads) == 1 and process.is_alive()
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive() and process.exitcode != 0
        calls = PostgresFrameworkModelCalls(h.settings)
        objects = ArtifactStorageService(h.settings)
        monkeypatch.setattr(objects, "_rustfs_client", lambda: FileUploadClient(tmp_path))
        reader = FrameworkModelEvidence(objects=objects, writes=calls)
        if written:
            assert (await reader.read_upload(h.binding, uploads[0].upload_id)).content == b'{"fixture":"upload"}'
        else:
            with pytest.raises(RuntimeError):
                await reader.read_upload(h.binding, uploads[0].upload_id)
        with pytest.raises(FrameworkModelRecoveryRequired, match="request_upload_requires_reconciliation"):
            await calls.start(h.owner, selected.attempt_id)
        assert await calls.snapshot(h.binding) == before
        assert await calls.evidence_uploads(h.binding) == uploads
        assert len(list(tmp_path.iterdir())) == written
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)

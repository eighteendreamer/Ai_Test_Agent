from __future__ import annotations

import hashlib
import json
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_model_call import FrameworkModelCallRecord, FrameworkModelObject, model_evidence_prefix
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_model_calls import intent


def database_state(h):
    # Compare exact rows without printing lease tokens in assertion diagnostics.
    tables = [getattr(h.settings.database, f"postgres_{name}_table") for name in (
        "session", "message", "event", "task_outbox", "agent_task", "agent_attempt")]
    tables += [f"{h.tasks._tasks}_runtime", f"{h.tasks._tasks}_message_stage",
        h.calls.budgets_table, h.calls.calls_table, h.calls.uploads_table]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        values = {}
        for table in tables:
            cur.execute(f"SELECT * FROM {table}")
            rows = sorted(json.dumps(row, sort_keys=True, default=str) for row in cur.fetchall())
            values[table] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
        return values


async def prepare_call(h, *, expire=True):
    submitted = await submit(h)
    h.original = await h.tasks.claim(submitted.id, worker_id="original", expected_binding=h.binding)
    h.call = await h.calls.prepare_initial(h.original.runtime_owner,
        intent(position_version=1, model_config_fingerprint=h.binding.model_config_fingerprint))
    if expire:
        assert await h.store.renew_turn_execution(h.session_id, h.original.lease_token, 0)
    return h.call


def dispatcher(h, capabilities=None):
    selected = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
        framework_model_calls=h.calls,
        framework_capabilities=capabilities or FrameworkWorkerCapabilities("a" * 64, True))
    h.executions = []
    h.claimed_epochs = []

    async def execute(task):
        h.executions.append(task.id)
        h.claimed_epochs.append(task.runtime_owner.owner_epoch)

    selected._execute = execute
    return selected


async def deliver(h, selected):
    await selected.handle_v2(QueuedTask("format-admission", h.command.model_dump(mode="json")),
        worker_id="format-reader")


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [
    (["schema_version"], 4),
    (["future_field"], "private-format-diagnostic"),
    (["intent", "future_field"], "private-format-diagnostic"),
])
async def test_incompatible_model_call_is_deferred_before_replacement_claim(admission, path, value, caplog):
    h = admission
    await prepare_call(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,%s,%s::jsonb) "
            "WHERE binding_id=%s", (path, json.dumps(value), h.binding.binding_id))
    before = database_state(h)
    selected = dispatcher(h)
    with pytest.raises(TaskDeferred, match="framework_model_call_format_unavailable"):
        await deliver(h, selected)
    assert database_state(h) == before
    assert h.executions == h.runtime.calls == []
    saved = await h.tasks.get(h.command.task_id)
    assert saved.attempt_no == 1 and saved.attempt_id == h.original.attempt_id
    assert saved.worker_id == "original" and saved.status == "claimed"
    records = [row for row in caplog.records if row.message == "framework_model_reader_unavailable"]
    assert len(records) == 1 and records[0].binding_id == h.binding.binding_id
    assert records[0].worker_id == "format-reader" and records[0].task_id == h.command.task_id
    assert records[0].reason == "framework_model_call_format_unavailable"
    assert "private-format-diagnostic" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [(["schema_version"], 2),
    (["future_field"], "private-format-diagnostic")])
async def test_incompatible_budget_cannot_consume_first_attempt(admission, path, value):
    h = admission
    await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,%s,%s::jsonb) "
            "WHERE binding_id=%s", (path, json.dumps(value), h.binding.binding_id))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_model_budget_format_unavailable"):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == h.runtime.calls == []
    assert (await h.tasks.get(h.command.task_id)).attempt_no == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [(["schema_version"], 2),
    (["target", "future_field"], "private-format-diagnostic")])
async def test_incompatible_upload_intent_is_not_ignored_during_admission(admission, path, value):
    h = admission
    call = await prepare_call(h, expire=False)
    # Create an actual intent with no object-store I/O before expiring owner.
    upload_id = uuid4().hex
    target = FrameworkModelObject(storage_uri=f"rustfs://{h.settings.storage.rustfs_bucket}/"
        f"{model_evidence_prefix(h.binding.binding_id, call.intent.attempt_id, upload_id)}/request.json",
        sha256=call.intent.request_sha256, size_bytes=1)
    await h.calls.prepare_evidence_upload(h.original.runtime_owner, call.intent,
        upload_id=upload_id, stage="request", target=target)
    assert await h.store.renew_turn_execution(h.session_id, h.original.lease_token, 0)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.uploads_table} SET record=jsonb_set(record,%s,%s::jsonb) "
            "WHERE binding_id=%s", (path, json.dumps(value), h.binding.binding_id))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_model_upload_format_unavailable"):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == h.runtime.calls == []


@pytest.mark.asyncio
async def test_ready_transfer_requires_compatible_reader_without_changing_original_call(admission):
    h = admission
    original = await prepare_call(h)
    replacement = await h.tasks.claim(h.command.task_id, worker_id="transfer", expected_binding=h.binding)
    transferred = await h.calls.transfer_ready(replacement.runtime_owner, expected=original)
    assert transferred.schema_version == 2 and len(transferred.reservation_transfers) == 1
    assert await h.store.renew_turn_execution(h.session_id, replacement.lease_token, 0)
    before = database_state(h)
    caps = FrameworkWorkerCapabilities("a" * 64, True)
    with pytest.raises(TaskDeferred, match="framework_model_call_format_unavailable"):
        await deliver(h, dispatcher(h, replace(caps, model_call_max_version=1)))
    assert database_state(h) == before and h.executions == []
    await deliver(h, dispatcher(h, caps))
    assert h.executions == [h.command.task_id] and h.runtime.calls == []
    saved = await h.tasks.get(h.command.task_id)
    assert saved.attempt_no == 3 and h.claimed_epochs == [3]
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.calls == [transferred] and snapshot.reserved_calls == 1
    assert snapshot.policy == h.budget and snapshot.charged_tokens == original.intent.reserved_tokens


@pytest.mark.asyncio
async def test_model_format_is_rechecked_after_waiting_for_claim_lock(admission, monkeypatch):
    h = admission
    await prepare_call(h)
    selected = dispatcher(h)
    reached, original = Event(), h.tasks._lock_session

    def lock(cur, session_id):
        reached.set()
        return original(cur, session_id)

    monkeypatch.setattr(h.tasks, "_lock_session", lock)
    pending = None
    try:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {h.tasks._session_table} WHERE id=%s FOR UPDATE", (h.session_id,))
            pending = asyncio.create_task(deliver(h, selected))
            assert await asyncio.to_thread(reached.wait, 5)
            with postgres_connect(h.settings) as change, change.cursor() as writer:
                writer.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{schema_version}}','4') "
                    "WHERE binding_id=%s", (h.binding.binding_id,))
            before = database_state(h)
        with pytest.raises(TaskDeferred, match="framework_model_call_format_unavailable"):
            await pending
        assert database_state(h) == before and h.executions == h.runtime.calls == []
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


def call_value():
    return FrameworkModelCallRecord(binding_id="binding-format", intent=intent(position_version=1),
        owner_epoch=1, owner_task_id="task-format", owner_attempt_id="worker-attempt",
        phase="ready", created_at=datetime.now(timezone.utc)).model_dump(mode="json")


@pytest.mark.parametrize("version", [None, 1, 2, 3])
def test_installed_reader_accepts_legacy_defaults_and_supported_call_formats(version):
    value = call_value()
    if version is None:
        value.pop("schema_version")
    else:
        value["schema_version"] = version
    FrameworkWorkerCapabilities("a" * 64, True).require_model_ledger_format("call", value)


@pytest.mark.parametrize("version", [None, True, False, "3", 3.0, 0, -1, 4])
def test_reader_does_not_coerce_unknown_explicit_call_versions(version):
    value = call_value()
    value["schema_version"] = version
    with pytest.raises(TaskDeferred, match="framework_model_call_format_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True).require_model_ledger_format("call", value)


@pytest.mark.parametrize("change", ["nested_version", "nested_field", "position_version"])
def test_nested_model_protocol_formats_are_checked(change):
    value = call_value()
    if change == "position_version":
        value["intent"]["position_version"] = 2
    else:
        value["response_observations"] = [{"schema_version": 1, "observation_id": "c" * 64,
            "response": {"storage_uri": "rustfs://fixture/response", "sha256": "d" * 64, "size_bytes": 1},
            "response_kind": "langchain_messages", "authoritative": False,
            "observed_at": value["created_at"]}]
        value["response_observations"][0]["schema_version" if change == "nested_version" else "future_field"] = 2
    with pytest.raises(TaskDeferred, match="framework_model_call_format_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True).require_model_ledger_format("call", value)


def test_known_data_error_is_not_disguised_as_format_deferral():
    value = call_value()
    value["usage"] = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 1}
    with pytest.raises(ValidationError, match="total usage"):
        FrameworkWorkerCapabilities("a" * 64, True).require_model_ledger_format("call", value)


@pytest.mark.asyncio
async def test_known_model_data_error_rolls_back_claim_without_compatibility_fallback(admission):
    h = admission
    await prepare_call(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{usage}}',%s::jsonb) "
            "WHERE binding_id=%s", (json.dumps({"input_tokens": 10, "output_tokens": 10, "total_tokens": 1}),
             h.binding.binding_id))
    before = database_state(h)
    with pytest.raises(ValidationError, match="total usage"):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == h.runtime.calls == []


@pytest.mark.asyncio
async def test_terminal_budgetless_task_does_not_receive_new_allowance(admission):
    h = admission
    submitted = await h.tasks.submit_v2(h.command, h.payload, h.message, idempotency_key=h.key)
    original = await h.tasks.claim(submitted.id, worker_id="budgetless-original", expected_binding=h.binding)
    await h.tasks.finish(original, status="cancelled", error_code="test_fixture_cancelled")
    before = database_state(h)
    selected = dispatcher(h)
    await deliver(h, selected)
    assert database_state(h) == before and h.executions == h.runtime.calls == []


@pytest.mark.asyncio
async def test_incompatible_consumer_keeps_delivery_for_compatible_terminal_reader(admission):
    h = admission
    call = await prepare_call(h)
    task = await h.tasks.claim(h.command.task_id, worker_id="transfer", expected_binding=h.binding)
    transferred = await h.calls.transfer_ready(task.runtime_owner, expected=call)
    assert transferred.schema_version == 2
    await h.tasks.finish(task, status="cancelled", error_code="test_fixture_cancelled")
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="model-reader", client=h.redis)
    await queue.connect()
    assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
    before = database_state(h)
    caps = FrameworkWorkerCapabilities("a" * 64, True)
    old = dispatcher(h, replace(caps, model_call_max_version=1))

    async def old_handler(item):
        await old.handle_v2(item, worker_id="old-format-reader")

    worker = RedisTaskWorker(queue, consumer="old-format-reader", handler=old_handler, block_ms=0)
    assert await worker.run_once() == 0
    pending = await queue.pending()
    assert len(pending) == 1 and pending[0]["consumer"] == "old-format-reader"
    assert database_state(h) == before and h.executions == []
    assert await h.redis.xlen(f"{queue.stream}:dead_letter") == 0
    assert await h.redis.xlen(queue.stream) == 1
    selected = dispatcher(h, caps)
    # Transfer only this test's PEL entry, then consume that consumer's pending
    # entry. No timing assumption or production reclaim interval is changed.
    await h.redis.xclaim(queue.stream, queue.group, "compatible-format-reader", 0,
        [pending[0]["message_id"]], justid=True)
    reclaimed = await queue.consume(consumer="compatible-format-reader", stream_id="0", block_ms=0)
    assert len(reclaimed) == 1 and reclaimed[0].retry_count == 0
    assert reclaimed[0].payload == h.command.model_dump(mode="json")
    await selected.handle_v2(reclaimed[0], worker_id="compatible-format-reader")
    assert await queue.ack_owned(reclaimed[0], consumer="compatible-format-reader")
    assert not await queue.pending() and database_state(h) == before
    assert h.executions == h.runtime.calls == []
    assert await h.redis.xlen(f"{queue.stream}:dead_letter") == 0

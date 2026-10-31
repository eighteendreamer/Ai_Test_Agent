from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from threading import Event

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.framework_message_stages import message_stage_table
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_model_reader_admission import database_state, dispatcher, deliver
from tests.test_framework_context_transcript import covered_asset
from tests.test_framework_application_projection import projection
from tests.test_framework_runtime_result_asset import frozen_result


@pytest_asyncio.fixture(autouse=True)
async def assert_checkpoint_isolation(isolate_recovery_stores, admission):
    assert admission.settings.deep_agents.checkpoint_schema == admission.prefix + "_checkpoints"


@pytest_asyncio.fixture
async def transcript_admission(admission):
    h = admission
    h.binding = h.binding.model_copy(update={"runtime_version": "v2.1.0"})
    h.command = h.command.model_copy(update={"runtime_binding": h.binding})
    session = await h.store.get_session(h.session_id)
    h.transcript = covered_asset(session, h.binding)
    session.metadata["framework_transcript_asset"] = h.transcript
    await h.store.save_session(session)
    h.submitted = await submit(h)
    return h


def save_transcript(h, value):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._session_table} SET metadata=jsonb_set(metadata,'{{framework_transcript_asset}}',%s::jsonb) "
            "WHERE id=%s", (json.dumps(value), h.session_id))


@pytest.mark.asyncio
async def test_transcript_is_rechecked_after_waiting_for_session_lock(transcript_admission, monkeypatch):
    h = transcript_admission
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
            before = database_state(h)
            future = {**deepcopy(h.transcript), "schema_version": 99}
            cur.execute(f"UPDATE {h.tasks._session_table} SET metadata=jsonb_set(metadata,'{{framework_transcript_asset}}',%s::jsonb) "
                "WHERE id=%s", (json.dumps(future), h.session_id))
            # Read our own change before releasing the lock. An independent
            # snapshot cannot see the uncommitted metadata yet.
            cur.execute(f"SELECT * FROM {h.tasks._session_table}")
            rows = sorted(json.dumps(row, sort_keys=True, default=str) for row in cur.fetchall())
            before[h.tasks._session_table] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
        with pytest.raises(TaskDeferred, match="framework_transcript_format_unavailable"):
            await pending
        assert database_state(h) == before and h.executions == []
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_unstarted_claim_checks_current_transcript_before_new_attempt(transcript_admission):
    h = transcript_admission
    owner = await h.tasks.claim(h.submitted.id, worker_id="original-transcript", expected_binding=h.binding)
    assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
    save_transcript(h, {**h.transcript, "future_field": "private-transcript-input"})
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_transcript_format_unavailable"):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == []


def changed_transcript(h, change):
    value = deepcopy(h.transcript)
    if change == "schema_version":
        value[change] = 99
    elif change == "future_field":
        value[change] = "private-transcript-input"
    elif change == "content_sha256":
        value[change] = "0" * 64
    else:
        if change == "native_type":
            value["framework_messages"][-1]["type"] = "future-native-message"
        elif change == "tool_protocol":
            value["framework_messages"].pop(2)
        else:
            value[change] = "different-protocol-or-identity"
        value["content_sha256"] = approval_value_hash({key: item for key, item in value.items() if key != "content_sha256"})
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["schema_version", "future_field", "content_sha256", "model_config_fingerprint",
    "serializer", "session_id", "native_type", "tool_protocol"])
async def test_locked_claim_reuses_the_actual_transcript_reader_before_writes(transcript_admission, change, caplog):
    h = transcript_admission
    save_transcript(h, changed_transcript(h, change))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_transcript_format_unavailable") as caught:
        await h.tasks.claim(h.submitted.id, worker_id="locked-transcript-reader", expected_binding=h.binding,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert database_state(h) == before
    assert caught.value.__context__ is None or caught.value.__suppress_context__
    records = [row for row in caplog.records if row.message == "framework_transcript_reader_unavailable"]
    assert len(records) == 1 and records[0].task_id == h.submitted.id
    assert records[0].worker_id == "locked-transcript-reader" and records[0].binding_id == h.binding.binding_id
    assert records[0].reason == "framework_transcript_format_unavailable" and records[0].exc_info is None
    assert "private-transcript-input" not in caplog.text and "different-protocol-or-identity" not in caplog.text


@pytest.mark.asyncio
async def test_waiting_resource_claim_does_not_consume_another_attempt_for_unknown_history(transcript_admission):
    h = transcript_admission
    owner = await h.tasks.claim(h.submitted.id, worker_id="waiting-transcript", expected_binding=h.binding)
    await h.tasks.defer(owner, reason="fixture_resource_busy")
    save_transcript(h, changed_transcript(h, "schema_version"))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_transcript_format_unavailable"):
        await h.tasks.claim(h.submitted.id, worker_id="waiting-transcript-reader", expected_binding=h.binding,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert database_state(h) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("check_model_ledger", [False, True])
async def test_live_transcript_is_checked_without_a_stage_or_optional_ledger_port(transcript_admission, check_model_ledger):
    h = transcript_admission
    # Explicit historical shape: no stage row, not a malformed JSON record.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {message_stage_table(h.settings)} WHERE binding_id=%s", (h.binding.binding_id,))
    save_transcript(h, changed_transcript(h, "schema_version"))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_transcript_format_unavailable"):
        await h.tasks.claim(h.submitted.id, worker_id="historical-transcript-reader", expected_binding=h.binding,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True), check_model_ledger=check_model_ledger)
    assert database_state(h) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_compatible_or_absent_transcript_preserves_first_claim(transcript_admission, missing):
    h = transcript_admission
    if missing:
        save_transcript(h, None)  # The existing import reader treats JSON null as absent.
    await deliver(h, dispatcher(h))
    saved = await h.tasks.get(h.submitted.id)
    assert saved.status == "claimed" and saved.attempt_no == 1 and h.executions == [saved.id]


@pytest.mark.asyncio
async def test_expired_unstarted_compatible_transcript_uses_original_reclaim(transcript_admission):
    h = transcript_admission
    owner = await h.tasks.claim(h.submitted.id, worker_id="original-transcript", expected_binding=h.binding)
    assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
    await deliver(h, dispatcher(h))
    saved = await h.tasks.get(h.submitted.id)
    assert saved.status == "claimed" and saved.attempt_no == 2 and h.executions == [saved.id]


@pytest.mark.asyncio
async def test_terminal_redelivery_does_not_import_a_later_session_asset(transcript_admission):
    h = transcript_admission
    owner = await h.tasks.claim(h.submitted.id, worker_id="cancelled-transcript", expected_binding=h.binding)
    await h.tasks.finish(owner, status="cancelled", error_code="test_fixture_cancelled")
    save_transcript(h, changed_transcript(h, "schema_version"))
    before = database_state(h)
    await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == []


@pytest.mark.asyncio
async def test_running_expiry_keeps_recovery_semantics_without_importing_latest_history(transcript_admission):
    h = transcript_admission
    owner = await h.tasks.claim(h.submitted.id, worker_id="running-transcript", expected_binding=h.binding)
    await h.tasks.begin(owner, resource_id="transcript-fixture", fencing_token=1)
    assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
    save_transcript(h, changed_transcript(h, "schema_version"))
    saved = await h.tasks.claim(h.submitted.id, worker_id="expiry-transcript", expected_binding=h.binding,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert saved.status == "recovery_required" and saved.attempt_no == 1


@pytest.mark.asyncio
async def test_frozen_runtime_result_does_not_reopen_the_latest_session_transcript(frozen_result):
    h = frozen_result
    before = await h.tasks.get_framework_message_stage(h.task.id, session_id=h.session_id)
    assert before.phase == "runtime_submitted" and "runtime_result_asset" in before.source_manifest
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    save_transcript(h, {"schema_version": 99})
    saved = await h.tasks.claim(h.task.id, worker_id="frozen-transcript", expected_binding=h.binding,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert saved.status == "recovery_required" and saved.attempt_no == 1
    assert await h.tasks.get_framework_message_stage(h.task.id, session_id=h.session_id) == before
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_atomic_transcript_deferral_retains_real_redis_delivery_without_retry(transcript_admission):
    h = transcript_admission
    owner = await h.tasks.claim(h.submitted.id, worker_id="original-transcript", expected_binding=h.binding)
    assert await h.store.renew_turn_execution(h.session_id, owner.lease_token, 0)
    save_transcript(h, changed_transcript(h, "schema_version"))
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.prefix + ":transcript-format", group="transcript-format")
    await queue.connect()
    selected = dispatcher(h)
    received = []

    async def handle(item):
        received.append(item.retry_count)
        await selected.handle_v2(item, worker_id="transcript-format-worker")

    try:
        await queue.enqueue(h.command.model_dump(mode="json"))
        entries, before = await h.redis.xrange(queue.stream), database_state(h)
        assert await RedisTaskWorker(queue, consumer="transcript-format-worker", handler=handle, block_ms=1).run_once() == 0
        assert received == [0] and await h.redis.xrange(queue.stream) == entries
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xlen(queue.stream + ":dead_letter") == 0
        assert database_state(h) == before and h.executions == []
    finally:
        await queue.close()

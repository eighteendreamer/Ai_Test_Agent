from __future__ import annotations

import json
import asyncio
from threading import Event

import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_message_stages import message_stage_table
from src.runtime.task_deferred import TaskDeferred
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_model_reader_admission import database_state, prepare_call, dispatcher, deliver
from tests.test_framework_application_projection import projection
from tests.test_framework_runtime_result_asset import frozen_result
from tests.test_framework_model_admission import submit


@pytest_asyncio.fixture(autouse=True)
async def assert_isolated_checkpoint_schema(isolate_recovery_stores, admission):
    # Fail before any explicit graph fixture opens a saver, not at teardown.
    assert admission.settings.deep_agents.checkpoint_schema == admission.prefix + "_checkpoints"


def change_stage(h, change):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {message_stage_table(h.settings)} WHERE binding_id=%s",
            (h.binding.binding_id,))
        record = cur.fetchone()["record"]
        change(record)
        if record.get("source_manifest") is not None:
            record["source_sha256"] = approval_value_hash(record["source_manifest"])
        cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE binding_id=%s",
            (json.dumps(record), h.binding.binding_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "claimed", "running", "cancelled"])
async def test_future_stage_field_is_deferred_without_changing_original_claim(admission, status, caplog):
    h = admission
    if status == "queued":
        await submit(h)
    else:
        await prepare_call(h, expire=False)
        if status == "running":
            await h.tasks.begin(h.original, resource_id="format-fixture", fencing_token=1)
        if status == "cancelled":
            await h.tasks.finish(h.original, status="cancelled", error_code="test_fixture_cancelled")
        else:
            assert await h.store.renew_turn_execution(h.session_id, h.original.lease_token, 0)
    change_stage(h, lambda raw: raw.update(future_field="private-stage-diagnostic"))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_message_stage_format_unavailable"):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == h.runtime.calls == []
    records = [row for row in caplog.records if row.message in {
        "framework_message_stage_reader_unavailable", "agent_v2_capability_unavailable"}]
    assert len(records) == 1 and records[0].task_id == h.command.task_id
    assert records[0].binding_id == h.binding.binding_id and records[0].worker_id == "format-reader"
    assert records[0].reason == "framework_message_stage_format_unavailable"
    assert records[0].exc_info is None and "private-stage-diagnostic" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("schema_version", 2), ("future_field", "private-stage-diagnostic")])
async def test_future_result_format_is_deferred_before_expired_running_transition(frozen_result, field, value):
    h = frozen_result
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    change_stage(h, lambda raw: raw["source_manifest"]["runtime_result_asset"].update({field: value}))
    before = database_state(h)
    runs, calls = h.native_runs, list(h.runtime.calls)
    with pytest.raises(TaskDeferred, match="framework_message_stage_format_unavailable"):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == []
    assert h.native_runs == runs and h.runtime.calls == calls


@pytest.mark.asyncio
async def test_stage_reader_is_not_conditional_on_model_ledger_injection(admission):
    h = admission
    await prepare_call(h)
    change_stage(h, lambda raw: raw.update(future_field="private-stage-diagnostic"))
    before = database_state(h)
    selected = dispatcher(h)
    selected._framework_model_calls = None
    with pytest.raises(TaskDeferred, match="framework_message_stage_format_unavailable"):
        await deliver(h, selected)
    assert database_state(h) == before and h.executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value,reason", [(None, "framework_message_stage_format_unavailable"),
    ([], "framework_message_stage_unavailable"), ("future-stage", "framework_message_stage_unavailable")])
async def test_nonobject_persisted_stage_defers_without_treating_json_null_as_absent(admission, value, reason):
    h = admission
    await prepare_call(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE binding_id=%s",
            (json.dumps(value), h.binding.binding_id))
    before = database_state(h)
    with pytest.raises(TaskDeferred, match=reason):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == h.runtime.calls == []


@pytest.mark.asyncio
async def test_stage_version_is_rechecked_inside_the_waiting_claim(admission, monkeypatch):
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
            change_stage(h, lambda raw: raw.update(schema_version=30))
            before = database_state(h)
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            await pending
        assert database_state(h) == before and h.executions == []
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_known_result_integrity_error_remains_an_error_without_claim(frozen_result):
    h = frozen_result
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    change_stage(h, lambda raw: raw["source_manifest"]["runtime_result_asset"].update(asset_sha256="0" * 64))
    before = database_state(h)
    with pytest.raises(ValidationError):
        await deliver(h, dispatcher(h))
    assert database_state(h) == before and h.executions == []


@pytest.mark.asyncio
async def test_compatible_result_and_legacy_absent_stage_keep_original_transitions(frozen_result):
    h = frozen_result
    runs, calls = h.native_runs, list(h.runtime.calls)
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    # The format reader does not replace expiry/recovery authority. A valid
    # running record must still enter its original recovery_required state.
    task = await h.tasks.claim(h.task.id, worker_id="compatible-stage",
        expected_binding=h.binding, framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert task.status == "recovery_required" and task.attempt_no == 1
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {message_stage_table(h.settings)} WHERE binding_id=%s", (h.binding.binding_id,))
    before = database_state(h)
    terminal = await h.tasks.claim(h.task.id, worker_id="historical-terminal",
        expected_binding=h.binding, framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert terminal.status == "recovery_required" and database_state(h) == before
    assert h.native_runs == runs and h.runtime.calls == calls

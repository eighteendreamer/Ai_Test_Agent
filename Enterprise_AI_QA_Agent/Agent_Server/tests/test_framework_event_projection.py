"""The original event fact, not just its ID, must survive finalizer retries."""
from __future__ import annotations

from datetime import datetime
import asyncio
import json

import pytest
import pytest_asyncio

from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_application_projection import _hash
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired, message_stage_table
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ExecutionEvent
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_application_projection import projection
from tests.test_framework_runtime_result_asset import frozen_result
from tests.test_framework_snapshot_projection import snapshot_write, stop_after_snapshot
from tests.test_framework_message_stages import run, selected, stage


def event_row(h, identity):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id,type,timestamp,payload FROM {h.settings.database.postgres_event_table} WHERE id=%s",
            (identity,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


@pytest_asyncio.fixture
async def frozen_event_result(projection, monkeypatch):
    """A graph event already streamed to the store before finalization."""
    h = projection
    execute = h.runtime.execute_turn
    h.live_event = ExecutionEvent(id=h.prefix + "-live-event", session_id=h.session_id,
        type="runtime.fixture", timestamp=datetime.utcnow(), payload={"fact": "original"},
        turn_id=h.binding.turn_id, trace_id="original-trace")

    async def execute_with_event(*args, **kwargs):
        result = await execute(*args, **kwargs)
        with bind_request_context(worker_id="streaming-worker", attempt_id="streaming-attempt"):
            await h.store.append_event(h.session_id, h.live_event)
        result.events.append(h.live_event)
        return result

    h.result_values = {}
    async def pause(**kwargs):
        h.result_values.update(kwargs)
        raise ConnectionError("injected pause after event result freeze")

    h.task = await selected(h)
    with monkeypatch.context() as patch:
        patch.setattr(h.runtime, "execute_turn", execute_with_event)
        patch.setattr(h.sessions, "_finalize_runtime_result", pause)
        with pytest.raises(ConnectionError, match="event result freeze"):
            await run(h, h.task)
    h.asset = await FrameworkRuntimeResults(h.tasks).load(h.task.runtime_owner)
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["payload", "type", "timestamp", "context"])
async def test_retry_does_not_accept_changed_event_with_same_id(frozen_result, monkeypatch, changed):
    h = frozen_result
    await stop_after_snapshot(h, monkeypatch)
    identity = h.asset.event_id("snapshot.saved")
    expression = {"payload": "payload || '{\"foreign\":true}'::jsonb",
        "type": "'foreign.event'", "timestamp": "timestamp+interval '1 second'",
        "context": "jsonb_set(payload,'{_harness_context}','{\"worker_id\":\"foreign-worker\"}')"}[changed]
    column = "payload" if changed == "context" else changed
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET {column}={expression} WHERE id=%s",
            (identity,))
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Ee]vent"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_retry_keeps_original_event_context_and_does_not_increment_count(frozen_result, monkeypatch):
    h = frozen_result
    with bind_request_context(worker_id="first-worker", attempt_id="first-attempt"):
        await stop_after_snapshot(h, monkeypatch)
    identity = h.asset.event_id("snapshot.saved")
    original = event_row(h, identity)
    assert original["payload"]["_harness_context"]["worker_id"] == "first-worker"
    count = (await h.store.get_session(h.session_id)).event_count
    receipts = (await stage(h, h.task)).source_manifest["event_projection_commits"]
    with bind_request_context(worker_id="replacement-worker", attempt_id="replacement-attempt"):
        await stop_after_snapshot(h, monkeypatch)
    assert event_row(h, identity) == original
    assert (await h.store.get_session(h.session_id)).event_count == count
    assert (await stage(h, h.task)).source_manifest["event_projection_commits"] == receipts
    assert receipts["event_hashes"][identity] == _hash(original)
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_original_streamed_event_is_collected_without_rewriting_or_recounting(frozen_event_result):
    h = frozen_event_result
    original = event_row(h, h.live_event.id)
    with framework_owner_scope(h.task.runtime_owner), bind_request_context(worker_id="finalizer-worker"):
        await h.sessions._finalize_runtime_result(**h.result_values)
    saved = await stage(h, h.task)
    assert saved.source_manifest["event_projection_commits"]["event_hashes"][h.live_event.id] == _hash(original)
    assert event_row(h, h.live_event.id) == original
    assert len([e for e in await h.store.list_events(h.session_id) if e.id == h.live_event.id]) == 1
    assert saved.schema_version == 26
    assert "post_commit_memory_intent" in saved.source_manifest


@pytest.mark.asyncio
async def test_conflicting_streamed_event_stops_before_snapshot(frozen_event_result):
    h = frozen_event_result
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || '{{\"fact\":\"changed\"}}'::jsonb "
            "WHERE id=%s", (h.live_event.id,))
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="Event projection differs"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert "event_projection_commits" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_event_and_receipt_roll_back_together(frozen_result, monkeypatch):
    import src.runtime.framework_application_projection as commits

    h = frozen_result
    record = commits.record_event_projection
    count = (await h.store.get_session(h.session_id)).event_count
    def rollback(*args, **kwargs):
        record(*args, **kwargs)
        raise ConnectionError("injected event receipt rollback")

    with monkeypatch.context() as patch:
        patch.setattr(commits, "record_event_projection", rollback)
        with framework_owner_scope(h.task.runtime_owner):
            with pytest.raises(ConnectionError, match="event receipt rollback"):
                await h.sessions._finalize_runtime_result(**h.result_values)
    assert event_row(h, h.asset.event_id("snapshot.saved")) is None
    assert "event_projection_commits" not in (await stage(h, h.task)).source_manifest
    assert (await h.store.get_session(h.session_id)).event_count == count
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    assert "application_projection_commit" in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing_row", "missing_receipt", "digest", "binding", "asset", "format"])
async def test_event_receipt_inconsistency_requires_reconciliation(frozen_result, monkeypatch, changed):
    h = frozen_result
    await stop_after_snapshot(h, monkeypatch)
    identity = h.asset.event_id("snapshot.saved")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "missing_row":
            cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s", (identity,))
        else:
            cur.execute(f"SELECT record FROM {message_stage_table(h.settings)} WHERE task_id=%s", (h.task.id,))
            record = cur.fetchone()["record"]
            receipt = record["source_manifest"]["event_projection_commits"]
            if changed == "missing_receipt":
                record["source_manifest"].pop("event_projection_commits")
            elif changed == "format":
                record["schema_version"] = 8
            elif changed == "digest":
                receipt["event_hashes"][identity] = "0" * 64
            else:
                receipt[{"binding": "binding_fingerprint", "asset": "runtime_result_asset_sha256"}[changed]] = "0" * 64
            record["source_sha256"] = approval_value_hash(record["source_manifest"])
            cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE task_id=%s",
                (json.dumps(record), h.task.id))
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Ee]vent"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert len((await h.store.get_session(h.session_id)).messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_source", ["snapshot", "runtime"])
async def test_final_transaction_rechecks_previously_saved_events(frozen_event_result, monkeypatch, identity_source):
    h = frozen_event_result
    save = h.store.save_session_with_events
    identity = h.live_event.id if identity_source == "runtime" else h.asset.event_id("snapshot.saved")
    async def drift(*args, **kwargs):
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || '{{\"drift\":true}}'::jsonb "
                "WHERE id=%s", (identity,))
        return await save(*args, **kwargs)

    monkeypatch.setattr(h.store, "save_session_with_events", drift)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="event projections changed"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert event_row(h, h.asset.event_id("turn.completed")) is None
    saved = await stage(h, h.task)
    assert "application_projection_commit" not in saved.source_manifest
    assert h.asset.event_id("turn.completed") not in saved.source_manifest["event_projection_commits"]["event_hashes"]


@pytest.mark.asyncio
async def test_concurrent_original_event_writes_share_one_receipt(frozen_event_result):
    h = frozen_event_result
    original = event_row(h, h.live_event.id)
    count = (await h.store.get_session(h.session_id)).event_count
    async def append():
        await h.store.append_event(h.session_id, h.live_event, framework_projection=snapshot_write(h))
    await asyncio.gather(*(append() for _ in range(3)))
    assert event_row(h, h.live_event.id) == original
    assert (await h.store.get_session(h.session_id)).event_count == count
    assert (await stage(h, h.task)).source_manifest["event_projection_commits"]["event_hashes"] == {
        h.live_event.id: _hash(original)}


@pytest.mark.asyncio
async def test_expired_owner_cannot_collect_event_receipt(frozen_event_result):
    h = frozen_event_result
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await h.store.append_event(h.session_id, h.live_event, framework_projection=snapshot_write(h))
    assert "event_projection_commits" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_ack_recovery_also_checks_original_runtime_event(frozen_event_result):
    h = frozen_event_result
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    saved = await stage(h, h.task)
    assert h.live_event.id not in saved.source_manifest["application_projection_commit"]["event_hashes"]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET type='changed' WHERE id=%s", (h.live_event.id,))
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    await h.tasks.claim(h.task.id, worker_id="expiry", expected_binding=h.binding)
    assert await h.tasks.ensure_framework_message_recovery(h.task.id) is None
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_legacy_append_keeps_existing_duplicate_semantics(frozen_event_result):
    h = frozen_event_result
    original = event_row(h, h.live_event.id)
    await h.store.append_event(h.session_id, h.live_event.model_copy(update={"type": "legacy-duplicate"}))
    assert event_row(h, h.live_event.id) == original
    assert "event_projection_commits" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["unknown_id", "timestamp", "turn", "runtime_payload"])
async def test_event_writer_rejects_non_original_intent(frozen_event_result, changed):
    from datetime import timedelta

    h = frozen_event_result
    if changed == "runtime_payload":
        event = h.live_event.model_copy(update={"payload": {"fact": "not the original"}})
    else:
        event = ExecutionEvent(id=h.asset.event_id("snapshot.saved"), session_id=h.session_id,
            type="snapshot.saved", timestamp=h.asset.projection_at, payload={"turn_id": h.binding.turn_id})
        if changed == "unknown_id":
            event.id = "not-an-original-event"
        elif changed == "timestamp":
            event.timestamp += timedelta(seconds=1)
        else:
            event.payload["turn_id"] = "not-the-original-turn"
    original = event_row(h, h.live_event.id)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Ee]vent"):
        await h.store.append_event(h.session_id, event, framework_projection=snapshot_write(h))
    assert event_row(h, h.live_event.id) == original
    assert "event_projection_commits" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("same_content", [False, True])
async def test_event_collision_after_intent_check_is_not_silently_accepted(frozen_result, monkeypatch, same_content):
    import src.runtime.framework_application_projection as commits

    h = frozen_result
    prepare = commits.prepare_event_projection
    identity = h.asset.event_id("snapshot.saved")
    def collide(cur, settings, write, event):
        prepared = prepare(cur, settings, write, event)
        cur.execute(f"INSERT INTO {settings.database.postgres_event_table}(id,session_id,type,timestamp,payload) "
            "VALUES (%s,%s,%s,%s,%s::jsonb)", (event.id, h.session_id,
                event.type if same_content else "foreign-event", event.timestamp,
                json.dumps(event.payload) if same_content else "{}"))
        return prepared

    monkeypatch.setattr(commits, "prepare_event_projection", collide)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired,
            match="generated event has no atomic" if same_content else "Event projection differs"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert event_row(h, identity) is None
    assert "event_projection_commits" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("old_version", [6, 7, 8])
async def test_historical_application_receipt_format_still_allows_ack_only(frozen_result, old_version):
    """Historical schema shapes, not a claim of old-binary deployment testing."""
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_framework_message_recovery import outbox

    h = frozen_result
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    record = (await stage(h, h.task)).model_dump(mode="json")
    manifest = record["source_manifest"]
    manifest.pop("post_commit_memory_intent")
    manifest.pop("post_commit_memory_plan")
    manifest.pop("event_projection_commits")
    if old_version < 8:
        manifest.pop("snapshot_projection_commit")
    if old_version < 7:
        manifest.pop("runtime_result_asset")
    proof = manifest.pop("application_projection_commit")
    proof["source_sha256"] = approval_value_hash(manifest)
    manifest["application_projection_commit"] = proof
    record["schema_version"] = old_version
    record["source_sha256"] = approval_value_hash(manifest)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE task_id=%s",
            (json.dumps(record), h.task.id))
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    terminal = await h.tasks.claim(h.task.id, worker_id="expiry", expected_binding=h.binding)
    assert terminal.status == "recovery_required"
    terminal = await h.tasks.get(h.task.id)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("historical-ack", outbox(h, successor.id)["payload"]),
        worker_id="historical-reader")
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert (await stage(h, h.task)).schema_version == old_version
    assert (await stage(h, h.task)).phase == "projected"
    assert await h.tasks.get(h.task.id) == terminal
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1
    assert h.native_runs == 1 and len(h.runtime.calls) == 1

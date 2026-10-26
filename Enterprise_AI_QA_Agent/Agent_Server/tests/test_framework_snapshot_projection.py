"""Snapshot projection must retain its original database row across retries."""
from __future__ import annotations

from datetime import timedelta
import asyncio
import json

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_message_stages import message_stage_table
from src.runtime.framework_application_projection import FrameworkProjectionWrite, _hash
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_application_projection import projection
from tests.test_framework_runtime_result_asset import frozen_result
from tests.test_framework_message_stages import stage


async def stop_after_snapshot(h, monkeypatch):
    async def fail_commit(*args, **kwargs):
        raise ConnectionError("injected crash before final Session transaction")

    with monkeypatch.context() as patch:
        patch.setattr(h.store, "save_session_with_events", fail_commit)
        with framework_owner_scope(h.task.runtime_owner):
            with pytest.raises(ConnectionError, match="before final Session transaction"):
                await h.sessions._finalize_runtime_result(**h.result_values)
    return await h.store.get_latest_snapshot(h.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["graph_state", "version", "created_at"])
async def test_retry_does_not_overwrite_changed_snapshot(frozen_result, monkeypatch, changed):
    h = frozen_result
    snapshot = await stop_after_snapshot(h, monkeypatch)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        expression = {"graph_state": "graph_state || '{\"foreign\":true}'::jsonb",
            "version": "version+10", "created_at": "created_at+interval '1 second'"}[changed]
        cur.execute(f"UPDATE {h.settings.database.postgres_snapshot_table} SET {changed}={expression} WHERE id=%s",
            (snapshot.id,))
    mutated = await h.store.get_latest_snapshot(h.session_id)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="snapshot"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.get_latest_snapshot(h.session_id) == mutated
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_snapshot_event_uses_actual_allocated_version(frozen_result):
    h = frozen_result
    intended = h.asset.runtime_result.snapshot
    # The existing store may legitimately allocate a new version under its
    # Session lock. That is not drift, but the event must describe that row.
    occupied = intended.model_copy(update={"id": intended.id + "-occupied",
        "created_at": intended.created_at - timedelta(seconds=1), "stage": "fixture"})
    await h.store.save_snapshot(h.session_id, occupied, turn_lease_token=h.task.lease_token)
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    saved = await h.store.get_latest_snapshot(h.session_id)
    event, = [e for e in await h.store.list_events(h.session_id) if e.type == "snapshot.saved"]
    assert saved.id == intended.id and saved.version > intended.version
    assert event.payload["snapshot_version"] == saved.version


@pytest.mark.asyncio
async def test_retry_reuses_snapshot_row_and_atomic_receipt(frozen_result, monkeypatch):
    h = frozen_result
    original = await stop_after_snapshot(h, monkeypatch)
    saved_stage = await stage(h, h.task)
    receipt = saved_stage.source_manifest["snapshot_projection_commit"]
    assert receipt["runtime_result_asset_sha256"] == h.asset.asset_sha256
    assert receipt["snapshot_id"] == original.id
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.get_latest_snapshot(h.session_id) == original
    assert (await stage(h, h.task)).source_manifest["snapshot_projection_commit"] == receipt
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "snapshot.saved"]) == 1


def snapshot_write(h):
    return FrameworkProjectionWrite(owner=h.task.runtime_owner,
        snapshot_id=h.asset.runtime_result.snapshot.id,
        snapshot_sha256=_hash(h.asset.runtime_result.snapshot.graph_state),
        output_message_id=h.asset.assistant_message_id,
        runtime_result_asset_sha256=h.asset.asset_sha256)


@pytest.mark.asyncio
async def test_snapshot_and_receipt_roll_back_in_same_transaction(frozen_result, monkeypatch):
    import src.runtime.framework_application_projection as commits

    h = frozen_result
    record = commits.record_snapshot_projection

    def fail_after_receipt(*args, **kwargs):
        record(*args, **kwargs)
        raise ConnectionError("injected snapshot receipt rollback")

    monkeypatch.setattr(commits, "record_snapshot_projection", fail_after_receipt)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(ConnectionError, match="receipt rollback"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert "snapshot_projection_commit" not in (await stage(h, h.task)).source_manifest
    assert (await h.store.get_session(h.session_id)).snapshot_count == 0


@pytest.mark.asyncio
async def test_concurrent_same_snapshot_writes_reuse_one_actual_row(frozen_result):
    h = frozen_result
    async def save():
        return await h.store.save_snapshot(h.session_id, h.asset.runtime_result.restore().snapshot,
            turn_lease_token=h.task.lease_token, framework_projection=snapshot_write(h))

    saved = await asyncio.gather(*(save() for _ in range(3)))
    assert saved[0] == saved[1] == saved[2]
    assert len(await h.store.list_snapshots(h.session_id)) == 1
    receipt = (await stage(h, h.task)).source_manifest["snapshot_projection_commit"]
    assert receipt["snapshot_id"] == saved[0].id


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing_row", "missing_receipt", "unproved_row", "receipt_digest", "stage_format"])
async def test_inconsistent_snapshot_projection_requires_reconciliation(frozen_result, monkeypatch, changed):
    h = frozen_result
    if changed == "unproved_row":
        # A bare row from an older/unrelated writer is not a receipt.
        await h.store.save_snapshot(h.session_id, h.asset.runtime_result.restore().snapshot,
            turn_lease_token=h.task.lease_token)
    else:
        await stop_after_snapshot(h, monkeypatch)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            if changed == "missing_row":
                cur.execute(f"DELETE FROM {h.settings.database.postgres_snapshot_table} WHERE id=%s",
                    (h.asset.runtime_result.snapshot.id,))
            else:
                cur.execute(f"SELECT record FROM {message_stage_table(h.settings)} WHERE task_id=%s", (h.task.id,))
                record = cur.fetchone()["record"]
                if changed == "missing_receipt":
                    record["source_manifest"].pop("snapshot_projection_commit")
                elif changed == "receipt_digest":
                    record["source_manifest"]["snapshot_projection_commit"]["snapshot_sha256"] = "0" * 64
                else:
                    record["schema_version"] = 7
                record["source_sha256"] = approval_value_hash(record["source_manifest"])
                cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE task_id=%s",
                    (json.dumps(record), h.task.id))
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired,
            match="[Ee]vent" if changed == "stage_format" else "[Ss]napshot"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert len((await h.store.get_session(h.session_id)).messages) == 1


@pytest.mark.asyncio
async def test_final_transaction_rechecks_actual_snapshot_header(frozen_result, monkeypatch):
    h = frozen_result
    save = h.store.save_session_with_events

    async def drift_before_commit(*args, **kwargs):
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_snapshot_table} SET version=version+10 WHERE id=%s",
                (h.asset.runtime_result.snapshot.id,))
        return await save(*args, **kwargs)

    monkeypatch.setattr(h.store, "save_session_with_events", drift_before_commit)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="snapshot"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_expired_owner_cannot_create_snapshot_receipt(frozen_result):
    h = frozen_result
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await h.store.save_snapshot(h.session_id, h.asset.runtime_result.restore().snapshot,
            framework_projection=snapshot_write(h))
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert "snapshot_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_version,reader_version", [(10, 9), (9, 8), (8, 7)])
async def test_future_message_stage_is_deferred_before_claim(frozen_result, monkeypatch, stage_version, reader_version):
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred
    from tests.test_framework_message_recovery import outbox

    h = frozen_result
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    terminal = await h.tasks.claim(h.task.id, worker_id="expiry", expected_binding=h.binding)
    assert terminal.status == "recovery_required"
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=jsonb_set(record,'{{schema_version}}',%s::jsonb) "
            "WHERE task_id=%s", (str(stage_version), h.task.id))

    async def forbidden_claim(*args, **kwargs):
        raise AssertionError("Unsupported format must be deferred before claim")

    monkeypatch.setattr(h.tasks, "claim", forbidden_claim)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True,
        message_stage_max_version=reader_version))
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("future-stage", outbox(h, successor.id)["payload"]),
            worker_id="incompatible-worker")
    assert (await h.tasks.get(successor.id)).attempt_no == 0


@pytest.mark.asyncio
async def test_snapshot_appearing_after_intent_check_is_not_overwritten(frozen_result, monkeypatch):
    import src.runtime.framework_application_projection as commits

    h = frozen_result
    prepare = commits.prepare_snapshot_projection

    def competing_insert(cur, settings, write, snapshot):
        stage, existing = prepare(cur, settings, write, snapshot)
        assert existing is None
        # Inject the unique-ID race exactly between the original check and
        # INSERT. It must not turn into an UPDATE of a different row.
        cur.execute(f"INSERT INTO {settings.database.postgres_snapshot_table} "
            "(id,session_id,version,created_at,stage,graph_state) VALUES (%s,%s,50,%s,'foreign','{}'::jsonb)",
            (snapshot.id, h.session_id, snapshot.created_at))
        return stage, existing

    monkeypatch.setattr(commits, "prepare_snapshot_projection", competing_insert)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="snapshot"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    # The injected competing write shares this test transaction: rejection
    # must roll it back as well, not commit a rewritten result or receipt.
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert "snapshot_projection_commit" not in (await stage(h, h.task)).source_manifest

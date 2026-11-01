"""Formal assets use the existing part writers, never a synthetic message-stage."""
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import replace
import asyncio
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.runtime.framework_application_projection import FrameworkProjectionWrite, _hash
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import ExecutionEvent
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_result import projected_case
from tests.test_controlled_case_execution_lease import delivery


def write(p):
    asset = p.result.result_asset
    return FrameworkProjectionWrite(owner=p.h.task.runtime_owner, snapshot_id=asset.runtime_result.snapshot.id,
        snapshot_sha256=_hash(asset.runtime_result.snapshot.graph_state), output_message_id=asset.assistant_message_id,
        runtime_result_asset_sha256=asset.asset_sha256, controlled_case_native=p.result.graph_outcome.native_terminal)


def checkpoint(p):
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        return p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)


async def save_snapshot(p):
    return await p.s.h.store.save_snapshot(p.s.session.id, p.result.result_asset.runtime_result.snapshot,
        turn_lease_token=p.h.task.lease_token, framework_projection=write(p))


@pytest.mark.asyncio
async def test_original_snapshot_writer_retains_formal_part_receipt_in_original_attempt(projected_case):
    p = projected_case
    previous = checkpoint(p)
    snapshot = await save_snapshot(p)
    intended = p.result.result_asset.runtime_result.snapshot
    # Store DTOs deliberately expose UTC-naive headers, while the original
    # intent and TIMESTAMPTZ row remain aware. Verify all three, not just IDs.
    assert snapshot.model_dump(mode="json", exclude={"created_at"}) == intended.model_dump(mode="json", exclude={"created_at"})
    assert snapshot.created_at == ensure_utc_datetime(intended.created_at)
    saved = checkpoint(p)
    assert saved.checkpoint_version == 3 and saved.checkpoint_at == previous.checkpoint_at
    assert saved.checkpoint_payload["admission"] == previous.checkpoint_payload["admission"]
    assert saved.checkpoint_payload["runtime_result_asset"] == previous.checkpoint_payload["runtime_result_asset"]
    receipt = saved.checkpoint_payload["projection_receipts"]["snapshot_projection_commit"]
    assert receipt["snapshot_id"] == snapshot.id
    assert receipt["runtime_result_asset_sha256"] == p.result.result_asset.asset_sha256
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id,session_id,version,created_at,graph_state,stage FROM {p.s.h.settings.database.postgres_snapshot_table} WHERE id=%s", (snapshot.id,))
        row = cur.fetchone()
        assert row["created_at"] == intended.created_at and _hash(dict(row)) == receipt["snapshot_sha256"]
    before = database_digest(p.s)
    assert await save_snapshot(p) == snapshot and database_digest(p.s) == before
    loaded = await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(
        p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert loaded == p.result.result_asset
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        for table in (f"{p.s.h.tasks._tasks}_message_stage", p.s.h.settings.database.postgres_message_table,
                      p.s.d.runs._result_table):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
    assert len(p.requests) == 2 and len(p.effects) == 1
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
    with pytest.raises(ValueError, match="finalizer"):
        await p.s.h.tasks.finish(p.h.task, status="completed", result_status="completed")


@pytest.mark.asyncio
async def test_existing_event_writer_preserves_formal_runtime_and_generated_original_receipts(projected_case):
    p = projected_case
    snapshot = await save_snapshot(p)
    asset = p.result.result_asset
    event = ExecutionEvent(id=asset.event_id("snapshot.saved"), session_id=p.s.session.id,
        timestamp=asset.projection_at, type="snapshot.saved", payload={"turn_id": p.s.binding.turn_id,
            "snapshot_id": snapshot.id, "snapshot_version": snapshot.version, "snapshot_stage": snapshot.stage})
    for item in (asset.runtime_result.events[0], event):
        await p.s.h.store.append_event(p.s.session.id, item, framework_projection=write(p))
    before = database_digest(p.s)
    for item in (asset.runtime_result.events[0], event):
        await p.s.h.store.append_event(p.s.session.id, item, framework_projection=write(p))
    assert database_digest(p.s) == before
    parts = checkpoint(p).checkpoint_payload["projection_receipts"]
    assert set(parts["event_projection_commits"]["event_hashes"]) == {asset.runtime_result.events[0].id, event.id}
    assert parts["snapshot_projection_commit"]["snapshot_id"] == snapshot.id
    assert len([e for e in await p.s.h.store.list_events(p.s.session.id) if e.id == event.id]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["graph_state", "version", "created_at", "missing"])
async def test_formal_snapshot_original_is_not_overwritten_when_part_drifted(projected_case, drift):
    p = projected_case
    snapshot = await save_snapshot(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        table = p.s.h.settings.database.postgres_snapshot_table
        if drift == "missing":
            cur.execute(f"DELETE FROM {table} WHERE id=%s", (snapshot.id,))
        else:
            expression = {"graph_state": "graph_state || '{\"foreign\":true}'::jsonb", "version": "version+10",
                "created_at": "created_at+interval '1 second'"}[drift]
            cur.execute(f"UPDATE {table} SET {drift}={expression} WHERE id=%s", (snapshot.id,))
        cur.execute(f"SELECT to_jsonb(t) AS row FROM {table} t WHERE id=%s", (snapshot.id,))
        before = cur.fetchone()
    with pytest.raises(RuntimeError, match="snapshot"):
        await save_snapshot(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT to_jsonb(t) AS row FROM {table} t WHERE id=%s", (snapshot.id,))
        assert cur.fetchone() == before


@pytest.mark.asyncio
async def test_actual_allocated_snapshot_version_does_not_rewrite_formal_intent(projected_case):
    p = projected_case
    intended = p.result.result_asset.runtime_result.snapshot
    await p.s.h.store.save_snapshot(p.s.session.id, intended.model_copy(update={"id": "occupied-version", "stage": "fixture"}),
        turn_lease_token=p.h.task.lease_token)
    saved = await save_snapshot(p)
    assert saved.version > intended.version and saved.id == intended.id
    assert (await save_snapshot(p)) == saved
    assert p.result.result_asset.runtime_result.snapshot.version == intended.version


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "ack", "cancel_waiter"])
async def test_formal_snapshot_row_and_receipt_have_the_same_real_commit(projected_case, monkeypatch, caplog, failure):
    import src.runtime.framework_application_projection as commits

    p = projected_case
    record, save = commits.record_snapshot_projection, p.s.h.store._save_snapshot_sync
    entered, release, finished = Event(), Event(), Event()
    def record_part(*args, **kwargs):
        value = record(*args, **kwargs)
        if failure == "rollback":
            raise RuntimeError("isolated snapshot transaction failure")
        if failure == "cancel_waiter":
            entered.set()
            assert release.wait(30), "snapshot transaction not released"
        return value
    def save_part(*args, **kwargs):
        try:
            value = save(*args, **kwargs)
            if failure == "ack":
                raise RuntimeError("isolated committed snapshot ACK loss")
            return value
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(commits, "record_snapshot_projection", record_part)
        patch.setattr(p.s.h.store, "_save_snapshot_sync", save_part)
        operation = asyncio.create_task(save_snapshot(p))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="isolated"):
                await operation
    if failure == "cancel_waiter":
        assert any(getattr(record, "recovery_reason", None) == "snapshot_write_receipt_unknown"
            and getattr(record, "automatic_replay", None) is False for record in caplog.records)
    assert checkpoint(p).checkpoint_version == (2 if failure == "rollback" else 3)
    assert (await p.s.h.store.get_latest_snapshot(p.s.session.id) is not None) == (failure != "rollback")
    snapshot = await save_snapshot(p)
    assert await save_snapshot(p) == snapshot
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["native", "job", "ledger", "runtime_event", "receipt", "lease", "owner", "missing_native"])
async def test_formal_part_writes_still_require_all_originals_and_live_dual_owner(projected_case, drift):
    from src.runtime.controlled_case_results import asset_event_id

    p = projected_case
    captured = deepcopy(write(p))
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if drift == "native":
            captured.controlled_case_native.values["messages"][-1].content = "foreign result"
        elif drift == "job":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_tool_job_table} SET summary='foreign' WHERE session_id=%s", (p.s.session.id,))
        elif drift == "ledger":
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"') WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif drift == "runtime_event":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_event_table} SET payload=payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s",
                (p.result.runtime_result.events[0].id,))
        elif drift == "receipt":
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_event_table} WHERE id=%s", (asset_event_id(p.s.binding),))
        elif drift == "lease":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif drift == "owner":
            captured = replace(captured, owner=captured.owner.model_copy(update={"controlled_case_lease": None}))
        else:
            captured = replace(captured, controlled_case_native=None)
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        await p.s.h.store.save_snapshot(p.s.session.id, p.result.result_asset.runtime_result.snapshot, framework_projection=captured)
    assert database_digest(p.s) == before and checkpoint(p).checkpoint_version == 2
    assert await p.s.h.store.get_latest_snapshot(p.s.session.id) is None


@pytest.mark.asyncio
async def test_concurrent_formal_part_writers_do_not_lose_other_receipts(projected_case):
    p = projected_case
    await asyncio.gather(save_snapshot(p), p.s.h.store.append_event(p.s.session.id,
        p.result.result_asset.runtime_result.events[0], framework_projection=write(p)))
    parts = checkpoint(p).checkpoint_payload["projection_receipts"]
    assert parts["snapshot_projection_commit"] is not None and parts["event_projection_commits"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["old_reader", "extra_parts", "future_version"])
async def test_projection_checkpoint_compatibility_defers_before_any_reclaim_mutation(projected_case, change):
    p = projected_case
    await save_snapshot(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        if change == "extra_parts":
            raw = deepcopy(attempt.checkpoint_payload)
            raw["projection_receipts"]["future_part"] = {}
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": raw}))
        elif change == "future_version":
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_version": 4}))
    before = database_digest(p.s)
    with pytest.raises(TaskDeferred, match="case_checkpoint_format"):
        await p.s.h.tasks.claim_controlled_case(delivery(p.s), worker_id="incompatible-part-reader",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True,
                controlled_case_checkpoint_max_version=2 if change == "old_reader" else 3))
    assert database_digest(p.s) == before


@asynccontextmanager
async def observation_services(p):
    from src.application.context.memory_runtime_service import MemoryRuntimeService
    from src.application.context.observation_runtime_service import ObservationRuntimeService
    from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore

    settings = p.s.h.settings
    settings.database.postgres_memory_table = p.s.h.prefix + "_case_observations"
    store = PostgresVectorMemoryStore(settings)
    await store.initialize()
    memories, observations = MemoryRuntimeService(store), ObservationRuntimeService()
    def build(original):
        state = original.runtime_result.state
        items = observations.build_tool_observations(session_id=p.s.session.id, turn_id=p.s.binding.turn_id,
            trace_id=state["trace_id"], tool_results=state["tool_results"], context_bundle=state["context_bundle"])
        return items, memories.build_observation_requests(items)
    try:
        yield store, memories, build
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {settings.database.postgres_memory_table}")


@pytest.mark.asyncio
async def test_existing_observation_writer_reuses_formal_asset_and_original_pg_rows(projected_case):
    from src.runtime.framework_observation_projection import FrameworkObservationProjections

    p = projected_case
    async with observation_services(p) as (store, memories, build):
        parts = FrameworkObservationProjections(p.s.h.tasks)
        asset = await parts.prepare(write(p), build)
        assert len(asset.observations) == 1
        def forbidden(*args):
            raise AssertionError("original formal observations must not be rebuilt")
        assert await parts.prepare(write(p), forbidden) == asset
        kwargs = dict(turn_lease_token=p.h.task.lease_token, framework_projection=write(p), prepared_requests=asset.write_requests)
        identities = await memories.write_observations(asset.observations, **kwargs)
        assert identities == [asset.observations[0].id]
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT to_jsonb(t) AS row FROM {p.s.h.settings.database.postgres_memory_table} t ORDER BY id")
            original = cur.fetchall()
        assert await memories.write_observations(asset.observations, **kwargs) == identities
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT to_jsonb(t) AS row FROM {p.s.h.settings.database.postgres_memory_table} t ORDER BY id")
            assert cur.fetchall() == original
        receipt = checkpoint(p).checkpoint_payload["projection_receipts"]["observation_projection_commits"]
        assert set(receipt["row_hashes"]) == set(identities)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["payload", "time", "missing", "bare_generated"])
async def test_formal_generated_event_drift_does_not_get_promoted_or_overwritten(projected_case, drift):
    p = projected_case
    asset = p.result.result_asset
    event = ExecutionEvent(id=asset.event_id("snapshot.saved"), session_id=p.s.session.id,
        timestamp=asset.projection_at, type="snapshot.saved", payload={"turn_id": p.s.binding.turn_id})
    if drift == "bare_generated":
        await p.s.h.store.append_event(p.s.session.id, event, turn_lease_token=p.h.task.lease_token)
    else:
        await p.s.h.store.append_event(p.s.session.id, event, framework_projection=write(p))
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            table = p.s.h.settings.database.postgres_event_table
            if drift == "missing":
                cur.execute(f"DELETE FROM {table} WHERE id=%s", (event.id,))
            elif drift == "payload":
                cur.execute(f"UPDATE {table} SET payload=payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (event.id,))
            else:
                cur.execute(f"UPDATE {table} SET timestamp=timestamp+interval '1 second' WHERE id=%s", (event.id,))
    before = database_digest(p.s)
    with pytest.raises(RuntimeError, match="event|Event"):
        await p.s.h.store.append_event(p.s.session.id, event, framework_projection=write(p))
    assert database_digest(p.s) == before


@pytest.mark.asyncio
async def test_dual_lease_is_rechecked_after_snapshot_insert_and_before_receipt_commit(projected_case, monkeypatch):
    import src.runtime.framework_application_projection as commits

    p = projected_case
    record = commits.record_snapshot_projection
    def expire_after_insert(cur, settings, *args):
        cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        return record(cur, settings, *args)
    before = database_digest(p.s)
    monkeypatch.setattr(commits, "record_snapshot_projection", expire_after_insert)
    with pytest.raises(RuntimeError):
        await save_snapshot(p)
    assert database_digest(p.s) == before and await p.s.h.store.get_latest_snapshot(p.s.session.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["content", "missing", "bare_row"])
async def test_formal_observation_row_drift_is_not_repaired_or_adopted(projected_case, drift):
    from src.runtime.framework_observation_projection import FrameworkObservationProjections

    p = projected_case
    async with observation_services(p) as (store, memories, build):
        asset = await FrameworkObservationProjections(p.s.h.tasks).prepare(write(p), build)
        kwargs = dict(turn_lease_token=p.h.task.lease_token, framework_projection=write(p), prepared_requests=asset.write_requests)
        if drift == "bare_row":
            await store.write(asset.write_requests[0])
        else:
            await memories.write_observations(asset.observations, **kwargs)
            with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
                table = p.s.h.settings.database.postgres_memory_table
                if drift == "missing":
                    cur.execute(f"DELETE FROM {table} WHERE id=%s", (asset.observations[0].id,))
                else:
                    cur.execute(f"UPDATE {table} SET content='foreign' WHERE id=%s", (asset.observations[0].id,))
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT to_jsonb(t) AS row FROM {p.s.h.settings.database.postgres_memory_table} t ORDER BY id")
            before = cur.fetchall()
        attempt = checkpoint(p)
        with pytest.raises(RuntimeError, match="[Oo]bservation"):
            await memories.write_observations(asset.observations, **kwargs)
        assert checkpoint(p) == attempt
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT to_jsonb(t) AS row FROM {p.s.h.settings.database.postgres_memory_table} t ORDER BY id")
            assert cur.fetchall() == before


@pytest.mark.asyncio
async def test_formal_observation_row_and_receipt_roll_back_together(projected_case, monkeypatch):
    import src.runtime.framework_observation_projection as parts

    p = projected_case
    async with observation_services(p) as (store, memories, build):
        asset = await parts.FrameworkObservationProjections(p.s.h.tasks).prepare(write(p), build)
        kwargs = dict(turn_lease_token=p.h.task.lease_token, framework_projection=write(p), prepared_requests=asset.write_requests)
        record = parts.record_memory_projection
        def fail(*args, **kwargs):
            record(*args, **kwargs)
            raise RuntimeError("isolated observation receipt rollback")
        attempt = checkpoint(p)
        with monkeypatch.context() as patch:
            patch.setattr(parts, "record_memory_projection", fail)
            with pytest.raises(RuntimeError, match="isolated observation"):
                await memories.write_observations(asset.observations, **kwargs)
        assert checkpoint(p) == attempt
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {p.s.h.settings.database.postgres_memory_table}")
            assert cur.fetchone()["n"] == 0
        assert await memories.write_observations(asset.observations, **kwargs) == [asset.observations[0].id]

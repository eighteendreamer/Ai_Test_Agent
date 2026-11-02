"""An admitted case expiry preserves its original attempt for scoped recovery."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.run_management import router
from src.application.test_runs.run_service import TestRunService as RunService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.postgres_runtime_owner import assert_runtime_owner
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.controlled_case_admission import CONTROLLED_CASE_ADMISSION_KEY
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, submit, database_digest
from tests.test_controlled_case_execution_lease import claim, delivery
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_projection_parts import checkpoint
from tests.test_controlled_case_settlement import implementation
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_task_input import source as memory_case


def expire(s, now, *, item_id=None, status=None, version=None):
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        item = s.d.runs._lock_item(cur, item_id or s.item.id)
        changes = {"lease_expires_at": now - timedelta(seconds=1)}
        if status is not None:
            changes["status"] = status
        s.d.runs._write_item(cur, item.model_copy(update=changes))
        if version is not None or status is not None:
            attempt = s.d.runs._lock_attempt(cur, item.id, item.lease_token)
            changes = {}
            if version is not None:
                # Raw persisted formats, not a claim that this is a valid
                # admission/settlement of the indicated version. No reaper
                # may parse, downgrade or discard this reserved original.
                changes.update(checkpoint_version=version, checkpoint_payload={
                    "schema_version": version, "unrecognized_original": {"retain": [None, 7, "fixture"]}})
            if status is not None:
                changes["status"] = "claimed" if status == "claimed" else "running"
            s.d.runs._write_attempt(cur, attempt.model_copy(update=changes))


async def recover(store, run_id, now, entry):
    if entry == "all":
        return await store.recover_all_expired(now)
    return await store.recover_expired(run_id, now)


@pytest.mark.asyncio
async def test_generic_run_reaper_cannot_requeue_an_admitted_case_or_replace_its_attempt(formal_case, caplog):
    s = formal_case
    await submit(s)
    now = datetime.now(timezone.utc)
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        item = s.d.runs._lock_item(cur, s.item.id)
        s.d.runs._write_item(cur, item.model_copy(update={"lease_expires_at": now - timedelta(seconds=1)}))
    before = database_digest(s)
    assert await s.d.runs.recover_expired(s.item.run_id, now) == 0
    assert database_digest(s) == before
    claimed = await s.d.runs.claim_items(run_id=s.item.run_id, worker_id="cannot-replay-admitted-case",
        limit=20, lease_seconds=120, now=now)
    assert all(item.id != s.item.id for item, _ in claimed)
    assert database_digest(s) == before
    assert any(r.message == "controlled_case_expiry_requires_reconciliation"
        and r.recovery_reason == "controlled_case_original_attempt_preserved" for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 99])
@pytest.mark.parametrize("entry", ["run", "all"])
async def test_postgres_reaper_preserves_every_reserved_format_without_interpreting_it(formal_case, version, entry):
    s = formal_case
    await submit(s)
    now = datetime.now(timezone.utc)
    expire(s, now, version=version)
    before = database_digest(s)
    assert await recover(s.d.runs, s.item.run_id, now, entry) == 0
    assert await recover(s.d.runs, s.item.run_id, now, entry) == 0
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["claimed", "running", "waiting_resource"])
async def test_postgres_reaper_cannot_invalidate_reserved_identity_in_any_reclaimable_state(formal_case, status):
    s = formal_case
    await submit(s)
    now = datetime.now(timezone.utc)
    expire(s, now, status=status)
    before = database_digest(s)
    assert await s.d.runs.recover_expired(s.item.run_id, now) == 0
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_concurrent_reapers_recover_only_the_ordinary_sibling_and_never_the_reserved_case(formal_case):
    s = formal_case
    await submit(s)
    sibling = next(item for item, _ in s.claims if item.id != s.item.id)
    now = datetime.now(timezone.utc)
    expire(s, now)
    expire(s, now, item_id=sibling.id)
    original = await s.d.runs.get_item(s.item.id)
    attempt = await s.d.runs.get_latest_attempt(s.item.id)
    recovered = await asyncio.gather(*(recover(s.d.runs, s.item.run_id, now, entry)
        for entry in ["run", "all", "run", "all"]))
    assert sum(recovered) == 1
    assert await s.d.runs.get_item(s.item.id) == original
    assert await s.d.runs.get_latest_attempt(s.item.id) == attempt
    queued = await s.d.runs.get_item(sibling.id)
    expired = await s.d.runs.get_latest_attempt(sibling.id)
    assert queued.status == "queued" and queued.lease_token is None
    assert expired.status == "expired" and expired.completed_at == now
    claims = await s.d.runs.claim_items(run_id=s.item.run_id, worker_id="ordinary-replacement",
        limit=20, lease_seconds=120, now=now)
    assert [item.id for item, _ in claims] == [sibling.id]
    assert claims[0][1].recovered_from_attempt_id == expired.id
    assert await s.d.runs.get_item(s.item.id) == original
    assert await s.d.runs.get_latest_attempt(s.item.id) == attempt
    detail = await s.d.runs.get_run(s.item.run_id)
    assert len([a for a in detail.attempts if a.run_item_id == s.item.id]) == 1
    assert detail.run.stats.running == 1 and detail.run.stats.claimed == len(s.claims) - 1


@pytest.mark.asyncio
async def test_reaper_and_claim_race_preserve_the_original_and_do_not_restore_expired_owner_authority(formal_case):
    s = formal_case
    await submit(s)
    task = await claim(s)
    await s.h.tasks.begin(task, resource_id="case-expiry-test", fencing_token=1)
    now = datetime.now(timezone.utc)
    expire(s, now)
    before = database_digest(s)
    results = await asyncio.gather(
        s.d.runs.recover_expired(s.item.run_id, now),
        s.d.runs.recover_all_expired(now),
        s.d.runs.claim_items(run_id=s.item.run_id, worker_id="cannot-replay", limit=20, lease_seconds=120, now=now))
    assert results == [0, 0, []]
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        with pytest.raises(ContinuationLeaseLostError):
            assert_runtime_owner(cur, s.h.settings, task.runtime_owner)
    assert not await s.h.tasks.renew_framework_execution(task, 90)
    with pytest.raises((ValueError, ContinuationLeaseLostError)):
        await s.d.runs.heartbeat_item(s.item.id, s.item.lease_token, 90, now)
    assert database_digest(s) == before and s.h.runtime.calls == []
    # The existing task expiry classification remains usable once its Session
    # lease also expires; retaining the RunItem never grants replay authority.
    assert await s.h.store.renew_turn_execution(s.session.id, task.lease_token, 0)
    observed = await claim(s)
    assert observed.status == "recovery_required" and observed.error_code == "execution_owner_expired"
    assert (observed.id, observed.attempt_id, observed.attempt_no) == (task.id, task.attempt_id, 1)
    after = database_digest(s)
    assert (await claim(s)).status == "recovery_required"
    assert database_digest(s) == after and s.h.runtime.calls == []


@pytest.mark.asyncio
async def test_public_recovery_returns_only_the_requeued_count_without_claiming_case_recovery(formal_case):
    s = formal_case
    await submit(s)
    now = datetime.now(timezone.utc)
    expire(s, now)
    before = database_digest(s)
    service = RunService(store=s.d.runs, project_service=None, suite_service=None, test_case_service=None,
        clock=lambda: now)
    app = FastAPI()
    app.state.test_run_service = service
    app.include_router(router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/runs/{s.item.run_id}/recover-expired")
    assert response.status_code == 200 and response.json() == {"recovered_count": 0}
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_real_assessment_expiry_preserves_all_originals_but_does_not_create_settlement_permission(assessment_case):
    p = assessment_case
    asset = await freeze(p)
    now = datetime.now(timezone.utc)
    expire(p.s, now)
    before = originals_digest(p)
    assert await p.s.d.runs.recover_all_expired(now) == 0
    assert checkpoint(p).checkpoint_version == 5 and checkpoint(p).checkpoint_payload["assessment"] == asset.model_dump(mode="json")
    with pytest.raises(RuntimeError, match="settlement is missing"):
        await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["item", "attempt"])
async def test_ordinary_sibling_recovery_failure_rolls_back_without_changing_reserved_originals(formal_case, monkeypatch, failure):
    s = formal_case
    await submit(s)
    sibling = next(item for item, _ in s.claims if item.id != s.item.id)
    now = datetime.now(timezone.utc)
    expire(s, now)
    expire(s, now, item_id=sibling.id)
    before = database_digest(s)
    name = "_write_item" if failure == "item" else "_write_attempt"
    original = getattr(s.d.runs, name)
    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("isolated generic expiry transaction rollback")
    monkeypatch.setattr(s.d.runs, name, fail_after_write)
    with pytest.raises(RuntimeError, match="expiry transaction rollback"):
        await s.d.runs.recover_expired(s.item.run_id, now)
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 99])
@pytest.mark.parametrize("status", ["claimed", "running", "waiting_resource"])
@pytest.mark.parametrize("entry", ["run", "all"])
async def test_memory_reaper_preserves_raw_reserved_originals_and_all_timing_fields(memory_case, version, status, entry):
    s = memory_case
    now = s.now
    s.store._items[s.item.id] = s.item.model_copy(update={
        "status": status, "lease_expires_at": now - timedelta(seconds=1)})
    s.store._attempts[s.attempt.id] = s.attempt.model_copy(update={
        "status": "claimed" if status == "claimed" else "running",
        "checkpoint_key": CONTROLLED_CASE_ADMISSION_KEY, "checkpoint_version": version,
        "checkpoint_payload": {"schema_version": version, "unrecognized_original": [None, 7, "fixture"]}})
    before = await s.store.get_run(s.item.run_id)
    assert await recover(s.store, s.item.run_id, now, entry) == 0
    assert await recover(s.store, s.item.run_id, now, entry) == 0
    assert await s.store.get_run(s.item.run_id) == before
    assert await s.store.claim_items(run_id=s.item.run_id, worker_id="cannot-replay",
        limit=20, lease_seconds=120, now=now) == []
    assert await s.store.get_item(s.item.id) == next(item for item in before.items if item.id == s.item.id)
    assert await s.store.get_latest_attempt(s.item.id) == next(a for a in before.attempts if a.id == s.attempt.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "agent_case_execution", "controlled_case_admission_future", "xcontrolled_case_admission"])
@pytest.mark.parametrize("entry", ["run", "all"])
async def test_memory_reaper_keeps_the_original_generic_recovery_for_nonreserved_keys(memory_case, key, entry):
    s = memory_case
    now = s.now
    original = s.attempt.model_copy(update={"checkpoint_key": key, "checkpoint_version": 7,
        "checkpoint_payload": {"unrecognized_original": "legacy-checkpoint"}})
    s.store._attempts[s.attempt.id] = original
    s.store._items[s.item.id] = s.item.model_copy(update={"lease_expires_at": now - timedelta(seconds=1)})
    assert await recover(s.store, s.item.run_id, now, entry) == 1
    expired = await s.store.get_latest_attempt(s.item.id)
    assert expired.status == "expired" and expired.checkpoint_payload == original.checkpoint_payload
    [(item, attempt)] = await s.store.claim_items(run_id=s.item.run_id, worker_id="ordinary-replacement",
        limit=20, lease_seconds=120, now=now)
    assert item.id == s.item.id and item.attempt_no == attempt.attempt_no == s.attempt.attempt_no + 1
    assert attempt.recovered_from_attempt_id == s.attempt.id and attempt.id != s.attempt.id
    assert (attempt.checkpoint_key, attempt.checkpoint_version, attempt.checkpoint_payload) == (
        original.checkpoint_key, original.checkpoint_version, original.checkpoint_payload)

"""Generic approval maintenance cannot acquire a parked formal execution."""
from datetime import timedelta
import logging

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.test_runs.run_service import TestRunService as RunService
from src.application.test_runs.run_store import InMemoryTestRunStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import InMemorySessionStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.controlled_case_admission import CONTROLLED_CASE_ADMISSION_KEY
from src.schemas.session import ToolApprovalRequest, ToolApprovalStatus
from src.schemas.tool_job import ToolJobRecord, ToolJobStatus
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_approval_wait import park, facts, clock, implementation
from tests.test_controlled_case_execution_lease import delivery, capabilities
from tests.test_controlled_case_pending_approvals import pending_formal
from tests.test_controlled_case_task_input import source as memory_case
from tests.test_test_run_duration_transitions import _new_running_item
from tests.test_test_run_regression import _seed_waiting_approval


def service(store, *, sessions=None, jobs=None):
    return RunService(store=store, project_service=None, suite_service=None,
        test_case_service=None, session_store=sessions, tool_job_service=jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["waiting_approval", "claimed"])
@pytest.mark.parametrize("version", [1, 999])
async def test_generic_pg_resume_preserves_reserved_original_even_for_old_claimed_sample(
        pending_formal, state, version, caplog):
    p = pending_formal
    receipt = await park(p)
    store = p.s.d.runs
    now = clock(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        item = store._lock_item(cur, p.s.item.id)
        attempt = store._lock_attempt(cur, item.id, item.lease_token)
        # Retain the exact old generic-resume failure shape for its idempotent
        # branch. Unknown reserved versions are not permission to downgrade.
        if state == "claimed":
            store._write_item(cur, item.model_copy(update={"status": state,
                "lease_expires_at": now + timedelta(seconds=90)}))
        payload = dict(attempt.checkpoint_payload, schema_version=version)
        store._write_attempt(cur, attempt.model_copy(update={"status": state,
            "checkpoint_payload": payload, "checkpoint_version": version}))
    before = facts(p)
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError, match="dedicated approval continuation"):
        await service(store).resume_waiting_approval(p.s.item.id, item.approval_id)
    assert facts(p) == before
    assert "controlled_case_approval_continuation_required" in caplog.text
    assert "test_run_item_approval_resume_requested" not in caplog.text
    if version == 1 and state == "waiting_approval":
        assert await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=capabilities()) == receipt
        assert facts(p) == before
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["waiting_approval", "claimed"])
@pytest.mark.parametrize("version", [1, 999])
async def test_generic_memory_resume_has_the_same_reserved_namespace_contract(memory_case, state, version):
    s = memory_case
    waiting = await s.store.mark_waiting_approval(s.item.id, s.item.lease_token,
        "reserved-approval", "reserved-job", s.now)
    # Memory store has no formal admission writer. This is an explicit
    # reserved-namespace compatibility sample, not a claimed real admission.
    attempt = s.store._attempts[s.attempt.id]
    s.store._attempts[attempt.id] = attempt.model_copy(update={"checkpoint_key": CONTROLLED_CASE_ADMISSION_KEY,
        "checkpoint_payload": {"schema_version": version}, "checkpoint_version": version, "status": state})
    if state == "claimed":
        s.store._items[waiting.id] = waiting.model_copy(update={"status": state,
            "lease_expires_at": s.now + timedelta(seconds=90)})
    before = await s.store.get_run(s.detail.run.id)
    with pytest.raises(ValueError, match="dedicated approval continuation"):
        await s.store.resume_waiting_approval(waiting.id, waiting.approval_id, 90, s.now)
    assert await s.store.get_run(s.detail.run.id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "agent_case_execution", "future_unrelated_checkpoint"])
async def test_unreserved_legacy_resume_keeps_its_original_attempt_and_idempotence(memory_case, key):
    s = memory_case
    if key is not None:
        await s.store.save_checkpoint(s.item.id, s.item.lease_token, key, {"schema_version": 999}, 1, s.now)
    waiting = await s.store.mark_waiting_approval(s.item.id, s.item.lease_token,
        "legacy-approval", "legacy-job", s.now)
    first = await s.store.resume_waiting_approval(waiting.id, waiting.approval_id, 90, s.now)
    before = await s.store.get_run(s.detail.run.id)
    assert await s.store.resume_waiting_approval(waiting.id, waiting.approval_id, 90, s.now) == first
    assert await s.store.get_run(s.detail.run.id) == before
    attempt = await s.store.get_latest_attempt(waiting.id)
    assert first.status == attempt.status == "claimed"
    assert attempt.id == s.attempt.id and first.lease_token == s.attempt.lease_token


@pytest.mark.asyncio
async def test_generic_denied_reconciler_preserves_formal_job_before_any_result_write(pending_formal, caplog):
    p = pending_formal
    await park(p)
    store = p.s.d.runs
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        # A run may retain its original Session. Populate both representations
        # so this exercises the existing generic restart scan, not a stub scan.
        run = store._read_run(cur, p.s.detail.run.id).model_copy(update={"session_id": p.s.session.id})
        store._write_run(cur, run)
        cur.execute(f"UPDATE {store._run_table} SET session_id=%s WHERE id=%s", (p.s.session.id, run.id))
    item = await store.get_item(p.s.item.id)
    approval = next(a for a in await p.h.store.list_approvals(p.s.session.id) if a.id == item.approval_id)
    approval.status = ToolApprovalStatus.denied
    approval.decision_note = "Retained human denial; no native delivery proof."
    await p.h.store.save_approval(p.s.session.id, approval)
    before = facts(p)
    caplog.set_level(logging.INFO)
    await service(store, sessions=p.h.store, jobs=p.bridge.jobs)._reconcile_denied_approvals()
    assert facts(p) == before
    assert "test_run_denied_approval_dedicated_continuation_required" in caplog.text
    assert (await p.bridge.jobs.get_job(item.tool_job_id)).status.value == "waiting_approval"
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 999])
async def test_reconciliation_mixed_batch_keeps_legacy_denial_and_reserved_attempt_separate(version):
    store, sessions = InMemoryTestRunStore(), InMemorySessionStore()
    jobs = ToolJobService(InMemoryToolJobStore())
    legacy_run, legacy_item = await _seed_waiting_approval(store)
    now = legacy_run.created_at
    formal_run_id, formal_item_id, original = await _new_running_item(store, now)
    store._runs[formal_run_id] = store._runs[formal_run_id].model_copy(update={"session_id": legacy_run.session_id})
    await store.mark_waiting_approval(formal_item_id, original.lease_token, "formal-denial", "formal-job", now)
    store._attempts[original.id] = store._attempts[original.id].model_copy(update={
        "checkpoint_key": CONTROLLED_CASE_ADMISSION_KEY,
        "checkpoint_version": version, "checkpoint_payload": {"schema_version": version}})
    before = await store.get_run(formal_run_id)
    for approval_id, job_id in [(legacy_item.approval_id, legacy_item.tool_job_id), ("formal-denial", "formal-job")]:
        # Both may have this legacy source label. Only the immutable reserved
        # Attempt key delegates ownership; metadata alone must not block.
        await sessions.save_approval(legacy_run.session_id, ToolApprovalRequest(id=approval_id,
            session_id=legacy_run.session_id, tool_key="fixture", tool_name="Fixture", reason="approval required",
            status=ToolApprovalStatus.denied, created_at=now, metadata={"source": "test_run_agent_execution"}))
        await jobs._store.save_job(ToolJobRecord(id=job_id, session_id=legacy_run.session_id,
            turn_id="legacy-turn", trace_id="fixture-trace", call_id=approval_id,
            tool_key="fixture", tool_name="Fixture", status=ToolJobStatus.waiting_approval,
            created_at=now, updated_at=now))
    runner = service(store, sessions=sessions, jobs=jobs)
    await runner._reconcile_denied_approvals()
    assert await store.get_run(formal_run_id) == before
    assert (await jobs.get_job("formal-job")).status == ToolJobStatus.waiting_approval
    legacy = await store.get_run(legacy_run.id)
    assert legacy.items[0].status == legacy.attempts[0].status == "blocked"
    assert len(legacy.results) == 1 and (await jobs.get_job(legacy_item.tool_job_id)).status == ToolJobStatus.denied
    await runner._reconcile_denied_approvals()
    assert await store.get_run(formal_run_id) == before and await store.get_run(legacy_run.id) == legacy
    assert (await jobs.get_job("formal-job")).status == ToolJobStatus.waiting_approval

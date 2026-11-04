"""Real admitted case leases; no graph, provider or domain execution."""
from __future__ import annotations

import json
import asyncio
from datetime import datetime, timedelta, timezone
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import (
    formal_case, pg_domain, prepare_domain, submit, database_digest,
    wait_for_session_sql_lock,
)


def delivery(s):
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {s.h.settings.database.postgres_task_outbox_table}")
        return cur.fetchone()["payload"]


def capabilities():
    return FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True)


async def claim(s, raw=None):
    return await s.h.tasks.claim_controlled_case(raw or delivery(s), worker_id="case-worker",
        framework_capabilities=capabilities())


@pytest.mark.parametrize("rejection", ["expired", "equal", "missing", "status", "token"])
def test_case_lease_rejection_renders_same_original_clock_without_capabilities(caplog, rejection):
    from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT
    from src.runtime.controlled_case_execution import require_case_live
    from src.application.test_runs.run_store import PostgresTestRunStore

    now = datetime.fromisoformat("2026-11-04T06:16:27.800195+00:00")
    expiry = now - timedelta(seconds=1) if rejection == "expired" else (
        None if rejection == "missing" else now if rejection == "equal" else now + timedelta(seconds=1))
    private = "fixture-private-case-lease-capability"
    item = SimpleNamespace(id="item", status="queued" if rejection == "status" else "running",
        lease_token="other" if rejection == "token" else private, lease_expires_at=expiry,
        started_at=None if rejection == "missing" else now-timedelta(seconds=119),
        heartbeat_at=None if rejection == "missing" else now-timedelta(seconds=120))
    locked = SimpleNamespace(store=PostgresTestRunStore, item=item, attempt=SimpleNamespace(id="execution"),
        lease=SimpleNamespace(run_id="run", lease_token=private), active_states=("running",))
    before = vars(item).copy()
    with pytest.raises(ContinuationLeaseLostError, match="original RunItem lease"):
        require_case_live(locked, now)
    record, = [record for record in caplog.records if record.message == "controlled_case_execution_lease_rejected"]
    ContextFilter().filter(record)
    diagnostic = json.loads(ContextFormatter(LOG_FORMAT).format(record).split(" diagnostics=", 1)[1])
    assert diagnostic == dict(execution_id="execution", recovery_reason="original_run_item_lease_inactive",
        authority_observed_at=now.isoformat(), case_lease_expires_at=expiry.isoformat() if expiry else None,
        case_lease_remaining_seconds=(expiry-now).total_seconds() if expiry else None,
        case_lease_started_at=item.started_at.isoformat() if item.started_at else None,
        case_heartbeat_at=item.heartbeat_at.isoformat() if item.heartbeat_at else None,
        case_item_status=item.status, case_expected_states="running", automatic_replay=False)
    assert vars(item) == before and record.exc_info is None and private not in repr(record.__dict__)


def test_case_live_check_does_not_log_rejection_or_rewrite_lease(caplog):
    from src.runtime.controlled_case_execution import require_case_live
    from src.application.test_runs.run_store import PostgresTestRunStore

    now = datetime.now(timezone.utc)
    item = SimpleNamespace(id="item", status="running", lease_token="capability", lease_expires_at=now+timedelta(seconds=1))
    locked = SimpleNamespace(store=PostgresTestRunStore, item=item,
        lease=SimpleNamespace(lease_token="capability"), active_states=("running",))
    before = vars(item).copy()
    assert require_case_live(locked, now) is None
    assert vars(item) == before and caplog.records == []


@pytest.mark.asyncio
async def test_case_claim_captures_two_independent_attempts_without_changing_original_input(formal_case):
    s = formal_case
    receipt = await submit(s)
    task = await claim(s)
    assert task.id == receipt.id and task.status == "claimed" and task.attempt_no == 1
    assert task.attempt_id != s.attempt.id
    lease = task.runtime_owner.controlled_case_lease
    assert lease.run_item_execution_attempt_id == s.attempt.id
    assert lease.lease_token == s.item.lease_token and lease.worker_id == s.attempt.worker_id
    assert task.runtime_owner.owner_token == task.lease_token != lease.lease_token
    assert "controlled_case_lease" not in task.runtime_owner.model_dump()
    from src.schemas.runtime_binding import RuntimeCheckpointSource

    source = RuntimeCheckpointSource.model_validate(task.runtime_owner.model_dump(exclude={"owner_token"}))
    assert s.item.lease_token not in source.model_dump_json() and task.lease_token not in source.model_dump_json()
    assert task.input == {"case_execution_input": s.asset.model_dump(mode="json")}
    current = await s.d.runs.get_item(s.item.id)
    assert current == s.item  # No second RunItem claim, reset or silent renewal.
    assert s.h.runtime.calls == []


@pytest.mark.asyncio
async def test_generic_claim_and_default_capabilities_cannot_bypass_case_port(formal_case):
    s = formal_case
    receipt = await submit(s)
    before = database_digest(s)
    with pytest.raises(ValueError, match="reference"):
        await s.h.tasks.claim(receipt.id, worker_id="generic", expected_binding=s.binding,
            framework_capabilities=capabilities(), check_model_ledger=True)
    with pytest.raises(TaskDeferred, match="runtime_kind_unavailable"):
        await s.h.tasks.claim_controlled_case(delivery(s), worker_id="default",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_fresh_case_uses_existing_model_ledger_without_any_provider_call(formal_case):
    from tests.test_framework_model_calls import intent

    s = formal_case
    await submit(s)
    task = await claim(s)
    call = intent(position_version=1, model_config_fingerprint=s.binding.model_config_fingerprint)
    prepared = await s.calls.prepare_initial(task.runtime_owner, call)
    assert prepared.phase == "ready" and prepared.owner_attempt_id == task.attempt_id
    assert prepared.owner_attempt_id != s.attempt.id
    before = database_digest(s)
    assert await s.calls.prepare_initial(task.runtime_owner, call) == prepared
    assert database_digest(s) == before and s.h.runtime.calls == []


@pytest.mark.asyncio
async def test_new_run_item_attempt_cannot_inherit_old_task_execution_authority(formal_case):
    from src.runtime.postgres_runtime_owner import assert_runtime_owner

    s = formal_case
    await submit(s)
    task = await claim(s)
    future = s.now + timedelta(minutes=3)
    # Persist the old generic reaper's identity-changing failure sample. The
    # current reaper must no longer produce it, but historical successor data
    # still must not inherit the old AgentTask's execution authority.
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        item = s.d.runs._lock_item(cur, s.item.id)
        original = s.d.runs._lock_attempt(cur, s.item.id, s.item.lease_token)
        s.d.runs._write_attempt(cur, original.model_copy(update={"status": "expired", "completed_at": future}))
        s.d.runs._write_item(cur, item.model_copy(update={"status": "queued", "lease_owner": None,
            "lease_token": None, "lease_expires_at": None, "heartbeat_at": None, "started_at": None}))
    claims = await s.d.runs.claim_items(run_id=s.detail.run.id, worker_id="replacement-run-worker",
        limit=20, lease_seconds=120, now=future)
    item, attempt = next((item, attempt) for item, attempt in claims if item.id == s.item.id)
    assert attempt.recovered_from_attempt_id == s.attempt.id and attempt.id != s.attempt.id
    before = database_digest(s)
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        with pytest.raises(ContinuationLeaseLostError):
            assert_runtime_owner(cur, s.h.settings, task.runtime_owner, states=("claimed", "running"))
    assert not await s.h.tasks.renew_framework_execution(task, 90)
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reference", "identity", "input", "corrupt_input", "budget", "checkpoint", "expired", "cancelled"])
async def test_case_claim_rejects_drift_without_claim_or_renewal(formal_case, change):
    s = formal_case
    await submit(s)
    raw = delivery(s)
    if change == "reference":
        raw["case_execution_reference"]["input_sha256"] = "f" * 64
    elif change == "identity":
        raw["trace_id"] = "different-trace"
    else:
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            if change in {"input", "corrupt_input"}:
                asset = s.asset.model_dump(mode="json")
                asset["context"]["objective"] += " changed after admission"
                if change == "input":
                    asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
                cur.execute(f"UPDATE {s.h.tasks._tasks} SET input_payload=%s::jsonb WHERE id=%s",
                    (json.dumps({"case_execution_input": asset}), s.command.task_id))
            elif change == "budget":
                changed = s.budget.model_copy(update={"max_calls": s.budget.max_calls + 1})
                cur.execute(f"UPDATE {s.calls.budgets_table} SET policy=%s::jsonb WHERE binding_id=%s",
                    (changed.model_dump_json(), s.binding.binding_id))
            elif change == "checkpoint":
                attempt = s.d.runs._get_attempt_by_id(cur, s.attempt.id)
                payload = {**attempt.checkpoint_payload, "task_id": "another-task"}
                s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": payload}))
            elif change == "expired":
                cur.execute("SELECT clock_timestamp() - interval '1 second' AS expired")
                s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": cur.fetchone()["expired"]}))
            else:
                s.d.runs._write_run(cur, s.detail.run.model_copy(update={"status": "cancelled"}))
    before = database_digest(s)
    with pytest.raises((ValueError, ContinuationLeaseLostError)):
        await claim(s, raw)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_two_case_claims_create_only_one_agent_attempt(formal_case):
    s = formal_case
    await submit(s)
    results = await asyncio.gather(claim(s), claim(s), return_exceptions=True)
    assert sum(isinstance(result, TaskDeferred) for result in results) == 1
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {s.h.tasks._attempts}")
        assert cur.fetchone()["n"] == 1
    assert (await s.d.runs.get_item(s.item.id)) == s.item


@pytest.mark.asyncio
async def test_claim_rechecks_original_expiry_after_waiting_on_runtime_owner(formal_case):
    s = formal_case
    await submit(s)
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() + interval '2 seconds' AS expiry")
        expiry = cur.fetchone()["expiry"]
        s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": expiry}))
    before = database_digest(s)
    table = f"{s.h.tasks._tasks}_runtime"
    with postgres_connect(s.h.settings) as holder, holder.cursor() as lock:
        lock.execute(f"SELECT binding_id FROM {table} WHERE binding_id=%s FOR UPDATE", (s.binding.binding_id,))
        pending = asyncio.create_task(claim(s))
        try:
            await wait_for_session_sql_lock(s, table)
            lock.execute("SELECT clock_timestamp() < %s AS active", (expiry,))
            assert lock.fetchone()["active"]
            deadline = time.monotonic() + 4
            while True:
                lock.execute("SELECT clock_timestamp() >= %s AS expired", (expiry,))
                if lock.fetchone()["expired"]:
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            holder.commit()
            with pytest.raises(ContinuationLeaseLostError):
                await pending
        finally:
            holder.rollback()
            if not pending.done():
                await pending
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_case_heartbeat_renews_both_leases_atomically_and_does_not_change_attempts(formal_case, monkeypatch):
    s = formal_case
    await submit(s)
    task = await claim(s)
    original = s.d.runs.__class__._write_item
    before = database_digest(s)
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("controlled heartbeat rollback")
    with monkeypatch.context() as patch:
        patch.setattr(s.d.runs.__class__, "_write_item", fail)
        with pytest.raises(RuntimeError, match="heartbeat rollback"):
            await s.h.tasks.renew_framework_execution(task, 90)
    assert database_digest(s) == before
    assert await s.h.tasks.renew_framework_execution(task, 90)
    item = await s.d.runs.get_item(s.item.id)
    assert item.model_dump(exclude={"lease_expires_at", "updated_at"}) == s.item.model_dump(exclude={"lease_expires_at", "updated_at"})
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT expires_at FROM {s.h.tasks._tasks}_runtime WHERE binding_id=%s", (s.binding.binding_id,))
        assert cur.fetchone()["expires_at"] == item.lease_expires_at
    assert task.runtime_owner.controlled_case_lease.lease_token == s.item.lease_token


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["run_item", "agent_task", "stripped"])
async def test_either_lost_lease_fences_real_saver_writes_and_model_preparation(formal_case, lost, caplog):
    from langgraph.checkpoint.base import empty_checkpoint
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from tests.test_framework_model_calls import intent

    s = formal_case
    await submit(s)
    task = await claim(s)
    await s.h.tasks.begin(task, resource_id="isolated-case-resource", fencing_token=1)
    s.h.settings.deep_agents.checkpoint_schema = f"case_lease_{uuid4().hex}"
    provider = DeepAgentCheckpointProvider(s.h.settings)
    config = {"configurable": {"thread_id": s.turn}}
    try:
        async with provider.open(owner=task.runtime_owner) as saver:
            saved = await saver.aput(config, empty_checkpoint(), {}, {})
            await saver.aput_writes(saved, [("result", "original")], "tool")
            if lost == "run_item":
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    cur.execute("SELECT clock_timestamp() - interval '1 second' AS expiry")
                    expiry = cur.fetchone()["expiry"]
                    s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": expiry}))
            elif lost == "agent_task":
                assert await s.h.store.renew_turn_execution(s.session.id, task.lease_token, 0)
            else:
                task = task.model_copy(update={"runtime_owner": task.runtime_owner.model_copy(update={"controlled_case_lease": None})})
                saver.runtime_owner = task.runtime_owner
            before = await saver.aget_tuple(saved)
            with pytest.raises(ContinuationLeaseLostError):
                await saver.aput(config, empty_checkpoint(), {}, {})
            with pytest.raises(ContinuationLeaseLostError):
                await saver.aput_writes(saved, [("result", "late")], "tool")
            with pytest.raises(ContinuationLeaseLostError):
                await s.calls.execution_deadline(task.runtime_owner)
            before_ledger = database_digest(s)
            with pytest.raises(ContinuationLeaseLostError):
                await s.calls.prepare_initial(task.runtime_owner,
                    intent(position_version=1, model_config_fingerprint=s.binding.model_config_fingerprint))
            assert database_digest(s) == before_ledger
            assert not await s.h.tasks.renew_framework_execution(task, 90)
            assert await saver.aget_tuple(saved) == before
            if lost == "run_item":
                from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT

                records = [record for record in caplog.records if record.message == "controlled_case_execution_lease_rejected"]
                assert records
                for record in records:
                    ContextFilter().filter(record)
                    diagnostic = json.loads(ContextFormatter(LOG_FORMAT).format(record).split(" diagnostics=", 1)[1])
                    observed = datetime.fromisoformat(diagnostic["authority_observed_at"])
                    assert diagnostic["case_lease_expires_at"] == expiry.isoformat()
                    assert diagnostic["case_lease_remaining_seconds"] == (expiry-observed).total_seconds() < 0
                    assert diagnostic["case_item_status"] == "running" and diagnostic["automatic_replay"] is False
                    assert s.item.lease_token not in repr(record.__dict__)
    finally:
        await provider.close()
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {s.h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_case_cannot_settle_completed_without_its_unique_finalizer(formal_case):
    s = formal_case
    await submit(s)
    task = await claim(s)
    await s.h.tasks.begin(task, resource_id="case-test", fencing_token=1)
    before = database_digest(s)
    with pytest.raises(ValueError, match="finalizer"):
        await s.h.tasks.finish(task, status="completed", result_status="completed")
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("format_change", ["future_input", "extra_input", "future_budget"])
async def test_future_case_originals_defer_before_claim_without_changing_facts(formal_case, format_change):
    s = formal_case
    await submit(s)
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        if format_change == "future_budget":
            raw = s.budget.model_dump(mode="json")
            raw["schema_version"] = 3
            cur.execute(f"UPDATE {s.calls.budgets_table} SET policy=%s::jsonb WHERE binding_id=%s",
                (json.dumps(raw), s.binding.binding_id))
        else:
            raw = s.asset.model_dump(mode="json")
            raw["future_field" if format_change == "extra_input" else "schema_version"] = (
                2 if format_change == "extra_input" else 5)
            cur.execute(f"UPDATE {s.h.tasks._tasks} SET input_payload=%s::jsonb WHERE id=%s",
                (json.dumps({"case_execution_input": raw}), s.command.task_id))
    before = database_digest(s)
    with pytest.raises(TaskDeferred, match="format_unavailable"):
        await claim(s)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_running_task_lost_owner_becomes_reconciliation_without_reexecution(formal_case):
    s = formal_case
    await submit(s)
    task = await claim(s)
    await s.h.tasks.begin(task, resource_id="case-test", fencing_token=1)
    assert await s.h.store.renew_turn_execution(s.session.id, task.lease_token, 0)
    observed = await claim(s)
    assert observed.status == "recovery_required" and observed.attempt_id == task.attempt_id
    assert observed.attempt_no == 1 and observed.error_code == "execution_owner_expired"
    before = database_digest(s)
    assert (await claim(s)).status == "recovery_required"
    assert database_digest(s) == before and s.h.runtime.calls == []


@pytest.mark.asyncio
async def test_case_claim_lock_timeout_preserves_original_deferred_classification(formal_case):
    s = formal_case
    await submit(s)
    s.h.settings.deep_agents.checkpoint_lock_timeout_ms = 50
    before = database_digest(s)
    with postgres_connect(s.h.settings) as holder, holder.cursor() as lock:
        lock.execute(f"SELECT binding_id FROM {s.h.tasks._tasks}_runtime WHERE binding_id=%s FOR UPDATE",
            (s.binding.binding_id,))
        with pytest.raises(TaskDeferred, match="lock is busy"):
            await claim(s)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_terminal_case_delivery_after_run_cancellation_is_receipt_only(formal_case):
    s = formal_case
    await submit(s)
    task = await claim(s)
    await s.h.tasks.finish(task, status="recovery_required", error_code="explicit_test_reconciliation")
    await s.d.runs.cancel_run(s.detail.run.id, "isolated cancellation", datetime.now(timezone.utc))
    before = database_digest(s)
    receipt = await claim(s)
    assert receipt.status == "recovery_required" and receipt.attempt_id == task.attempt_id
    assert receipt.runtime_owner is None
    assert database_digest(s) == before

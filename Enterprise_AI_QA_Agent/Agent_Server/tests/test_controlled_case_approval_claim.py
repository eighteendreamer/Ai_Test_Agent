"""New physical owner, original formal Attempt; no graph decision delivery yet."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import importlib
import json
import subprocess
import sys
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_wait import park, facts, clock, implementation as wait_service
from tests.test_controlled_case_approval_submission import submit, implementation as admission_service
from tests.test_controlled_case_execution_lease import delivery, capabilities
from tests.test_controlled_case_task_input import source as memory_case


def implementation(p):
    return importlib.import_module("src.runtime.controlled_case_approval_continuation").ControlledCaseApprovalContinuations(
        admission_service(p))


def claim_caps():
    return replace(capabilities(), controlled_case_approval_claim_available=True)


def wire(p, task_id):
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {p.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("agent_turn:" + task_id,))
        return cur.fetchone()["payload"]


async def admitted(p):
    wait = await park(p)
    task = await submit(p, wait.batch[0].approval_id)
    return wait, task, wire(p, task.id)


async def claim(p, raw, worker_id="approval-worker"):
    return await implementation(p).claim(raw, worker_id=worker_id, capabilities=claim_caps())


async def begin(p, task, *, caps=None):
    return await implementation(p).begin(task, resource_id="isolated-approval", fencing_token=2,
        capabilities=caps or claim_caps())


@pytest.mark.asyncio
async def test_dedicated_claim_rotates_both_capabilities_but_preserves_original_reservation(pending_formal):
    p = pending_formal
    wait, submitted, raw = await admitted(p)
    origin = await p.s.h.tasks.get(p.h.task.id)
    native, budget = native_originals(p), await p.s.calls.snapshot(p.s.binding)
    saved = await p.s.d.runs.get_run(p.s.item.run_id)
    previous = next(a for a in saved.attempts if a.id == p.s.attempt.id)
    task = await claim(p, raw)
    assert task.id == submitted.id and task.operation == "case_approval" and task.status == "claimed"
    assert task.attempt_no == 1 and task.attempt_id not in {p.h.task.attempt_id, p.s.attempt.id}
    owner, lease = task.runtime_owner, task.runtime_owner.controlled_case_lease
    assert owner.owner_epoch == p.h.task.runtime_owner.owner_epoch + 1
    assert owner.task_id == task.id and owner.attempt_id == task.attempt_id and owner.owner_token == task.lease_token
    assert lease.run_item_execution_attempt_id == previous.id and lease.attempt_no == previous.attempt_no
    assert lease.worker_id == task.worker_id and lease.input_sha256 == wait.input_sha256
    assert len({lease.lease_token, task.lease_token, p.s.item.lease_token, p.h.task.lease_token}) == 4
    current = await p.s.d.runs.get_run(p.s.item.run_id)
    item = next(i for i in current.items if i.id == p.s.item.id)
    attempt = next(a for a in current.attempts if a.id == previous.id)
    assert item.status == attempt.status == "claimed" and item.lease_token == attempt.lease_token == lease.lease_token
    assert attempt.checkpoint_payload == previous.checkpoint_payload and attempt.checkpoint_version == previous.checkpoint_version
    assert attempt.started_at == previous.started_at and attempt.waiting_started_at is None
    assert len(current.attempts) == len(saved.attempts) and current.results == []
    assert await p.s.h.tasks.get(p.h.task.id) == origin
    assert native_originals(p) == native and await p.s.calls.snapshot(p.s.binding) == budget
    assert await wait_service(p).acknowledge_delivery(delivery(p.s), capabilities=capabilities()) == wait
    assert "controlled_case_lease" not in owner.model_dump() and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_begin_commits_formal_running_before_new_owner_can_write(pending_formal):
    p = pending_formal
    wait, _, raw = await admitted(p)
    task = await claim(p, raw)
    before = facts(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        configure_runtime_owner_transaction(cur, p.h.settings)
        with pytest.raises(ContinuationLeaseLostError):
            assert_runtime_owner(cur, p.h.settings, task.runtime_owner)
    assert facts(p) == before
    prepared = await implementation(p).begin(task, resource_id="isolated-approval", fencing_token=2, capabilities=claim_caps())
    assert prepared.asset_sha256 == wait.input_sha256 and prepared.graph_input.binding == p.s.binding
    current = await p.s.d.runs.get_run(p.s.item.run_id)
    item = next(i for i in current.items if i.id == p.s.item.id)
    attempt = next(a for a in current.attempts if a.id == p.s.attempt.id)
    assert item.status == attempt.status == (await p.s.h.tasks.get(task.id)).status == "running"
    assert item.active_started_at == attempt.active_started_at and item.active_started_at is not None
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        configure_runtime_owner_transaction(cur, p.h.settings)
        locked = assert_runtime_owner(cur, p.h.settings, task.runtime_owner)
        assert locked.checkpoint.task_id == p.h.task.id and locked.attempt.id == p.s.attempt.id
    for owner in (p.h.task.runtime_owner, task.runtime_owner.model_copy(update={"controlled_case_lease": None})):
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, p.h.settings)
            with pytest.raises(ContinuationLeaseLostError):
                assert_runtime_owner(cur, p.h.settings, owner)
    now = clock(p)
    first = await p.s.calls.root_activity_seconds(p.s.binding, as_of=now)
    later = await p.s.calls.root_activity_seconds(p.s.binding, as_of=now + timedelta(seconds=60))
    assert later - first == pytest.approx(60)
    with pytest.raises(ValueError, match="unique application finalizer"):
        await p.s.h.tasks.finish(task, status="completed", result_status="completed")
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["disabled", "project_id", "run_id", "run_item_id", "reference_id", "trace_id", "turn_id",
    "request_id", "root_trace_id", "parent_task_id", "priority", "worker_id", "input"])
async def test_begin_requires_enabled_composition_and_exact_captured_identity(pending_formal, drift):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    before = facts(p)
    if drift == "disabled":
        with pytest.raises(TaskDeferred, match="case_approval_claim_unavailable"):
            await begin(p, task, caps=capabilities())
    else:
        changed = task.model_copy(update={drift: task.priority + 1 if drift == "priority" else {} if drift == "input" else "foreign"})
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="captured"):
            await begin(p, changed)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "old_reader", "generic", "dispatcher"])
async def test_format_support_never_opens_default_claim_or_dispatch(pending_formal, monkeypatch, mode):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask

    p = pending_formal
    _, submitted, raw = await admitted(p)
    before = facts(p)
    if mode in {"disabled", "old_reader"}:
        caps = capabilities() if mode == "disabled" else replace(claim_caps(), controlled_case_approval_admission_max_version=0)
        with pytest.raises(TaskDeferred, match="case_approval_.*unavailable"):
            await implementation(p).claim(raw, worker_id="disabled", capabilities=caps)
    elif mode == "generic":
        with pytest.raises(ValueError, match="dedicated dual-owner"):
            await p.s.h.tasks.claim(submitted.id, worker_id="generic", expected_binding=p.s.binding,
                framework_capabilities=claim_caps())
    else:
        def forbidden(*args, **kwargs):
            pytest.fail("Default dispatcher acquired formal continuation authority")
        for method in ("claim", "claim_controlled_case", "begin", "finish"):
            monkeypatch.setattr(p.s.h.tasks, method, forbidden)
        monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
        dispatcher = AgentTaskDispatcher(p.h.settings, p.s.h.tasks, p.s.h.sessions, p.s.h.resources,
            framework_capabilities=claim_caps())
        with pytest.raises(TaskDeferred, match="framework_runtime_kind_unavailable"):
            await dispatcher.handle_v2(QueuedTask("isolated", raw), worker_id="default")
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_concurrent_claim_has_one_physical_owner_and_no_second_formal_attempt(pending_formal):
    p = pending_formal
    _, submitted, raw = await admitted(p)
    results = await asyncio.gather(claim(p, raw, "worker-a"), claim(p, raw, "worker-b"), return_exceptions=True)
    winners = [result for result in results if not isinstance(result, Exception)]
    assert len(winners) == 1 and sum(isinstance(result, TaskDeferred) for result in results) == 1
    assert winners[0].id == submitted.id and winners[0].attempt_no == 1
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {p.s.h.tasks._attempts} WHERE task_id=%s", (submitted.id,))
        assert cur.fetchone()["n"] == 1
        cur.execute(f"SELECT count(*) AS n FROM {p.s.d.runs._attempt_table} WHERE run_item_id=%s", (p.s.item.id,))
        assert cur.fetchone()["n"] == 1
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["claimed", "running", "expired_claimed", "expired_running"])
async def test_duplicate_delivery_never_reclaims_even_an_expired_owner(pending_formal, state):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    if state.endswith("running"):
        await begin(p, task)
    if state.startswith("expired"):
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s",
                (p.s.binding.binding_id,))
    before = facts(p)
    for worker in (task.worker_id, "new-worker"):
        with pytest.raises(TaskDeferred, match="already owned|reconciliation"):
            await claim(p, raw, worker)
        assert facts(p) == before
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_siblings_are_fifo_and_later_turn_message_cannot_block_current_approval(pending_formal):
    from tests.test_agent_task_dispatch import payload

    p = pending_formal
    wait = await park(p)
    later = await p.s.h.dispatcher.submit(p.s.session.id, payload("next turn", key="next-turn", submit_mode="background"))
    first = await submit(p, wait.batch[0].approval_id)
    second = await submit(p, wait.batch[1].approval_id)
    before = facts(p)
    with pytest.raises(TaskDeferred, match="earlier"):
        await claim(p, wire(p, second.id))
    assert facts(p) == before
    task = await claim(p, wire(p, first.id))
    await begin(p, task)
    before = facts(p)
    with pytest.raises(TaskDeferred, match="already owned"):
        await claim(p, wire(p, second.id))
    with pytest.raises(TaskDeferred):
        await p.s.h.tasks.claim(later.task.id, worker_id="later")
    assert facts(p) == before and (await p.s.h.tasks.get(second.id)).attempt_no == 0
    assert len(p.requests) == 1 and p.effects == []


def write_pattern(p, step, phase):
    tasks, runs = p.s.h.tasks, p.s.d.runs
    return {"task": f"UPDATE {tasks._tasks} SET " + ("status='claimed'" if phase == "claim" else "resource_id="),
        "agent_attempt": (f"INSERT INTO {tasks._attempts}" if phase == "claim" else f"UPDATE {tasks._attempts} SET status="),
        "session": f"UPDATE {tasks._session_table} SET " + ("metadata=metadata ||" if phase == "claim" else "status='running'"),
        "item": f"UPDATE {runs._item_table} SET position =",
        "attempt": f"UPDATE {runs._attempt_table} SET status =",
        "owner": f"UPDATE {tasks._tasks}_runtime SET",
        "audit": f"INSERT INTO {p.h.settings.database.postgres_event_table}",
        "second_audit": f"INSERT INTO {p.h.settings.database.postgres_event_table}"}[step]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,step", [(phase, step) for phase in ("claim", "begin")
    for step in ("task", "agent_attempt", "session", "item", "attempt", "owner", "audit")]
    + [("claim", "second_audit")])
async def test_each_dual_owner_write_boundary_rolls_back_all_facts(pending_formal, monkeypatch, phase, step):
    from src.infrastructure.sqlalchemy_runtime import SQLAlchemyCursor

    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw) if phase == "begin" else None
    before, original, matched = facts(p), SQLAlchemyCursor.execute, 0
    pattern = write_pattern(p, step, phase)
    def execute(self, statement, parameters=None):
        nonlocal matched
        result = original(self, statement, parameters)
        if " ".join(statement.split()).startswith(pattern):
            matched += 1
            if matched == (2 if step == "second_audit" else 1):
                raise RuntimeError("isolated dual owner write rollback")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(SQLAlchemyCursor, "execute", execute)
        with pytest.raises(RuntimeError, match="dual owner write rollback"):
            await (claim(p, raw) if phase == "claim" else begin(p, task))
    assert matched > 0 and facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["claim", "begin"])
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_final_write_wait_crossing_lease_expiry_rolls_back_every_owner(pending_formal, monkeypatch, phase, lease):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw) if phase == "begin" else None
    before, original, reached = facts(p), p.s.h.tasks._event, False
    def event(cur, row, kind, payload):
        nonlocal reached
        original(cur, row, kind, payload)
        if kind == ("test_run.case_approval_claimed" if phase == "claim" else "agent_task.running"):
            reached = True
            # A real locked transaction crosses a DB deadline, without waiting
            # the normal 120s lease or changing the pre-transaction originals.
            if lease == "session":
                cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                    "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()+INTERVAL '0.05 seconds')) WHERE id=%s",
                    (p.s.session.id,))
            elif lease == "owner":
                cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at=clock_timestamp()+INTERVAL '0.05 seconds' WHERE binding_id=%s",
                    (p.s.binding.binding_id,))
            else:
                cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                    "to_jsonb(clock_timestamp()+INTERVAL '0.05 seconds')) WHERE id=%s", (p.s.item.id,))
            cur.execute("SELECT pg_sleep(0.1)")
    with monkeypatch.context() as patch:
        patch.setattr(p.s.h.tasks, "_event", event)
        with pytest.raises(ContinuationLeaseLostError):
            await (claim(p, raw) if phase == "claim" else begin(p, task))
    assert reached and facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,failure", [(phase, failure) for phase in ("claim", "begin")
    for failure in ("reply_loss", "cancel_waiter")])
async def test_unknown_transaction_reply_never_automatically_replays(pending_formal, monkeypatch, phase, failure, caplog):
    p = pending_formal
    _, submitted, raw = await admitted(p)
    owner = await claim(p, raw) if phase == "begin" else None
    service = implementation(p)
    entered, release, finished = Event(), Event(), Event()
    target = type(service) if phase == "claim" else type(p.s.h.tasks)
    method = "_claim" if phase == "claim" else "_transition_sync"
    original, original_event = getattr(target, method), p.s.h.tasks._event
    def event(cur, row, kind, payload):
        original_event(cur, row, kind, payload)
        if kind == ("test_run.case_approval_claimed" if phase == "claim" else "agent_task.running") and failure == "cancel_waiter":
            entered.set()
            assert release.wait(30)
    def commit(self, *args, **kwargs):
        try:
            result = original(self, *args, **kwargs)
            if failure == "reply_loss":
                raise RuntimeError("isolated committed reply loss")
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(target, method, commit)
        patch.setattr(p.s.h.tasks, "_event", event)
        waiter = asyncio.create_task(service.claim(raw, worker_id="approval-worker", capabilities=claim_caps())
            if phase == "claim" else service.begin(owner, resource_id="isolated", fencing_token=2, capabilities=claim_caps()))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="committed reply loss"):
                await waiter
    saved = await p.s.h.tasks.get(submitted.id)
    assert saved.attempt_no == 1 and saved.status == ("claimed" if phase == "claim" else "running")
    before = facts(p)
    with pytest.raises(TaskDeferred, match="already owned"):
        await claim(p, raw)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []
    assert any(getattr(record, "recovery_reason", None) == "approval_owner_receipt_unknown"
        and getattr(record, "automatic_replay", None) is False for record in caplog.records)


@pytest.mark.asyncio
async def test_rotated_formal_capability_is_consistent_in_relational_and_record_lookup(pending_formal):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    lease = task.runtime_owner.controlled_case_lease
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT lease_token,record->>'lease_token' AS recorded FROM {p.s.d.runs._attempt_table} WHERE id=%s",
            (p.s.attempt.id,))
        saved = cur.fetchone()
        assert len({approval_value_hash(value) for value in (saved["lease_token"], saved["recorded"], lease.lease_token)}) == 1
        assert p.s.d.runs._lock_attempt(cur, p.s.item.id, lease.lease_token).id == p.s.attempt.id
        with pytest.raises(ValueError, match="Active attempt not found"):
            p.s.d.runs._lock_attempt(cur, p.s.item.id, p.s.item.lease_token)
    before = facts(p)
    with pytest.raises(ValueError, match="租约"):
        await p.s.d.runs.start_item(p.s.item.id, p.s.item.lease_token, clock(p))
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,drift", [(phase, drift) for phase in ("claim", "begin") for drift in (
    "parent", "input_hash", "admission", "origin_outbox", "successor_outbox", "pending_batch", "control", "interrupt",
    "job_started", "native_source", "journal_sending", "journal_delivered", "journal_applied", "ledger", "run_cancelled")])
async def test_claim_and_begin_recheck_durable_originals_without_repair(pending_formal, phase, drift):
    p = pending_formal
    wait, submitted, raw = await admitted(p)
    task = await claim(p, raw) if phase == "begin" else None
    if drift == "run_cancelled":
        await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated cancellation", clock(p))
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "parent":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET parent_task_id=NULL WHERE id=%s", (submitted.id,))
        elif drift == "input_hash":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_hash=%s WHERE id=%s", ("0" * 64, submitted.id))
        elif drift == "admission":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_payload=jsonb_set(input_payload,"
                "'{controlled_case_approval_admission,scope}','\"foreign\"') WHERE id=%s", (submitted.id,))
        elif drift.endswith("outbox"):
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                ("agent_turn:" + (p.h.task.id if drift == "origin_outbox" else submitted.id),))
        elif drift == "pending_batch":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{pending_turn,pending_approval_ids}',%s::jsonb) WHERE id=%s",
                (json.dumps([wait.batch[0].approval_id]), p.s.session.id))
        elif drift == "control":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{control}','{\"control_state\":\"cancelled\"}'::jsonb) WHERE id=%s", (p.s.session.id,))
        elif drift == "interrupt":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET interrupt_requested=true WHERE id=%s", (submitted.id,))
        elif drift == "job_started":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET started_at=clock_timestamp() WHERE id=%s",
                (wait.batch[0].tool_job_id,))
        elif drift == "native_source":
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoints SET "
                "metadata=jsonb_set(metadata,'{source}','\"update\"') WHERE thread_id=%s", (p.s.binding.thread_id,))
        elif drift.startswith("journal"):
            change = {"journal_sending": "state='sending'", "journal_delivered": "state='delivered',delivered_at=clock_timestamp(),proof='{}'::jsonb",
                "journal_applied": "applied_at=clock_timestamp()"}[drift]
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET {change} WHERE approval_id=%s", (submitted.reference_id,))
        elif drift == "ledger":
            cur.execute(f"SELECT record FROM {p.s.calls.calls_table} WHERE binding_id=%s", (p.s.binding.binding_id,))
            later = deepcopy(cur.fetchone()["record"])
            later["intent"]["attempt_id"], later["intent"]["logical_call_id"] = "isolated-later-attempt", "isolated-later-call"
            later["created_at"] = clock(p).isoformat()
            cur.execute(f"INSERT INTO {p.s.calls.calls_table}(binding_id,attempt_id,record) VALUES (%s,%s,%s::jsonb)",
                (p.s.binding.binding_id, later["intent"]["attempt_id"], json.dumps(later)))
    before = facts(p)
    # These paths retain their existing distinct codec, admission and lease
    # exceptions; every rejection must preserve the full captured DB/native set.
    error = (ContinuationLeaseLostError if phase == "begin" and drift in {"parent", "input_hash", "admission", "run_cancelled"}
        else TaskDeferred if drift == "admission" else ValueError)
    with pytest.raises(error):
        await (claim(p, raw) if phase == "claim" else begin(p, task))
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["formal_expiry", "task_expiry", "epoch", "formal_token", "task_token", "stripped"])
async def test_begin_and_renew_fence_each_independent_capability(pending_formal, lost):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    owner = task.runtime_owner
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if lost == "formal_expiry":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "'\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif lost == "task_expiry":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif lost == "epoch":
            owner = owner.model_copy(update={"owner_epoch": owner.owner_epoch - 1})
        elif lost == "formal_token":
            owner = owner.model_copy(update={"controlled_case_lease": owner.controlled_case_lease.model_copy(
                update={"lease_token": p.s.item.lease_token})})
        elif lost == "task_token":
            owner = owner.model_copy(update={"owner_token": p.h.task.lease_token})
        else:
            owner = owner.model_copy(update={"controlled_case_lease": None})
    captured = task.model_copy(update={"runtime_owner": owner})
    before = facts(p)
    with pytest.raises(ContinuationLeaseLostError):
        await begin(p, captured)
    assert not await p.s.h.tasks.renew_framework_execution(captured, 90)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["claimed", "running"])
async def test_renew_is_paired_atomic_and_cannot_revive_original_worker(pending_formal, monkeypatch, state):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    if state == "running":
        await begin(p, task)
    before, original = facts(p), type(p.s.d.runs)._write_item
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("isolated paired heartbeat rollback")
    with monkeypatch.context() as patch:
        patch.setattr(type(p.s.d.runs), "_write_item", fail)
        with pytest.raises(RuntimeError, match="heartbeat rollback"):
            await p.s.h.tasks.renew_framework_execution(task, 90)
    assert facts(p) == before
    assert await p.s.h.tasks.renew_framework_execution(task, 90)
    current = await p.s.d.runs.get_item(p.s.item.id)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT expires_at FROM {p.s.h.tasks._tasks}_runtime WHERE binding_id=%s", (p.s.binding.binding_id,))
        assert cur.fetchone()["expires_at"] == current.lease_expires_at
        cur.execute(f"SELECT (metadata->>'turn_lease_expires_at')::timestamptz AS expiry FROM {p.s.h.tasks._session_table} WHERE id=%s",
            (p.s.session.id,))
        assert cur.fetchone()["expiry"] == current.lease_expires_at
    before = facts(p)
    assert not await p.s.h.tasks.renew_framework_execution(p.h.task, 90)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_plain_begin_and_defer_stay_closed_and_audit_contains_only_lease_hashes(pending_formal):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    before = facts(p)
    with pytest.raises(ValueError, match="dedicated admission"):
        await p.s.h.tasks.begin(task, resource_id="generic", fencing_token=2)
    with pytest.raises(ValueError, match="dedicated admission"):
        await p.s.h.tasks.defer(task, "unavailable")
    assert facts(p) == before
    events = await p.h.store.list_events(p.s.session.id)
    audit, = [event for event in events if event.type == "test_run.case_approval_claimed"]
    serialized = json.dumps(audit.model_dump(mode="json"))
    for value in (task.lease_token, task.runtime_owner.controlled_case_lease.lease_token, p.h.task.lease_token, p.s.item.lease_token):
        assert value not in serialized
    assert audit.payload["previous_case_lease_sha256"] != audit.payload["case_lease_sha256"]
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"deadline_seconds": 20}], indirect=True)
@pytest.mark.parametrize("phase,window", [(phase, window) for phase in ("claim", "begin") for window in ("before", "last_write")])
async def test_frozen_root_deadline_cannot_be_extended_by_queue_or_final_writes(pending_formal, monkeypatch, phase, window):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw) if phase == "begin" else None
    deadline = p.s.budget.deadline_at
    assert clock(p) < deadline
    before, original, reached = facts(p), p.s.h.tasks._event, False
    def wait_until_deadline(cur):
        cur.execute("SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM (%s::timestamptz-clock_timestamp())))+0.05)", (deadline,))
    def event(cur, row, kind, payload):
        nonlocal reached
        original(cur, row, kind, payload)
        if kind == ("test_run.case_approval_claimed" if phase == "claim" else "agent_task.running"):
            reached = True
            wait_until_deadline(cur)
    if window == "before":
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            wait_until_deadline(cur)
    with monkeypatch.context() as patch:
        if window == "last_write":
            patch.setattr(p.s.h.tasks, "_event", event)
        with pytest.raises(ValueError, match="deadline has expired"):
            await (claim(p, raw) if phase == "claim" else begin(p, task))
    assert reached == (window == "last_write")
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.parametrize("module", ["src.runtime.controlled_case_approval_continuation",
    "src.runtime.controlled_case_execution", "src.runtime.postgres_agent_task_store"])
def test_continuation_modules_import_without_prior_initialization(module):
    result = subprocess.run([sys.executable, "-c", f"import importlib; importlib.import_module({module!r}); print('cold-ok')"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold-ok"


@pytest.mark.asyncio
async def test_generic_formal_start_cannot_separate_the_claimed_dual_owners(pending_formal):
    p = pending_formal
    _, _, raw = await admitted(p)
    task = await claim(p, raw)
    before = facts(p)
    with pytest.raises(ValueError, match="dedicated approval continuation"):
        await p.s.d.runs.start_item(p.s.item.id, task.runtime_owner.controlled_case_lease.lease_token, clock(p))
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key,version", [("controlled_case_admission", 1), ("controlled_case_admission", 999),
    (None, 1), ("agent_case_execution", 1), ("unrelated_checkpoint", 999)])
async def test_generic_start_respects_reserved_namespace_but_keeps_ordinary_claims(memory_case, key, version):
    s = memory_case
    item, attempt = s.store._items[s.item.id], s.store._attempts[s.attempt.id]
    # Memory has no formal admission; this is namespace compatibility data.
    s.store._items[item.id] = item.model_copy(update={"status": "claimed"})
    s.store._attempts[attempt.id] = attempt.model_copy(update={"status": "claimed", "checkpoint_key": key,
        "checkpoint_version": version, "checkpoint_payload": {"schema_version": version} if key else None})
    before = await s.store.get_run(s.detail.run.id)
    if key == "controlled_case_admission":
        with pytest.raises(ValueError, match="dedicated approval continuation"):
            await s.store.start_item(item.id, item.lease_token, s.now)
        assert await s.store.get_run(s.detail.run.id) == before
    else:
        started = await s.store.start_item(item.id, item.lease_token, s.now)
        assert started.status == "running" and (await s.store.get_latest_attempt(item.id)).id == attempt.id

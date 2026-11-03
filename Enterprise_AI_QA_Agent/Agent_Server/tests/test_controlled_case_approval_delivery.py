"""One formal successor delivers one native decision; not formal settlement."""
from copy import deepcopy
from dataclasses import replace
import asyncio
import importlib
import json
from threading import Event

import pytest

from src.runtime.execution_context import framework_owner_scope
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_wait import park, facts
from tests.test_controlled_case_approval_wait import clock
from tests.test_controlled_case_approval_submission import submit
from tests.test_controlled_case_approval_claim import implementation, claim_caps, claim, wire
from tests.test_controlled_case_approval_claim import begin


def delivery_caps():
    return replace(claim_caps(), controlled_case_approval_delivery_available=True)


async def successor(p, decision="approved"):
    wait = await park(p)
    native = next(item for item in p.outcome.graph_outcome.native_interrupts if item.tool_call_id == "B")
    chosen = next(entry for entry in wait.batch if entry.interrupt_id == native.interrupt_id)
    submitted = await submit(p, chosen.approval_id, decision)
    return wait, await claim(p, wire(p, submitted.id))


def receipt(p, approval_id):
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        return p.bridge.projection._journal.get_receipt_in_transaction(cur, approval_id)


async def execute(p, task, *, caps=None, adapter=None, build=None):
    module = importlib.import_module("src.application.runtime.controlled_case_executor")
    port = module.ControlledCaseExecutor(task_store=p.s.h.tasks, graph_adapter=adapter or p.h.adapter,
        approval_continuations=implementation(p), framework_capabilities=caps or delivery_caps())
    async def tools(prepared):
        state = deepcopy(prepared.runtime_preparation.initial_state)
        wrapped, _ = await p.bridge.runtime._build_deep_agent_business_tools(state=state, tool_records=p.bridge.records)
        return wrapped
    return await port.execute(task, build_business_tools=build or tools,
        resource_id="isolated-formal-decision", fencing_token=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_actual_successor_delivers_only_one_decision_and_preserves_the_unfinished_batch(pending_formal, decision):
    p = pending_formal
    wait, task = await successor(p, decision)
    previous = await p.s.h.tasks.get(p.h.task.id)
    ledger = await p.s.calls.snapshot(p.s.binding)
    original_detail = await p.s.d.runs.get_run(p.s.item.run_id)
    result = await execute(p, task)
    assert result.native_terminal is None and len(result.native_interrupts) == 1
    chosen = next(entry for entry in wait.batch if entry.approval_id == task.reference_id)
    other, = [entry for entry in wait.batch if entry.approval_id != task.reference_id]
    assert {item.interrupt_id for item in result.native_interrupts} == {other.interrupt_id}
    saved = receipt(p, chosen.approval_id)
    assert saved.state == "delivered" and saved.delivered_at is not None and saved.applied_at is None
    assert saved.proof["owner_epoch"] == task.runtime_owner.owner_epoch
    assert saved.proof["interrupt_id"] == chosen.interrupt_id and saved.proof["evidence_kind"] == "atomic_write"
    assert receipt(p, other.approval_id) is None
    assert len(p.effects) == (1 if decision == "approved" else 0)
    if p.effects:
        assert p.effects[0][0] == {"path": "b.py"} and p.effects[0][2] == chosen.tool_job_id
    assert (await p.bridge.jobs.get_job(other.tool_job_id)).status.value == "waiting_approval"
    assert await p.s.calls.snapshot(p.s.binding) == ledger and len(p.requests) == 1
    assert await p.s.h.tasks.get(p.h.task.id) == previous
    detail = await p.s.d.runs.get_run(p.s.item.run_id)
    assert {entry.id for entry in detail.attempts} == {entry.id for entry in original_detail.attempts} and detail.results == []
    current = next(entry for entry in detail.attempts if entry.id == p.s.attempt.id)
    original = next(entry for entry in original_detail.attempts if entry.id == p.s.attempt.id)
    assert current.checkpoint_payload == original.checkpoint_payload and current.checkpoint_version == original.checkpoint_version
    assert current.started_at == original.started_at and current.attempt_no == original.attempt_no
    assert (await p.s.h.tasks.get(task.id)).status == "running"


@pytest.mark.asyncio
async def test_running_delivery_has_no_implicit_second_execution(pending_formal):
    p = pending_formal
    _, task = await successor(p)
    await execute(p, task)
    before = facts(p)
    with pytest.raises(ValueError, match="claimed task"):
        await execute(p, task)
    assert facts(p) == before and len(p.effects) == 1 and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["delivery_disabled", "claim_disabled", "no_port", "serialized"])
async def test_disabled_or_missing_composition_never_commits_running_or_sends(pending_formal, mode):
    from src.application.runtime.controlled_case_executor import ControlledCaseExecutor

    p = pending_formal
    _, task = await successor(p)
    before = facts(p)
    async def forbidden(*args, **kwargs):
        pytest.fail("Unavailable delivery assembled tools or invoked a graph")
    if mode == "no_port":
        port = ControlledCaseExecutor(task_store=p.s.h.tasks, graph_adapter=p.h.adapter)
        with pytest.raises(ValueError, match="dedicated continuation port"):
            await port.execute(task, build_business_tools=forbidden, resource_id="isolated", fencing_token=2)
    elif mode == "serialized":
        captured = type(task).model_validate_json(task.model_dump_json())
        with pytest.raises(ValueError, match="captured case owner"):
            await execute(p, captured, build=forbidden)
    else:
        caps = claim_caps() if mode == "delivery_disabled" else replace(delivery_caps(), controlled_case_approval_claim_available=False)
        reason = "delivery_unavailable" if mode == "delivery_disabled" else "claim_unavailable"
        with pytest.raises(TaskDeferred, match=reason):
            await execute(p, task, caps=caps, build=forbidden)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_prepare_freezes_one_sending_intent_and_unknown_sending_cannot_be_replayed(pending_formal):
    p = pending_formal
    wait, task = await successor(p, "denied")
    await begin(p, task)
    before = native_originals(p), await p.s.calls.snapshot(p.s.binding)
    plan = await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert plan.receipt.state == "sending" and plan.resume == {plan.link.interrupt_id: plan.receipt.decision}
    assert not plan.continue_from_checkpoint and plan.link.tool_call_id == "B"
    assert plan.job.started_at is None and plan.job.status.value == "waiting_approval"
    assert before == (native_originals(p), await p.s.calls.snapshot(p.s.binding))
    saved = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcile instead of replay"):
        await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert facts(p) == saved and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["receipt", "audit"])
async def test_each_delivery_admission_write_rolls_back_receipt_audit_and_all_originals(pending_formal, monkeypatch, step):
    from src.infrastructure.sqlalchemy_runtime import SQLAlchemyCursor

    p = pending_formal
    _, task = await successor(p)
    await begin(p, task)
    before, original, reached = facts(p), SQLAlchemyCursor.execute, False
    prefix = (f"UPDATE {FrameworkApprovalJournal(p.h.settings)._receipts} SET state='sending'" if step == "receipt"
        else f"INSERT INTO {p.h.settings.database.postgres_event_table}")
    def execute_sql(self, statement, parameters=None):
        nonlocal reached
        result = original(self, statement, parameters)
        if isinstance(statement, str) and " ".join(statement.split()).startswith(prefix):
            reached = True
            raise RuntimeError("isolated sending admission rollback")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(SQLAlchemyCursor, "execute", execute_sql)
        with pytest.raises(RuntimeError, match="sending admission rollback"):
            await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert reached and facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reply_loss", "cancel_waiter"])
async def test_committed_sending_with_unknown_receipt_never_invokes_or_auto_replays(pending_formal, monkeypatch, failure, caplog):
    p = pending_formal
    _, task = await successor(p)
    service = implementation(p)
    entered, release, finished = Event(), Event(), Event()
    original, original_event = type(service)._prepare_delivery, p.s.h.tasks._event
    def event(cur, row, kind, payload):
        original_event(cur, row, kind, payload)
        if kind == "test_run.case_approval_sending" and failure == "cancel_waiter":
            entered.set()
            assert release.wait(30)
    def commit(self, *args):
        try:
            result = original(self, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated committed sending reply loss")
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_prepare_delivery", commit)
        patch.setattr(p.s.h.tasks, "_event", event)
        waiter = asyncio.create_task(execute(p, task))
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
            with pytest.raises(RuntimeError, match="sending reply loss"):
                await waiter
    assert receipt(p, task.reference_id).state == "sending"
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcile instead of replay"):
        await service.prepare_delivery(task, capabilities=delivery_caps())
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []
    assert any(getattr(record, "automatic_replay", None) is False
        and getattr(record, "recovery_reason", None) == "approval_owner_receipt_unknown" for record in caplog.records)


@pytest.mark.asyncio
async def test_native_resume_write_and_delivery_receipt_rollback_together_in_real_fenced_saver(pending_formal, monkeypatch):
    p = pending_formal
    _, task = await successor(p, "denied")
    await begin(p, task)
    plan = await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    config = {"configurable": {"thread_id": p.s.binding.thread_id, "checkpoint_ns": plan.link.checkpoint_namespace,
        "checkpoint_id": plan.link.checkpoint_id}}
    before = facts(p)
    original, reached = FrameworkApprovalJournal._save_proof, False
    def fail(self, *args):
        nonlocal reached
        original(self, *args)
        reached = True
        raise RuntimeError("isolated atomic native delivery rollback")
    with framework_owner_scope(task.runtime_owner):
        async with p.h.provider.open() as saver:
            with monkeypatch.context() as patch:
                patch.setattr(FrameworkApprovalJournal, "_save_proof", fail)
                with pytest.raises(RuntimeError, match="atomic native delivery rollback"):
                    await saver.aput_writes(config, [("__resume__", [plan.receipt.decision])], plan.link.graph_task_id, plan.link.task_path)
            assert reached and facts(p) == before
            await saver.aput_writes(config, [("__resume__", [plan.receipt.decision])], plan.link.graph_task_id, plan.link.task_path)
    assert receipt(p, task.reference_id).state == "delivered"
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,projection", [(decision, projection) for decision in ("approved", "denied")
    for projection in ("present", "missing")])
async def test_consumed_decision_reconciles_native_fact_without_reinvoking_or_claiming(pending_formal, decision, projection):
    p = pending_formal
    _, task = await successor(p, decision)
    await execute(p, task)
    saved = receipt(p, task.reference_id)
    if projection == "missing":
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET state='sending',proof=NULL,delivered_at=NULL WHERE approval_id=%s",
                (task.reference_id,))
    native, budget, effects = native_originals(p), await p.s.calls.snapshot(p.s.binding), list(p.effects)
    result = await implementation(p).reconcile_delivery(task, capabilities=delivery_caps())
    assert result.receipt.state == "delivered" and result.continue_from_checkpoint and result.resume is None
    assert result.receipt.applied_at is None and result.receipt.decision_id == saved.decision_id
    assert result.receipt.proof["evidence_kind"] == ("reconciled" if projection == "missing" else "atomic_write")
    before = facts(p)
    assert await implementation(p).reconcile_delivery(task, capabilities=delivery_caps()) == result
    assert facts(p) == before and native_originals(p) == native and await p.s.calls.snapshot(p.s.binding) == budget
    assert p.effects == effects and len(p.requests) == 1
    events = await p.h.store.list_events(p.s.session.id)
    assert len([event for event in events if event.type == "approval.decision_delivered"]) == 1
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcile instead of replay"):
        await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert facts(p) == before


@pytest.mark.asyncio
async def test_unconsumed_sending_cannot_be_reconciled_into_delivery_permission(pending_formal):
    p = pending_formal
    _, task = await successor(p)
    await begin(p, task)
    await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="durably consumed"):
        await implementation(p).reconcile_delivery(task, capabilities=delivery_caps())
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["prepare_delivery", "reconcile_delivery"])
@pytest.mark.parametrize("field", ["project_id", "trace_id", "request_id", "root_trace_id", "parent_task_id",
    "reference_id", "priority", "input", "operation", "runtime_owner"])
async def test_delivery_ports_require_exact_captured_identity(pending_formal, operation, field):
    p = pending_formal
    _, task = await successor(p)
    await begin(p, task)
    changed = task.model_copy(update={field: None if field == "runtime_owner" else {} if field == "input"
        else task.priority + 1 if field == "priority" else "foreign"})
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="captured"):
        await getattr(implementation(p), operation)(changed, capabilities=delivery_caps())
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["prepare_delivery", "reconcile_delivery"])
@pytest.mark.parametrize("lost", ["formal_expiry", "task_expiry", "session_expiry", "epoch", "formal_token", "stripped"])
async def test_each_delivery_port_requires_both_live_capabilities(pending_formal, operation, lost):
    p = pending_formal
    _, task = await successor(p)
    await begin(p, task)
    owner = task.runtime_owner
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if lost == "formal_expiry":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "'\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif lost == "task_expiry":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif lost == "session_expiry":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{turn_lease_expires_at}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.session.id,))
        elif lost == "epoch":
            owner = owner.model_copy(update={"owner_epoch": owner.owner_epoch - 1})
        elif lost == "formal_token":
            owner = owner.model_copy(update={"controlled_case_lease": owner.controlled_case_lease.model_copy(update={"lease_token": "foreign"})})
        else:
            owner = owner.model_copy(update={"controlled_case_lease": None})
    captured = task.model_copy(update={"runtime_owner": owner})
    before = facts(p)
    error = FrameworkApprovalRecoveryRequired if lost == "stripped" else ContinuationLeaseLostError
    with pytest.raises(error):
        await getattr(implementation(p), operation)(captured, capabilities=delivery_caps())
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["parent", "input_hash", "origin_outbox", "successor_outbox", "pending_batch",
    "control", "interrupt", "job_started", "native_source", "ledger", "run_cancelled"])
async def test_first_send_rechecks_current_originals_after_running(pending_formal, drift):
    from src.runtime.postgres_agent_task_store import AgentSubmissionConflict

    p = pending_formal
    wait, task = await successor(p)
    await begin(p, task)
    if drift == "run_cancelled":
        await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated cancellation", clock(p))
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "parent":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET parent_task_id=NULL WHERE id=%s", (task.id,))
        elif drift == "input_hash":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_hash=%s WHERE id=%s", ("0" * 64, task.id))
        elif drift.endswith("outbox"):
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                ("agent_turn:" + (p.h.task.id if drift == "origin_outbox" else task.id),))
        elif drift == "pending_batch":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{pending_turn,pending_approval_ids}',%s::jsonb) WHERE id=%s",
                (json.dumps([wait.batch[0].approval_id]), p.s.session.id))
        elif drift == "control":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{control}','{\"control_state\":\"cancelled\"}'::jsonb) WHERE id=%s", (p.s.session.id,))
        elif drift == "interrupt":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET interrupt_requested=true WHERE id=%s", (task.id,))
        elif drift == "job_started":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET started_at=clock_timestamp() WHERE id=%s",
                (next(entry.tool_job_id for entry in wait.batch if entry.approval_id == task.reference_id),))
        elif drift == "native_source":
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoints SET "
                "metadata=jsonb_set(metadata,'{source}','\"update\"') WHERE thread_id=%s", (p.s.binding.thread_id,))
        elif drift == "ledger":
            cur.execute(f"SELECT record FROM {p.s.calls.calls_table} WHERE binding_id=%s", (p.s.binding.binding_id,))
            later = deepcopy(cur.fetchone()["record"])
            later["intent"]["attempt_id"], later["intent"]["logical_call_id"] = "isolated-later-attempt", "isolated-later-call"
            later["created_at"] = clock(p).isoformat()
            cur.execute(f"INSERT INTO {p.s.calls.calls_table}(binding_id,attempt_id,record) VALUES (%s,%s,%s::jsonb)",
                (p.s.binding.binding_id, later["intent"]["attempt_id"], json.dumps(later)))
    before = facts(p)
    error = (ContinuationLeaseLostError if drift in {"parent", "input_hash", "run_cancelled"}
        else AgentSubmissionConflict if drift == "control"
        else FrameworkApprovalRecoveryRequired)
    with pytest.raises(error):
        await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["prepare_delivery", "reconcile_delivery"])
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_final_delivery_projection_wait_crossing_each_lease_rolls_back(pending_formal, monkeypatch, operation, lease):
    p = pending_formal
    _, task = await successor(p, "denied")
    if operation == "reconcile_delivery":
        await execute(p, task)
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET state='sending',proof=NULL,delivered_at=NULL WHERE approval_id=%s",
                (task.reference_id,))
    else:
        await begin(p, task)
    before, reached = facts(p), False
    original_event = p.s.h.tasks._event
    original_append = p.h.store.append_event_in_transaction
    def expire(cur):
        nonlocal reached
        reached = True
        if lease == "session":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()+INTERVAL '0.05 seconds')) WHERE id=%s", (p.s.session.id,))
        elif lease == "owner":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at=clock_timestamp()+INTERVAL '0.05 seconds' WHERE binding_id=%s",
                (p.s.binding.binding_id,))
        else:
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "to_jsonb(clock_timestamp()+INTERVAL '0.05 seconds')) WHERE id=%s", (p.s.item.id,))
        cur.execute("SELECT pg_sleep(0.1)")
    def event(cur, row, kind, payload):
        result = original_event(cur, row, kind, payload)
        if kind == "test_run.case_approval_sending":
            expire(cur)
        return result
    def append(cur, session_id, value, **kwargs):
        result = original_append(cur, session_id, value, **kwargs)
        if value.type == "approval.decision_delivered":
            expire(cur)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(p.s.h.tasks, "_event", event)
        if operation == "reconcile_delivery":
            patch.setattr(p.h.store, "append_event_in_transaction", append)
        with pytest.raises(ContinuationLeaseLostError):
            await getattr(implementation(p), operation)(task, capabilities=delivery_caps())
    assert reached and facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"deadline_seconds": 20}], indirect=True)
@pytest.mark.parametrize("window", ["before", "last_write"])
async def test_first_send_cannot_extend_frozen_root_deadline(pending_formal, monkeypatch, window):
    p = pending_formal
    _, task = await successor(p)
    await begin(p, task)
    deadline = p.s.budget.deadline_at
    assert clock(p) < deadline
    before, original, reached = facts(p), p.s.h.tasks._event, False
    def wait(cur):
        cur.execute("SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM (%s::timestamptz-clock_timestamp())))+0.05)", (deadline,))
    def event(cur, row, kind, payload):
        nonlocal reached
        original(cur, row, kind, payload)
        if kind == "test_run.case_approval_sending":
            reached = True
            wait(cur)
    if window == "before":
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            wait(cur)
    with monkeypatch.context() as patch:
        if window == "last_write":
            patch.setattr(p.s.h.tasks, "_event", event)
        with pytest.raises(ValueError, match="deadline has expired"):
            await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert reached == (window == "last_write") and facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["proof", "native_resume", "unknown_job", "decision"])
async def test_reconciliation_never_repairs_conflicting_native_or_business_evidence(pending_formal, drift):
    p = pending_formal
    _, task = await successor(p)
    await execute(p, task)
    saved = receipt(p, task.reference_id)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "proof":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET proof=jsonb_set(proof,'{{interrupt_id}}','\"foreign\"') WHERE approval_id=%s",
                (task.reference_id,))
        elif drift == "native_resume":
            cur.execute(f"DELETE FROM {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                "WHERE thread_id=%s AND task_id=%s AND channel='__resume__'",
                (p.s.binding.thread_id, saved.proof["graph_task_id"]))
        elif drift == "unknown_job":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET status='running' WHERE id="
                f"(SELECT link->>'tool_job_id' FROM {p.s.h.tasks._tasks}_interrupt WHERE approval_id=%s)", (task.reference_id,))
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET decision='{{\"type\":\"reject\"}}'::jsonb WHERE approval_id=%s",
                (task.reference_id,))
    before, effects = facts(p), list(p.effects)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await implementation(p).reconcile_delivery(task, capabilities=delivery_caps())
    assert facts(p) == before and p.effects == effects and len(p.requests) == 1


@pytest.mark.asyncio
async def test_current_permission_revocation_is_checked_at_the_governed_execution_boundary(pending_formal, monkeypatch):
    p = pending_formal
    wait, task = await successor(p)
    original = p.h.adapter.execute_frozen
    async def invoke(*args, **kwargs):
        p.bridge.runtime._tool_registry.get("inspect_source").permission_level = "restricted"
        return await original(*args, **kwargs)
    monkeypatch.setattr(p.h.adapter, "execute_frozen", invoke)
    result = await execute(p, task)
    chosen = next(entry for entry in wait.batch if entry.approval_id == task.reference_id)
    job = await p.bridge.jobs.get_job(chosen.tool_job_id)
    assert job.status.value == "denied" and job.started_at is None and job.output_payload["error"] == "permission_denied"
    assert receipt(p, task.reference_id).state == "delivered" and receipt(p, task.reference_id).applied_at is None
    assert result.native_terminal is None and len(result.native_interrupts) == 1
    assert p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["receipt", "audit"])
async def test_reconciliation_projection_and_audit_are_one_transaction(pending_formal, monkeypatch, step):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET state='sending',proof=NULL,delivered_at=NULL WHERE approval_id=%s",
            (task.reference_id,))
    before, reached = facts(p), False
    original_save = FrameworkApprovalJournal._save_proof
    original_append = p.h.store.append_event_in_transaction
    def save(self, *args):
        nonlocal reached
        original_save(self, *args)
        reached = True
        raise RuntimeError("isolated delivery reconciliation rollback")
    def append(cur, session_id, event, **kwargs):
        nonlocal reached
        result = original_append(cur, session_id, event, **kwargs)
        if event.type == "approval.decision_delivered":
            reached = True
            raise RuntimeError("isolated delivery reconciliation rollback")
        return result
    with monkeypatch.context() as patch:
        if step == "receipt":
            patch.setattr(FrameworkApprovalJournal, "_save_proof", save)
        else:
            patch.setattr(p.h.store, "append_event_in_transaction", append)
        with pytest.raises(RuntimeError, match="reconciliation rollback"):
            await implementation(p).reconcile_delivery(task, capabilities=delivery_caps())
    assert reached and facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reply_loss", "cancel_waiter"])
async def test_unknown_reconciliation_reply_is_safe_to_observe_without_execution(pending_formal, monkeypatch, failure):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    service = implementation(p)
    entered, release, finished = Event(), Event(), Event()
    original, original_append = type(service)._reconcile_delivery, p.h.store.append_event_in_transaction
    def append(cur, session_id, event, **kwargs):
        result = original_append(cur, session_id, event, **kwargs)
        if event.type == "approval.decision_delivered" and failure == "cancel_waiter":
            entered.set()
            assert release.wait(30)
        return result
    def commit(self, *args):
        try:
            result = original(self, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated committed reconciliation reply loss")
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_reconcile_delivery", commit)
        patch.setattr(p.h.store, "append_event_in_transaction", append)
        waiter = asyncio.create_task(service.reconcile_delivery(task, capabilities=delivery_caps()))
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
            with pytest.raises(RuntimeError, match="reconciliation reply loss"):
                await waiter
    before = facts(p)
    observed = await service.reconcile_delivery(task, capabilities=delivery_caps())
    assert observed.receipt.state == "delivered" and observed.resume is None
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"deadline_seconds": 20}], indirect=True)
async def test_historical_delivery_after_budget_expiry_and_pending_change_is_not_new_execution(pending_formal):
    p = pending_formal
    wait, task = await successor(p, "denied")
    await execute(p, task)
    other, = [entry for entry in wait.batch if entry.approval_id != task.reference_id]
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
            "'{pending_turn,pending_approval_ids}',%s::jsonb) WHERE id=%s", (json.dumps([other.approval_id]), p.s.session.id))
        cur.execute("SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM (%s::timestamptz-clock_timestamp())))+0.05)",
            (p.s.budget.deadline_at,))
    assert clock(p) > p.s.budget.deadline_at
    native, budget = native_originals(p), await p.s.calls.snapshot(p.s.binding)
    observed = await implementation(p).reconcile_delivery(task, capabilities=delivery_caps())
    assert observed.receipt.state == "delivered" and observed.resume is None and observed.receipt.applied_at is None
    assert native_originals(p) == native and await p.s.calls.snapshot(p.s.binding) == budget
    assert p.effects == [] and len(p.requests) == 1
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcile instead of replay"):
        await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    assert facts(p) == before


@pytest.mark.asyncio
async def test_original_application_runtime_cannot_misroute_successor_to_initial_finalizer(pending_formal):
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime

    p = pending_formal
    _, task = await successor(p)
    before = facts(p)
    with pytest.raises(ValueError):
        await ControlledCaseRuntime(runtime_service=p.bridge.runtime).execute(task, executor=object(),
            resource_id="isolated-no-initial-finalizer", fencing_token=2)
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


def test_artifact_compatibility_and_claim_capability_do_not_enable_delivery():
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities

    assert not FrameworkWorkerCapabilities.from_artifact(deepagents_available=True).controlled_case_approval_delivery_available
    assert not claim_caps().controlled_case_approval_delivery_available

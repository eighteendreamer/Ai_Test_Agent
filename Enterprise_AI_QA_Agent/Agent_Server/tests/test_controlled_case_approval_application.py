"""A native tool outcome can be applied without finalizing a formal case."""
import asyncio
from threading import Event

import pytest

from src.application.runtime.tool_messages import build_rejected_tool_projection
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_approval_continuation import FrameworkApprovalContinuation
from src.runtime.framework_approval_journal import FrameworkApprovalJournal
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_delivery import successor, execute, delivery_caps, receipt
from tests.test_controlled_case_approval_claim import implementation, begin
from tests.test_controlled_case_approval_wait import facts
from tests.test_controlled_case_approval_wait import clock


def refusal(p, task, *, observed=None):
    # Explicit pure display policy for this single-material fixture. The
    # initial formal wait has no persisted application tool slots; this is NOT
    # evidence of their original order or permission to reconstruct full state.
    native = next(item for item in p.outcome.graph_outcome.native_interrupts if item.tool_call_id == "B")
    hygiene = p.bridge.runtime._transcript_hygiene_service.tool_message_metadata()
    def project(job, message):
        if observed is not None:
            observed.append(job.id)
        return build_rejected_tool_projection(binding=task.runtime_binding, source=native, job=job,
            approval_id=task.reference_id, ordinal=1, hygiene_metadata=hygiene,
            model_content=message.content, include_runtime_message=True)
    return project


async def apply(p, task, *, projector=None):
    return await implementation(p).apply_decision(task, capabilities=delivery_caps(), rejection_projector=projector)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_native_outcome_applies_once_without_graph_or_formal_settlement(pending_formal, decision):
    p = pending_formal
    wait, task = await successor(p, decision)
    await execute(p, task)
    original_receipt = receipt(p, task.reference_id)
    original_run = await p.s.d.runs.get_run(p.s.item.run_id)
    original_task = await p.s.h.tasks.get(task.id)
    original_producer = await p.s.h.tasks.get(p.h.task.id)
    native, budget, effects = native_originals(p), await p.s.calls.snapshot(p.s.binding), list(p.effects)
    calls = []
    result = await apply(p, task, projector=refusal(p, task, observed=calls) if decision == "denied" else None)
    assert result.receipt.state == "delivered" and result.receipt.applied_at is not None
    assert result.job.status.value == ("completed" if decision == "approved" else "denied")
    assert result.receipt.delivered_at == original_receipt.delivered_at
    assert {k: v for k, v in result.receipt.proof.items() if k != "application"} == original_receipt.proof
    evidence = result.receipt.proof["application"]
    assert evidence["decision_hash"] == original_receipt.decision_hash and evidence["tool_job_id"] == result.job.id
    assert evidence["tool_output_hash"] == approval_value_hash(result.job.output_payload)
    assert evidence["outcome"] == ("tool_result" if decision == "approved" else "human_rejected")
    if decision == "denied":
        assert calls == [result.job.id] and result.job.started_at is None and result.job.completed_at is not None
        assert TOOL_PROJECTION_KEY in evidence and result.job.output_payload["instruction_authority"] == "none"
    material = await implementation(p).read_tool_projection(task, capabilities=delivery_caps())
    assert material.record.job_id == result.job.id and material.record.approval_id == task.reference_id
    assert material.record.status == result.job.status.value and material.schema_version == 2
    assert material.runtime_message["tool_call_id"] == material.record.call_id
    before = facts(p)
    assert await apply(p, task) == result
    assert await implementation(p).read_tool_projection(task, capabilities=delivery_caps()) == material
    assert facts(p) == before
    assert native_originals(p) == native and await p.s.calls.snapshot(p.s.binding) == budget
    assert p.effects == effects and len(p.requests) == 1
    assert await p.s.d.runs.get_run(p.s.item.run_id) == original_run
    assert await p.s.h.tasks.get(task.id) == original_task and await p.s.h.tasks.get(p.h.task.id) == original_producer
    other, = [entry for entry in wait.batch if entry.approval_id != task.reference_id]
    assert receipt(p, other.approval_id) is None and (await p.bridge.jobs.get_job(other.tool_job_id)).status.value == "waiting_approval"
    events = await p.h.store.list_events(p.s.session.id)
    assert len([event for event in events if event.type == "approval.decision_delivered"]) == 1
    applied, = [event for event in events if event.type == "approval.decision_applied"]
    assert applied.payload["task_id"] == task.id and applied.attempt_id == task.attempt_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["queued", "sending"])
async def test_application_cannot_turn_unconsumed_intent_into_an_outcome(pending_formal, state):
    p = pending_formal
    _, task = await successor(p, "denied")
    await begin(p, task)
    if state == "sending":
        await implementation(p).prepare_delivery(task, capabilities=delivery_caps())
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="durably consumed"):
        await apply(p, task, projector=lambda *args: pytest.fail("No result to project"))
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
async def test_refusal_requires_explicit_pure_policy_and_never_regenerates_applied_material(pending_formal):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="explicit pure application projector"):
        await apply(p, task)
    assert facts(p) == before
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="applied approval"):
        await implementation(p).read_tool_projection(task, capabilities=delivery_caps())
    assert facts(p) == before
    result = await apply(p, task, projector=refusal(p, task))
    before = facts(p)
    assert await apply(p, task, projector=lambda *args: pytest.fail("Applied policy was regenerated")) == result
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["binding_fingerprint", "job_id", "status", "summary", "output", "started_at", "completed_at", "native_content"])
async def test_refusal_projector_cannot_invent_conflicting_job_or_native_facts(pending_formal, field):
    from src.schemas.framework_tool_projection import parse_tool_projection

    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    original = refusal(p, task)
    def project(job, native):
        value = original(job, native).model_dump(mode="json")
        if field == "binding_fingerprint":
            value[field] = "foreign"
        elif field == "native_content":
            value["model_content"] = "foreign"
        else:
            key = {"job_id": "job_id", "status": "status", "summary": "summary", "output": "output",
                "started_at": "started_at", "completed_at": "completed_at"}[field]
            value["record"][key] = ({} if field == "output" else None if field == "completed_at"
                else "2000-01-01T00:00:00" if field == "started_at" else "completed" if field == "status" else "foreign")
        value["sha256"] = approval_value_hash({key: item for key, item in value.items() if key != "sha256"})
        return parse_tool_projection(value)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="rejection projection"):
        await apply(p, task, projector=project)
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_duplicate_application_serializes_one_business_projection(pending_formal, decision):
    p = pending_formal
    _, task = await successor(p, decision)
    await execute(p, task)
    calls = []
    policy = refusal(p, task, observed=calls) if decision == "denied" else None
    results = await asyncio.gather(*(apply(p, task, projector=policy) for _ in range(3)))
    assert results[0] == results[1] == results[2]
    assert len(calls) == (1 if decision == "denied" else 0)
    assert len(p.effects) == (1 if decision == "approved" else 0) and len(p.requests) == 1
    events = await p.h.store.list_events(p.s.session.id)
    assert len([event for event in events if event.type == "approval.decision_applied"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["delivery_audit", "job", "application", "applied_audit"])
async def test_each_application_write_rolls_back_delivery_job_material_and_audit(pending_formal, monkeypatch, step):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET state='sending',proof=NULL,delivered_at=NULL WHERE approval_id=%s",
            (task.reference_id,))
    before, reached = facts(p), False
    original_append = p.h.store.append_event_in_transaction
    original_job = p.bridge.jobs.save_job_in_transaction
    original_apply = FrameworkApprovalJournal.record_application_in_transaction
    def append(cur, session_id, event, **kwargs):
        nonlocal reached
        result = original_append(cur, session_id, event, **kwargs)
        if event.type == ("approval.decision_delivered" if step == "delivery_audit" else "approval.decision_applied"):
            reached = True
            raise RuntimeError("isolated application write rollback")
        return result
    def job(*args, **kwargs):
        nonlocal reached
        result = original_job(*args, **kwargs)
        reached = True
        raise RuntimeError("isolated application write rollback")
    def application(self, *args):
        nonlocal reached
        result = original_apply(self, *args)
        reached = True
        raise RuntimeError("isolated application write rollback")
    with monkeypatch.context() as patch:
        if step in {"delivery_audit", "applied_audit"}:
            patch.setattr(p.h.store, "append_event_in_transaction", append)
        elif step == "job":
            patch.setattr(p.bridge.jobs, "save_job_in_transaction", job)
        else:
            patch.setattr(FrameworkApprovalJournal, "record_application_in_transaction", application)
        with pytest.raises(RuntimeError, match="application write rollback"):
            await apply(p, task, projector=refusal(p, task))
    assert reached and facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_applied_audit_wait_crossing_either_capability_rolls_back(pending_formal, monkeypatch, lease):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    before, original, reached = facts(p), p.h.store.append_event_in_transaction, False
    def append(cur, session_id, event, **kwargs):
        nonlocal reached
        result = original(cur, session_id, event, **kwargs)
        if event.type == "approval.decision_applied":
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
        return result
    with monkeypatch.context() as patch:
        patch.setattr(p.h.store, "append_event_in_transaction", append)
        with pytest.raises(ContinuationLeaseLostError):
            await apply(p, task, projector=refusal(p, task))
    assert reached and facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reply_loss", "cancel_waiter"])
async def test_committed_application_with_unknown_reply_never_reexecutes_or_reprojects(pending_formal, monkeypatch, failure, caplog):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    service, calls = implementation(p), []
    entered, release, finished = Event(), Event(), Event()
    original, original_append = type(service)._apply_decision, p.h.store.append_event_in_transaction
    def append(cur, session_id, event, **kwargs):
        result = original_append(cur, session_id, event, **kwargs)
        if event.type == "approval.decision_applied" and failure == "cancel_waiter":
            entered.set()
            assert release.wait(30)
        return result
    def commit(self, *args):
        try:
            result = original(self, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated committed application reply loss")
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_apply_decision", commit)
        patch.setattr(p.h.store, "append_event_in_transaction", append)
        waiter = asyncio.create_task(apply(p, task, projector=refusal(p, task, observed=calls)))
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
            with pytest.raises(RuntimeError, match="application reply loss"):
                await waiter
    before = facts(p)
    result = await apply(p, task, projector=lambda *args: pytest.fail("Committed original was regenerated"))
    assert result.receipt.applied_at is not None and calls == [result.job.id]
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1
    assert any(getattr(record, "automatic_replay", None) is False
        and getattr(record, "recovery_reason", None) == "approval_owner_receipt_unknown" for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply_decision", "read_tool_projection"])
@pytest.mark.parametrize("field", ["input", "trace_id", "project_id", "parent_task_id", "reference_id", "runtime_owner"])
async def test_application_and_material_reader_require_exact_captured_task(pending_formal, operation, field):
    p = pending_formal
    _, task = await successor(p)
    await execute(p, task)
    captured = task.model_copy(update={field: None if field == "runtime_owner" else {} if field == "input" else "foreign"})
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="captured"):
        await getattr(implementation(p), operation)(captured, capabilities=delivery_caps())
    assert facts(p) == before and len(p.effects) == 1 and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply_decision", "read_tool_projection"])
@pytest.mark.parametrize("lost", ["formal_expiry", "task_expiry", "epoch", "stripped", "disabled"])
async def test_application_and_material_reader_never_use_expired_or_unavailable_authority(pending_formal, operation, lost):
    from dataclasses import replace

    p = pending_formal
    _, task = await successor(p)
    await execute(p, task)
    owner, caps = task.runtime_owner, delivery_caps()
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if lost == "formal_expiry":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "'\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif lost == "task_expiry":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif lost == "epoch":
            owner = owner.model_copy(update={"owner_epoch": owner.owner_epoch - 1})
        elif lost == "stripped":
            owner = owner.model_copy(update={"controlled_case_lease": None})
        else:
            caps = replace(caps, controlled_case_approval_delivery_available=False)
    before = facts(p)
    error = TaskDeferred if lost == "disabled" else FrameworkApprovalRecoveryRequired if lost == "stripped" else ContinuationLeaseLostError
    with pytest.raises(error):
        await getattr(implementation(p), operation)(task.model_copy(update={"runtime_owner": owner}), capabilities=caps)
    assert facts(p) == before and len(p.effects) == 1 and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize("drift", ["missing_result", "native_error", "unknown_job", "decision", "delivery_proof"])
async def test_application_rejects_missing_or_conflicting_consumed_outcome_without_repair(pending_formal, decision, drift):
    p = pending_formal
    _, task = await successor(p, decision)
    await execute(p, task)
    saved = receipt(p, task.reference_id)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "missing_result":
            cur.execute(f"DELETE FROM {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                "WHERE thread_id=%s AND task_id=%s AND channel='messages'", (p.s.binding.thread_id, saved.proof["graph_task_id"]))
        elif drift == "native_error":
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes SET channel='__error__' "
                "WHERE thread_id=%s AND task_id=%s AND channel='messages'", (p.s.binding.thread_id, saved.proof["graph_task_id"]))
        elif drift == "unknown_job":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET status='running',started_at=clock_timestamp() WHERE id="
                f"(SELECT link->>'tool_job_id' FROM {p.s.h.tasks._tasks}_interrupt WHERE approval_id=%s)", (task.reference_id,))
        elif drift == "decision":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET decision='{{\"type\":\"foreign\"}}'::jsonb WHERE approval_id=%s", (task.reference_id,))
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET proof=jsonb_set(proof,'{{interrupt_id}}','\"foreign\"') WHERE approval_id=%s", (task.reference_id,))
    before, effects = facts(p), list(p.effects)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await apply(p, task, projector=refusal(p, task) if decision == "denied" else None)
    assert facts(p) == before and p.effects == effects and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize("drift", ["applied_proof", "job_output", "missing_material"])
async def test_applied_originals_cannot_be_recomputed_from_changed_material(pending_formal, decision, drift):
    p = pending_formal
    _, task = await successor(p, decision)
    await execute(p, task)
    result = await apply(p, task, projector=refusal(p, task) if decision == "denied" else None)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "applied_proof":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET proof=jsonb_set(proof,'{{application,tool_output_hash}}','\"foreign\"') WHERE approval_id=%s",
                (task.reference_id,))
        elif drift == "job_output":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET output_payload='{{\"changed\":true}}'::jsonb WHERE id=%s", (result.job.id,))
        elif decision == "denied":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET proof=proof #- '{{application,{TOOL_PROJECTION_KEY}}}' WHERE approval_id=%s", (task.reference_id,))
        else:
            cur.execute(f"DELETE FROM {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                "WHERE thread_id=%s AND task_id=%s AND channel='messages'", (p.s.binding.thread_id, result.link.graph_task_id))
    before, effects = facts(p), list(p.effects)
    for operation in (lambda: apply(p, task, projector=refusal(p, task) if decision == "denied" else None),
        lambda: implementation(p).read_tool_projection(task, capabilities=delivery_caps())):
        with pytest.raises(FrameworkApprovalRecoveryRequired):
            await operation()
        assert facts(p) == before
    assert p.effects == effects and len(p.requests) == 1


@pytest.mark.asyncio
async def test_current_governance_denial_applies_as_nonexecution_not_a_successful_effect(pending_formal, monkeypatch):
    p = pending_formal
    _, task = await successor(p)
    original = p.h.adapter.execute_frozen
    async def invoke(*args, **kwargs):
        p.bridge.runtime._tool_registry.get("inspect_source").permission_level = "restricted"
        return await original(*args, **kwargs)
    monkeypatch.setattr(p.h.adapter, "execute_frozen", invoke)
    await execute(p, task)
    result = await apply(p, task)
    material = await implementation(p).read_tool_projection(task, capabilities=delivery_caps())
    assert result.receipt.proof["application"]["outcome"] == "governance_denied"
    assert material.record.status == result.job.status.value == "denied" and result.job.started_at is None
    assert p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"deadline_seconds": 20}], indirect=True)
async def test_known_native_outcome_closes_after_deadline_without_extending_budget_or_reexecuting(pending_formal):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM (%s::timestamptz-clock_timestamp())))+0.05)", (p.s.budget.deadline_at,))
    assert clock(p) > p.s.budget.deadline_at
    native, budget = native_originals(p), await p.s.calls.snapshot(p.s.binding)
    result = await apply(p, task, projector=refusal(p, task))
    assert result.receipt.applied_at is not None and result.job.status.value == "denied"
    assert native_originals(p) == native and await p.s.calls.snapshot(p.s.binding) == budget
    assert p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
async def test_formal_application_requires_readable_original_governed_material_before_commit(pending_formal):
    from tests.test_framework_approval_results import replace_native_message

    p = pending_formal
    _, task = await successor(p)
    await execute(p, task)
    plan = await implementation(p).reconcile_delivery(task, capabilities=delivery_caps())
    replace_native_message(p.h, plan, {"artifact": None})
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="projection"):
        await apply(p, task)
    assert facts(p) == before and receipt(p, task.reference_id).applied_at is None
    assert len(p.effects) == 1 and len(p.requests) == 1


@pytest.mark.asyncio
async def test_formal_application_reuses_the_same_connection_core_without_ordinary_task_impersonation(pending_formal, monkeypatch):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    shared = FrameworkApprovalContinuation(p.h.settings, sessions=p.h.store, jobs=p.bridge.jobs)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="owned task"):
        await shared.settle(task.runtime_owner, task.reference_id, receipt(p, task.reference_id).decision_id)
    assert facts(p) == before
    service, observed = implementation(p), []
    original = type(service)._delivery_originals
    original_apply = FrameworkApprovalContinuation.apply_decision_in_transaction
    def originals(self, cur, *args):
        result = original(self, cur, *args)
        cur.execute("SELECT pg_backend_pid() AS pid")
        observed.append(("formal_admission", cur.fetchone()["pid"]))
        return result
    def core(self, cur, owner, plan, **kwargs):
        cur.execute("SELECT pg_backend_pid() AS pid")
        observed.append(("shared_application", cur.fetchone()["pid"]))
        assert owner.task_id == task.id
        return original_apply(self, cur, owner, plan, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_delivery_originals", originals)
        patch.setattr(FrameworkApprovalContinuation, "apply_decision_in_transaction", core)
        result = await apply(p, task, projector=refusal(p, task))
    assert result.receipt.applied_at is not None
    assert [entry[0] for entry in observed] == ["formal_admission", "shared_application"]
    assert len({entry[1] for entry in observed}) == 1 and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply_decision", "read_tool_projection"])
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_original_material_read_crossing_a_capability_is_fenced_before_commit(pending_formal, monkeypatch, operation, lease):
    p = pending_formal
    _, task = await successor(p, "denied")
    await execute(p, task)
    if operation == "read_tool_projection":
        await apply(p, task, projector=refusal(p, task))
    before, reached = facts(p), False
    original = FrameworkApprovalContinuation._applied_tool_projection_in_transaction
    def read(self, cur, *args):
        nonlocal reached
        material = original(self, cur, *args)
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
        return material
    with monkeypatch.context() as patch:
        patch.setattr(FrameworkApprovalContinuation, "_applied_tool_projection_in_transaction", read)
        with pytest.raises(ContinuationLeaseLostError):
            if operation == "apply_decision":
                await apply(p, task, projector=refusal(p, task))
            else:
                await implementation(p).read_tool_projection(task, capabilities=delivery_caps())
    assert reached and facts(p) == before and p.effects == [] and len(p.requests) == 1

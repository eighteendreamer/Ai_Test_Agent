"""Accept another original decision while one formal sibling owns the graph."""
import asyncio
from threading import Event
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_runtime import owned, ports
from tests.test_controlled_case_approval_state import state_caps
from tests.test_controlled_case_approval_wait import facts, implementation as waits
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_claim import implementation as continuations, wire
from tests.test_controlled_case_approval_delivery import receipt


async def decide_a(p, asset, decision="approved"):
    return await submissions(p).submit(wire(p, p.h.task.id), asset.state["tool_results"][0]["approval_id"],
        ApprovalDecisionRequest(decision=decision), capabilities=state_caps())


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_other_decision_can_queue_during_real_tool_but_cannot_claim_or_advance_graph(pending_formal, monkeypatch, decision):
    p = pending_formal
    asset, b = await owned(p)
    a = asset.state["tool_results"][0]["approval_id"]
    runtime, worker = ports(p)
    entered, release = asyncio.Event(), asyncio.Event()
    handler = p.bridge.runtime._tool_runtime_service._handlers["inspect_source"]
    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await handler(*args, **kwargs)
    monkeypatch.setitem(p.bridge.runtime._tool_runtime_service._handlers, "inspect_source", blocked)
    waiter = asyncio.create_task(runtime.execute(b, executor=worker, resource_id="busy-formal-owner", fencing_token=2))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        before = await p.s.h.tasks.get(b.id), native_originals(p), await p.s.calls.snapshot(p.s.binding)
        successor = await submissions(p).submit(wire(p, p.h.task.id), a,
            ApprovalDecisionRequest(decision=decision), capabilities=state_caps())
        assert successor.status == "queued" and successor.attempt_id is None
        assert "runtime_owner" not in successor.model_dump(mode="json")
        assert successor.parent_task_id == p.h.task.id and successor.reference_id == a
        assert receipt(p, a).state == "queued" and receipt(p, a).delivered_at is receipt(p, a).applied_at is None
        assert before == (await p.s.h.tasks.get(b.id), native_originals(p), await p.s.calls.snapshot(p.s.binding))
        saved = facts(p), native_originals(p)
        with pytest.raises(TaskDeferred, match="already owned"):
            await continuations(p).claim(wire(p, successor.id), worker_id="no-parallel-owner", capabilities=state_caps())
        assert (facts(p), native_originals(p)) == saved
        assert len(p.requests) == 1 and p.effects == []
    finally:
        release.set()
        outcome = await asyncio.wait_for(waiter, 30)
    assert len(outcome.graph_outcome.native_interrupts) == 1 and outcome.graph_outcome.native_terminal is None
    assert (await p.s.h.tasks.get(successor.id)).status == "queued" and (await p.s.h.tasks.get(b.id)).status == "running"
    assert len(p.effects) == len(p.requests) == 1
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["claimed", "running", "sending", "applied"])
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_admission_is_independent_of_the_active_sibling_decision_phase(pending_formal, phase, decision):
    p = pending_formal
    asset, b = await owned(p, decision)
    service = continuations(p)
    if phase == "applied":
        runtime, worker = ports(p)
        await runtime.execute(b, executor=worker, resource_id="prior-sibling-result", fencing_token=2)
    elif phase != "claimed":
        await service.begin(b, resource_id="owned-before-native", fencing_token=2, capabilities=state_caps())
        if phase == "sending":
            await service.prepare_delivery(b, capabilities=state_caps())
    before = await p.s.h.tasks.get(b.id), native_originals(p), await p.s.d.runs.get_run(p.s.item.run_id)
    a = await decide_a(p, asset, "denied")
    assert a.status == "queued" and a.attempt_id is None
    assert await decide_a(p, asset, "denied") == a
    assert before == (await p.s.h.tasks.get(b.id), native_originals(p), await p.s.d.runs.get_run(p.s.item.run_id))
    saved = facts(p)
    with pytest.raises(ValueError, match="different decision"):
        await decide_a(p, asset, "approved")
    with pytest.raises(TaskDeferred, match="already owned"):
        await service.claim(wire(p, a.id), worker_id="blocked-sibling", capabilities=state_caps())
    assert facts(p) == saved and len(p.requests) == 1
    assert len(p.effects) == (phase == "applied" and decision == "approved")
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("active_decision", ["approved", "denied"])
@pytest.mark.parametrize("queued_decision", ["approved", "denied"])
async def test_queued_sibling_does_not_invalidate_the_existing_claimed_runtime(pending_formal, active_decision, queued_decision):
    p = pending_formal
    asset, b = await owned(p, active_decision)
    a = await decide_a(p, asset, queued_decision)
    runtime, worker = ports(p)
    ledger = await p.s.calls.snapshot(p.s.binding)
    result = await runtime.execute(b, executor=worker, resource_id="original-claim-after-sibling-admission", fencing_token=2)
    material = result.approval_tool_projection
    assert material.record.approval_id == b.reference_id and material.message.metadata["ordinal"] == 2
    assert material.record.status == ("completed" if active_decision == "approved" else "denied")
    assert result.state["tool_results"][0] == asset.state["tool_results"][0]
    assert result.state["tool_messages"][0] == asset.state["tool_messages"][0]
    assert result.state["tool_results"][1] == material.record.model_dump(mode="json")
    assert result.state["pending_approvals"] == [asset.state["pending_approvals"][0]]
    assert len(result.graph_outcome.native_interrupts) == 1 and result.graph_outcome.native_terminal is None
    assert result.settlement_receipt is result.approval_wait_receipt is None
    assert (await p.s.h.tasks.get(a.id)).status == "queued" and (await p.s.h.tasks.get(b.id)).status == "running"
    assert receipt(p, a.reference_id).state == "queued" and receipt(p, b.reference_id).state == "delivered"
    assert receipt(p, a.reference_id).applied_at is None and receipt(p, b.reference_id).applied_at is not None
    assert await p.s.calls.snapshot(p.s.binding) == ledger and len(p.requests) == 1
    assert len(p.effects) == (active_decision == "approved")
    before = facts(p), native_originals(p)
    with pytest.raises(TaskDeferred, match="already owned"):
        await continuations(p).claim(wire(p, a.id), worker_id="still-not-reparked", capabilities=state_caps())
    assert (facts(p), native_originals(p)) == before
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["project", "wait_hash", "missing_pending", "extra_pending", "selected_job_started",
    "owner_epoch", "owner_worker", "owner_task", "owner_expiry", "session_expiry", "formal_expiry",
    "active_input", "claim_event", "attempt_finished", "interrupt", "run_cancelled"])
async def test_historical_wait_does_not_authorize_a_foreign_or_expired_busy_owner(pending_formal, drift):
    from tests.test_controlled_case_approval_runtime import expire_lease

    p = pending_formal
    asset, b = await owned(p)
    a = asset.state["tool_results"][0]
    if drift == "run_cancelled":
        await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated busy admission cancellation", asset.retained_at)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "project":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET project_id=NULL WHERE id=%s", (p.s.session.id,))
        elif drift in {"wait_hash", "missing_pending", "extra_pending"}:
            path, value = {"wait_hash": ("{pending_turn,controlled_case_wait_sha256}", '"foreign"'),
                "missing_pending": ("{pending_turn,pending_approval_ids}", "[]"),
                "extra_pending": ("{pending_turn,pending_approval_ids}", '["foreign"]')}[drift]
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,%s,%s::jsonb) WHERE id=%s",
                (path, value, p.s.session.id))
        elif drift == "selected_job_started":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET started_at=clock_timestamp() WHERE id=%s",
                (a["job_id"],))
        elif drift == "owner_epoch":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET owner_epoch=owner_epoch+1 WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif drift in {"owner_worker", "owner_task"}:
            column, value = ("worker_id", "foreign") if drift == "owner_worker" else ("task_id", p.h.task.id)
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET {column}=%s WHERE binding_id=%s", (value, p.s.binding.binding_id))
        elif drift in {"owner_expiry", "session_expiry", "formal_expiry"}:
            expire_lease(p, cur, drift.split("_")[0])
        elif drift == "active_input":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_payload='{{}}'::jsonb WHERE id=%s", (b.id,))
        elif drift == "claim_event":
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_event_table} WHERE type='agent_task.claimed' AND payload->>'task_id'=%s", (b.id,))
        elif drift == "attempt_finished":
            cur.execute(f"UPDATE {p.s.h.tasks._attempts} SET finished_at=clock_timestamp() WHERE id=%s", (b.attempt_id,))
        elif drift == "interrupt":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET interrupt_requested=TRUE WHERE id=%s", (b.id,))
    before = facts(p)
    with pytest.raises((ValueError, ContinuationLeaseLostError)):
        await decide_a(p, asset)
    assert facts(p) == before and receipt(p, a["approval_id"]) is None
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["session", "origin"])
async def test_busy_owner_delivery_must_not_lock_a_foreign_session_or_original(pending_formal, change):
    from src.schemas.agent_task import AgentTaskCommandV2
    from src.schemas.controlled_case_approval_submission import case_approval_task_id

    p = pending_formal
    asset, b = await owned(p)
    command = AgentTaskCommandV2.model_validate(wire(p, b.id))
    if change == "session":
        foreign = str(uuid4())
        command = command.model_copy(update={"session_id": foreign,
            "runtime_binding": command.runtime_binding.model_copy(update={"session_id": foreign})})
    else:
        foreign = str(uuid4())
        command = command.model_copy(update={"task_id": case_approval_task_id(foreign, command.reference_id),
            "parent_task_id": foreign,
            "case_approval_reference": command.case_approval_reference.model_copy(update={"origin_task_id": foreign})})
    command = AgentTaskCommandV2.model_validate(command.model_dump(mode="json"))
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.h.settings.database.postgres_task_outbox_table} SET payload=%s::jsonb WHERE event_key=%s",
            (command.model_dump_json(), "agent_turn:" + b.id))
    before = facts(p), native_originals(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="dedicated approval owner"):
        await decide_a(p, asset)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_busy_resume_without_a_native_result_stays_reconciliation_not_a_new_admission(pending_formal):
    from langgraph.checkpoint.base import WRITES_IDX_MAP
    from src.application.deep_agents.checkpoint_provider import checkpoint_serializer

    p = pending_formal
    asset, b = await owned(p)
    active = next(n for n in asset.natives if n.tool_call_id == "B")
    kind, blob = checkpoint_serializer().dumps_typed([receipt(p, b.reference_id).decision])
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
            "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) "
            "VALUES (%s,%s,%s,%s,%s,'__resume__',%s,%s,%s)",
            (p.s.binding.thread_id, p.s.binding.checkpoint_namespace, active.checkpoint_id, active.graph_task_id,
             WRITES_IDX_MAP["__resume__"], kind, blob, active.task_path))
    before = facts(p), native_originals(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="no settled tool result"):
        await decide_a(p, asset)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["same", "opposite"])
async def test_concurrent_busy_decisions_still_have_one_original_intent(pending_formal, mode):
    p = pending_formal
    asset, b = await owned(p)
    results = await asyncio.gather(decide_a(p, asset), decide_a(p, asset,
        "approved" if mode == "same" else "denied"), return_exceptions=True)
    if mode == "same":
        assert results[0] == results[1] and not isinstance(results[0], Exception)
    else:
        assert sum(isinstance(item, ValueError) for item in results) == 1
        assert sum(not isinstance(item, Exception) for item in results) == 1
    assert (await p.s.h.tasks.get(b.id)).status == "claimed" and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_busy_admission_last_audit_cannot_cross_any_owner_deadline(pending_formal, monkeypatch, lease):
    from tests.test_controlled_case_approval_runtime import expire_lease

    p = pending_formal
    asset, _ = await owned(p)
    before = facts(p)
    append = p.s.h.tasks._sessions.append_event_in_transaction
    def cross(cur, session_id, event, **kwargs):
        result = append(cur, session_id, event, **kwargs)
        if event.type == "approval.continuation_queued":
            expire_lease(p, cur, lease, delay="0.05 seconds")
            cur.execute("SELECT pg_sleep(0.1)")
        return result
    monkeypatch.setattr(p.s.h.tasks._sessions, "append_event_in_transaction", cross)
    with pytest.raises((ValueError, ContinuationLeaseLostError)):
        await decide_a(p, asset)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "reply_loss", "cancel_waiter"])
async def test_busy_transaction_failure_preserves_owner_and_never_reexecutes(pending_formal, monkeypatch, failure):
    p = pending_formal
    asset, b = await owned(p)
    service = submissions(p)
    before = facts(p)
    entered, release, finished = Event(), Event(), Event()
    submit, append = type(service)._submit, p.s.h.tasks._sessions.append_event_in_transaction
    def event(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == "approval.continuation_queued":
            if failure == "rollback":
                raise RuntimeError("isolated busy admission rollback")
            if failure == "cancel_waiter":
                entered.set()
                assert release.wait(30)
        return result
    def finish(slf, *args):
        try:
            result = submit(slf, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated busy admission reply loss")
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_submit", finish)
        patch.setattr(p.s.h.tasks._sessions, "append_event_in_transaction", event)
        waiter = asyncio.create_task(decide_a(p, asset))
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
            with pytest.raises(RuntimeError, match="isolated busy"):
                await waiter
    if failure == "rollback":
        assert facts(p) == before
    first = await decide_a(p, asset)
    saved = facts(p)
    assert await decide_a(p, asset) == first and facts(p) == saved
    assert (await p.s.h.tasks.get(b.id)).status == "claimed" and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_target", "changed_identity", "foreign_consuming"])
async def test_busy_native_observation_never_becomes_a_general_subset_or_replay_mode(pending_formal, monkeypatch, change):
    from src.application.deep_agents import native_approval_batch as batch

    p = pending_formal
    asset, b = await owned(p)
    active = next(n for n in asset.natives if n.tool_call_id == "B")
    if change == "foreign_consuming":
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            with pytest.raises(FrameworkApprovalRecoveryRequired, match="outside the original native batch"):
                batch.read_pending_tool_snapshot_original(cur, p.h.settings, p.source, p.s.asset.graph_input,
                    asset.natives, consuming_interrupt_id="foreign-interrupt")
        return
    identities = [active] if change == "missing_target" else [n.model_copy(update={"source_message_id": "foreign"}) for n in asset.natives]
    monkeypatch.setattr(batch, "collect_pending_tool_interrupts_from_snapshot", lambda *args, **kwargs: identities)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="complete pending native batch"):
        await decide_a(p, asset)
    assert facts(p) == before and (await p.s.h.tasks.get(b.id)).status == "claimed"

"""A keeps its root admission but resumes from the current immutable parking."""
from dataclasses import replace
import asyncio
import json

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.session import ApprovalDecisionRequest
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_claim import implementation as continuations, wire
from tests.test_controlled_case_approval_progress import progress_caps, retain, implementation as progress
from tests.test_controlled_case_approval_repark import prepared, repark, implementation as reparks
from tests.test_controlled_case_approval_runtime import ports, expire_lease
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_wait import facts, clock, implementation as waits


def successor_caps():
    return replace(progress_caps(), controlled_case_approval_successor_available=True)


async def submit_a(p, root, decision="approved"):
    return await submissions(p).submit(wire(p, p.h.task.id), root.state["tool_results"][0]["approval_id"],
        ApprovalDecisionRequest(decision=decision), capabilities=successor_caps())


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [True, False])
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_current_parking_claim_and_runtime_read_preserve_root_admission_and_b(pending_formal, queued, decision):
    p = pending_formal
    root, b, a, asset = await prepared(p, decision=decision, queued=queued)
    parked = await repark(p, b)
    if a is None:
        a = await submit_a(p, root)
    raw = wire(p, a.id)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT input_payload FROM {p.s.h.tasks._tasks} WHERE id=%s", (a.id,))
        original_admission = cur.fetchone()["input_payload"]
    before = await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(b.id), native_originals(p)
    budget = await p.s.calls.snapshot(p.s.binding)
    detail = await p.s.d.runs.get_run(p.s.item.run_id)
    original = next(attempt for attempt in detail.attempts if attempt.id == p.s.attempt.id)
    service = continuations(p)
    claimed = await service.claim(raw, worker_id="current-parking-a", capabilities=successor_caps())
    owner, lease = claimed.runtime_owner, claimed.runtime_owner.controlled_case_lease
    assert claimed.input == original_admission and claimed.parent_task_id == p.h.task.id
    assert claimed.input["controlled_case_approval_admission"]["wait_sha256"] == parked.parent_wait_sha256
    assert owner.owner_epoch == b.runtime_owner.owner_epoch + 1
    assert claimed.attempt_id not in {b.attempt_id, p.h.task.attempt_id, p.s.attempt.id}
    assert lease.run_item_execution_attempt_id == original.id and lease.attempt_no == original.attempt_no
    assert len({claimed.lease_token, lease.lease_token, b.lease_token, b.runtime_owner.controlled_case_lease.lease_token}) == 4
    frozen = facts(p), native_originals(p)
    original_input, current = await service.prepare_runtime(claimed, capabilities=successor_caps())
    assert original_input == p.s.asset and current == asset
    assert current.state["tool_results"][1]["status"] == ("completed" if decision == "approved" else "denied")
    assert current.state["event_log"][:len(root.state["event_log"])] == root.state["event_log"]
    assert current.hygiene_metadata == root.hygiene_metadata and current.root_model_budget == root.root_model_budget
    assert await service.prepare_runtime(claimed, capabilities=successor_caps()) == (original_input, asset)
    assert (facts(p), native_originals(p)) == frozen
    current.state["tool_messages"].clear()
    assert (await service.prepare_runtime(claimed, capabilities=successor_caps()))[1] == asset
    actual = await p.s.d.runs.get_run(p.s.item.run_id)
    attempt = next(attempt for attempt in actual.attempts if attempt.id == original.id)
    assert attempt.checkpoint_payload == original.checkpoint_payload and attempt.checkpoint_version == 8
    assert len(actual.attempts) == len(detail.attempts) and actual.results == []
    assert before == (await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(b.id), native_originals(p))
    assert await p.s.calls.snapshot(p.s.binding) == budget
    assert await reparks(p).acknowledge_delivery(wire(p, b.id), capabilities=progress_caps()) == parked
    assert await progress(p).read(wire(p, b.id), capabilities=progress_caps()) == asset
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=progress_caps()) == root
    assert len(p.requests) == 1 and len(p.effects) == (decision == "approved")


@pytest.mark.asyncio
@pytest.mark.parametrize("b_decision", ["approved", "denied"])
@pytest.mark.parametrize("a_decision", ["approved", "denied"])
async def test_a_really_continues_after_b_without_replaying_b_or_initial_finalizer(pending_formal, b_decision, a_decision):
    p = pending_formal
    root, b, _, asset = await prepared(p, decision=b_decision, queued=False)
    await repark(p, b)
    a = await submissions(p).submit(wire(p, p.h.task.id), root.state["tool_results"][0]["approval_id"],
        ApprovalDecisionRequest(decision=a_decision), capabilities=successor_caps())
    a = await continuations(p).claim(wire(p, a.id), worker_id="serial-runtime-a", capabilities=successor_caps())
    before = await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(b.id)
    runtime, worker = ports(p, caps=successor_caps())
    outcome = await runtime.execute(a, executor=worker, resource_id="serial-runtime-a", fencing_token=3)
    assert outcome.graph_outcome.native_terminal is not None and outcome.graph_outcome.native_interrupts == []
    assert outcome.approval_tool_projection.record.approval_id == a.reference_id
    assert outcome.state["tool_results"][0]["status"] == ("completed" if a_decision == "approved" else "denied")
    assert outcome.state["tool_results"][1] == asset.state["tool_results"][1]
    assert outcome.state["tool_messages"][1] == asset.state["tool_messages"][1]
    assert outcome.state["event_log"][:len(asset.state["event_log"])] == asset.state["event_log"]
    assert len([event for event in outcome.state["event_log"] if event["type"] == "runtime.turn_started"]) == 1
    assert len([event for event in outcome.state["event_log"] if event["type"] == "turn.resumed"]) == 2
    assert outcome.runtime_result is outcome.result_asset is outcome.settlement_receipt is outcome.assessment_asset is None
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert (await p.s.h.tasks.get(a.id)).status == "running"
    assert before == (await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(b.id))
    assert len(p.requests) == 2 and len(p.effects) == (a_decision == "approved") + (b_decision == "approved")
    assert await progress(p).read(wire(p, b.id), capabilities=progress_caps()) == asset
    frozen = facts(p), native_originals(p)
    with pytest.raises(ContinuationLeaseLostError):
        await runtime.execute(a, executor=worker, resource_id="no-a-replay", fencing_token=4)
    assert (facts(p), native_originals(p)) == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"third_tool": True}], indirect=True)
async def test_three_original_siblings_repark_twice_and_do_not_call_model_before_batch_closure(pending_formal, monkeypatch):
    p = pending_formal
    root, b, a, b_asset = await prepared(p)
    b_parking = await repark(p, b)
    service, caps = continuations(p), successor_caps()
    a = await service.claim(wire(p, a.id), worker_id="middle-a", capabilities=caps)
    entered, release = asyncio.Event(), asyncio.Event()
    handler = p.bridge.runtime._tool_runtime_service._handlers["inspect_source"]

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await handler(*args, **kwargs)

    monkeypatch.setitem(p.bridge.runtime._tool_runtime_service._handlers, "inspect_source", blocked)
    runtime, worker = ports(p, caps=caps)
    waiter = asyncio.create_task(runtime.execute(a, executor=worker, resource_id="middle-a", fencing_token=3))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        c_id = root.state["tool_results"][2]["approval_id"]
        c = await submissions(p).submit(wire(p, p.h.task.id), c_id,
            ApprovalDecisionRequest(decision="denied"), capabilities=caps)
        before = facts(p)
        with pytest.raises(TaskDeferred, match="already owned"):
            await service.claim(wire(p, c.id), worker_id="no-parallel-c", capabilities=caps)
        assert facts(p) == before and len(p.requests) == 1
    finally:
        release.set()
        outcome = await asyncio.wait_for(waiter, 30)
    assert len(outcome.graph_outcome.native_interrupts) == 1 and len(p.requests) == 1
    assert [item[0]["path"] for item in p.effects] == ["b.py", "a.py"]
    a_asset = await retain(p, a, outcome, caps=caps)
    # Retention alone still leaves A active, rather than publishing its state
    # as a new parking or permitting C to claim.
    before = facts(p)
    with pytest.raises(TaskDeferred, match="already owned"):
        await service.claim(wire(p, c.id), worker_id="no-material-bypass", capabilities=caps)
    assert facts(p) == before
    a_parking = await repark(p, a, caps=replace(caps, controlled_case_approval_repark_available=True))
    assert a_parking.task_id == a.id and [entry.approval_id for entry in a_parking.batch] == [c.reference_id]
    assert a_asset.parent_state_sha256 == b_asset.asset_sha256
    assert await reparks(p).acknowledge_delivery(wire(p, b.id), capabilities=progress_caps()) == b_parking
    c = await service.claim(wire(p, c.id), worker_id="last-c", capabilities=caps)
    assert c.runtime_owner.owner_epoch == a.runtime_owner.owner_epoch + 1
    assert (await service.prepare_runtime(c, capabilities=caps))[1] == a_asset
    final = await runtime.execute(c, executor=worker, resource_id="last-c", fencing_token=4)
    assert final.graph_outcome.native_terminal is not None and final.graph_outcome.native_interrupts == []
    assert final.state["tool_results"][:2] == a_asset.state["tool_results"][:2]
    assert final.state["tool_results"][2]["status"] == "denied" and len(p.requests) == 2
    assert [item[0]["path"] for item in p.effects] == ["b.py", "a.py"]
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert await progress(p).read(wire(p, b.id), capabilities=progress_caps()) == b_asset
    assert await progress(p).read(wire(p, a.id), capabilities=progress_caps()) == a_asset
    assert await reparks(p).acknowledge_delivery(wire(p, a.id), capabilities=progress_caps()) == a_parking


async def at_boundary(p, phase):
    root, b, a, asset = await prepared(p)
    parked = await repark(p, b)
    service, caps = continuations(p), successor_caps()
    task = None
    if phase != "claim":
        task = await service.claim(wire(p, a.id), worker_id="boundary-a", capabilities=caps)
    if phase == "delivery":
        await service.begin(task, resource_id="boundary-a", fencing_token=3, capabilities=caps)

    async def invoke(*, capabilities=caps):
        if phase == "claim":
            return await service.claim(wire(p, a.id), worker_id="boundary-a", capabilities=capabilities)
        if phase == "runtime":
            return await service.prepare_runtime(task, capabilities=capabilities)
        if phase == "begin":
            return await service.begin(task, resource_id="boundary-a", fencing_token=3, capabilities=capabilities)
        return await service.prepare_delivery(task, capabilities=capabilities)

    return root, b, a, asset, parked, task, invoke


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["claim", "runtime", "begin", "delivery"])
@pytest.mark.parametrize("drift", ["native_error", "root_pointer", "return_fact"])
async def test_every_successor_boundary_rechecks_current_source_not_just_schema8(pending_formal, phase, drift):
    p = pending_formal
    root, b, a, asset, parked, task, invoke = await at_boundary(p, phase)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "native_error":
            from langgraph.checkpoint.base import WRITES_IDX_MAP
            from src.application.deep_agents.checkpoint_provider import checkpoint_serializer

            native, = asset.natives
            kind, blob = checkpoint_serializer().dumps_typed("isolated current sibling failure")
            cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) "
                "VALUES (%s,%s,%s,%s,%s,'__error__',%s,%s,%s)",
                (p.s.binding.thread_id, p.s.binding.checkpoint_namespace, native.checkpoint_id,
                    native.graph_task_id, WRITES_IDX_MAP['__error__'], kind, blob, native.task_path))
        elif drift == "root_pointer":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{pending_turn,controlled_case_wait_sha256}',%s::jsonb) WHERE id=%s",
                (json.dumps(parked.parent_wait_sha256), p.s.session.id))
        else:
            cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,"
                "'{execution_returned}','false'::jsonb) WHERE payload->>'task_id'=%s AND type='agent_task.completed'", (b.id,))
            assert cur.rowcount == 1
    before = facts(p)
    if drift != "return_fact":
        assert await progress(p).read(wire(p, b.id), capabilities=progress_caps()) == asset
    with pytest.raises(ValueError):
        await invoke()
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["claim", "runtime", "begin", "delivery"])
@pytest.mark.parametrize("change", ["disabled", "codec", "checkpoint"])
async def test_new_format_readers_never_implicitly_enable_successor_execution(pending_formal, phase, change):
    p = pending_formal
    *_, invoke = await at_boundary(p, phase)
    caps = {"disabled": progress_caps(), "codec": replace(successor_caps(), controlled_case_approval_repark_max_version=0),
        "checkpoint": replace(successor_caps(), controlled_case_checkpoint_max_version=7)}[change]
    before = facts(p)
    with pytest.raises(TaskDeferred, match="successor_unavailable|repark_format_unavailable|checkpoint_format_unavailable"):
        await invoke(capabilities=caps)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_successor_claim_final_audit_crossing_either_lease_rolls_back(pending_formal, monkeypatch, lease):
    p = pending_formal
    *_, invoke = await at_boundary(p, "claim")
    original, reached = p.s.h.tasks._event, []

    def cross(cur, row, kind, payload):
        original(cur, row, kind, payload)
        if kind == "test_run.case_approval_claimed":
            reached.append(payload)
            expire_lease(p, cur, lease, delay="0.05 seconds")
            cur.execute("SELECT pg_sleep(0.1)")

    monkeypatch.setattr(p.s.h.tasks, "_event", cross)
    before = facts(p)
    with pytest.raises(ContinuationLeaseLostError):
        await invoke()
    assert len(reached) == 1 and reached[0]["current_wait_sha256"] != reached[0]["wait_sha256"]
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_successor_concurrent_claim_has_one_owner_and_cancelled_run_cannot_prepare_runtime(pending_formal):
    p = pending_formal
    root, b, a, asset = await prepared(p)
    await repark(p, b)
    service, caps = continuations(p), successor_caps()
    results = await asyncio.gather(*(service.claim(wire(p, a.id), worker_id=worker, capabilities=caps)
        for worker in ("serial-a-one", "serial-a-two")), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, TaskDeferred) for result in results) == 1
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {p.s.h.tasks._attempts} WHERE task_id=%s", (a.id,))
        assert cur.fetchone()["n"] == 1
    assert len([attempt for attempt in (await p.s.d.runs.get_run(p.s.item.run_id)).attempts
        if attempt.run_item_id == p.s.item.id]) == 1
    winner, = [result for result in results if not isinstance(result, Exception)]
    await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated successor cancellation", clock(p))
    before = facts(p)
    with pytest.raises(ContinuationLeaseLostError):
        await service.prepare_runtime(winner, capabilities=caps)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [True, False])
async def test_cancelled_parked_run_cannot_admit_or_claim_a(pending_formal, queued):
    p = pending_formal
    root, b, a, _ = await prepared(p, queued=queued)
    await repark(p, b)
    await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated parked cancellation", clock(p))
    before = facts(p)
    with pytest.raises(ValueError):
        if queued:
            await continuations(p).claim(wire(p, a.id), worker_id="cancelled-a", capabilities=successor_caps())
        else:
            await submit_a(p, root)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"third_tool": True}], indirect=True)
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_active_successor_sibling_admission_checks_leases_after_last_current_read(pending_formal, monkeypatch, lease):
    p = pending_formal
    root, b, a, _ = await prepared(p)
    await repark(p, b)
    await continuations(p).claim(wire(p, a.id), worker_id="owned-middle-a", capabilities=successor_caps())
    service = submissions(p)
    original, observed = type(service).current_wait_in_transaction, []

    def cross(slf, cur, *args, **kwargs):
        result = original(slf, cur, *args, **kwargs)
        observed.append(result.receipt.receipt_sha256)
        if len(observed) == 2:
            expire_lease(p, cur, lease, delay="0.05 seconds")
            cur.execute("SELECT pg_sleep(0.1)")
        return result

    monkeypatch.setattr(type(service), "current_wait_in_transaction", cross)
    before = facts(p)
    with pytest.raises(ContinuationLeaseLostError if lease == "formal" else ValueError):
        await service.submit(wire(p, p.h.task.id), root.state["tool_results"][2]["approval_id"],
            ApprovalDecisionRequest(decision="denied"), capabilities=successor_caps())
    assert len(observed) == 2 and facts(p) == before
    assert len(p.requests) == len(p.effects) == 1

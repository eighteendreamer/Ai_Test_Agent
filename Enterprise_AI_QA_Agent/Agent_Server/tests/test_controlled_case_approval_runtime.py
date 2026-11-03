"""One formal Runtime invocation uses original waiting slots, not first-input state."""
from copy import deepcopy
from dataclasses import replace
import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from src.application.runtime.controlled_case_executor import ControlledCaseExecutor
from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_state import state_caps, park_state, forge_state, mixed_formal
from tests.test_controlled_case_approval_wait import implementation as waits, facts
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_claim import implementation as continuations, wire
from tests.test_controlled_case_approval_delivery import receipt


def test_shared_refusal_factory_freezes_exact_ordinal_and_hygiene_before_callback(monkeypatch):
    from src.application.runtime import tool_messages

    captured = []
    monkeypatch.setattr(tool_messages, "build_rejected_tool_projection", lambda **kwargs: captured.append(kwargs))
    records, hygiene = [{"call_id": "A"}, {"call_id": "B"}], {"policy": {"version": 1}}
    projector = tool_messages.rejection_projector_from_original_slot(binding="bound", source="native",
        approval_id="approval", call_id="B", tool_records=records, hygiene_metadata=hygiene,
        include_runtime_message=True)
    records.reverse()
    hygiene["policy"]["version"] = 2
    job, native = object(), SimpleNamespace(content="original refusal")
    projector(job, native)
    assert captured == [{"binding": "bound", "source": "native", "job": job, "approval_id": "approval",
        "ordinal": 2, "hygiene_metadata": {"policy": {"version": 1}},
        "model_content": "original refusal", "include_runtime_message": True}]


@pytest.mark.parametrize("records", [[{"call_id": "A"}], [{"call_id": "B"}, {"call_id": "B"}]])
def test_shared_refusal_factory_does_not_guess_missing_or_duplicate_slots(records):
    from src.application.runtime.tool_messages import rejection_projector_from_original_slot

    with pytest.raises(FrameworkApprovalRecoveryRequired, match="Original rejection has no application tool slot"):
        rejection_projector_from_original_slot(binding="bound", source="native", approval_id="approval",
            call_id="B", tool_records=records, hygiene_metadata={}, include_runtime_message=True)


async def owned(p, decision="approved", *, path="b.py"):
    await park_state(p)
    asset = await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
    selected = next(item for item in asset.state["tool_results"] if item["input"]["path"] == path)
    submitted = await submissions(p).submit(wire(p, p.h.task.id), selected["approval_id"],
        ApprovalDecisionRequest(decision=decision), capabilities=state_caps())
    task = await continuations(p).claim(wire(p, submitted.id), worker_id="formal-runtime-worker", capabilities=state_caps())
    return asset, task


def ports(p, *, caps=None, continuation=None):
    worker = ControlledCaseExecutor(task_store=p.s.h.tasks, graph_adapter=p.h.adapter,
        approval_continuations=continuation or continuations(p), framework_capabilities=caps or state_caps())
    runtime = ControlledCaseRuntime(runtime_service=p.bridge.runtime, approval_wait_service=waits(p))
    return runtime, worker


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_formal_runtime_applies_selected_b_to_its_original_second_slot(pending_formal, decision):
    p = pending_formal
    asset, task = await owned(p, decision)
    runtime, worker = ports(p)
    before = await p.s.d.runs.get_run(p.s.item.run_id)
    producer = await p.s.h.tasks.get(p.h.task.id)
    ledger = await p.s.calls.snapshot(p.s.binding)
    result = await runtime.execute(task, executor=worker, resource_id="formal-runtime-result", fencing_token=2)
    material = result.approval_tool_projection
    assert material == await continuations(p).read_tool_projection(task, capabilities=state_caps())
    assert material.record.approval_id == task.reference_id and material.message.metadata["ordinal"] == 2
    assert material.record.status == ("completed" if decision == "approved" else "denied")
    assert result.state["tool_results"][1] == material.record.model_dump(mode="json")
    assert result.state["tool_messages"][1] == material.runtime_message
    assert result.state["tool_results"][0] == asset.state["tool_results"][0]
    assert result.state["tool_messages"][0] == asset.state["tool_messages"][0]
    assert result.tool_records == result.state["tool_results"]
    assert result.state["event_log"][:len(asset.state["event_log"])] == asset.state["event_log"]
    assert len([e for e in result.state["event_log"] if e["type"] == "runtime.turn_started"]) == 1
    assert len([e for e in result.state["event_log"] if e["type"] == "turn.resumed"]) == 1
    assert len(result.graph_outcome.native_interrupts) == 1 and result.graph_outcome.native_terminal is None
    assert result.state["pending_approvals"] == [asset.state["pending_approvals"][0]]
    assert result.state["context_bundle"]["deep_agent_approval_batch"] == result.state["pending_approvals"]
    # This is the application step BEFORE the dedicated atomic re-park/finalizer.
    assert result.state["control_state"] == "resuming" and result.state["pending_turn"] == {}
    assert result.runtime_result is result.result_asset is result.assessment_asset is result.settlement_receipt is None
    assert result.approval_wait_receipt is result.conversation_response is None
    assert (await p.s.h.tasks.get(task.id)).status == "running"
    assert await p.s.h.tasks.get(p.h.task.id) == producer
    current = await p.s.d.runs.get_run(p.s.item.run_id)
    original = next(a for a in before.attempts if a.id == p.s.attempt.id)
    actual = next(a for a in current.attempts if a.id == p.s.attempt.id)
    assert current.results == [] and actual.id == original.id and actual.started_at == original.started_at
    assert actual.checkpoint_payload == original.checkpoint_payload and actual.attempt_no == original.attempt_no
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset
    assert await p.s.calls.snapshot(p.s.binding) == ledger and len(p.requests) == 1
    assert len(p.effects) == (1 if decision == "approved" else 0)
    assert receipt(p, task.reference_id).applied_at is not None
    old = facts(p), native_originals(p)
    with pytest.raises(ContinuationLeaseLostError, match="no longer active"):
        await runtime.execute(task, executor=worker, resource_id="no-replay", fencing_token=3)
    assert (facts(p), native_originals(p)) == old


@pytest.mark.asyncio
async def test_claimed_runtime_preparation_is_read_only_and_cannot_begin_or_rebuild_context(pending_formal, monkeypatch):
    p = pending_formal
    asset, task = await owned(p)
    before = facts(p), native_originals(p)
    service = continuations(p)
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only Runtime preparation tried to start, execute or reassemble")
    for name in ("_transition_sync", "_update_status", "_clear_lease"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.h.adapter, "execute_frozen", forbidden)
    prepared, actual = await service.prepare_runtime(task, capabilities=state_caps())
    assert prepared == p.s.asset and actual == asset
    assert (facts(p), native_originals(p)) == before
    actual.state["tool_messages"].clear()
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset
    assert len(p.requests) == 1 and p.effects == []


def expire_lease(p, cur, lease, *, delay="0 seconds"):
    if lease == "session":
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
            "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()+%s::interval)) WHERE id=%s", (delay, p.s.session.id))
    elif lease == "owner":
        cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at=clock_timestamp()+%s::interval WHERE binding_id=%s",
            (delay, p.s.binding.binding_id))
    else:
        cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
            "to_jsonb(clock_timestamp()+%s::interval)) WHERE id=%s", (delay, p.s.item.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_read", "last_read", "after_read"])
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_runtime_read_does_not_grant_authority_after_any_lease_expires(pending_formal, monkeypatch, phase, lease):
    p = pending_formal
    _, task = await owned(p)
    service = continuations(p)
    runtime, worker = ports(p, continuation=service)
    if phase == "before_read":
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            expire_lease(p, cur, lease)
    elif phase == "last_read":
        original = type(service)._captured_originals
        def cross_deadline(slf, cur, *args, **kwargs):
            saved = original(slf, cur, *args, **kwargs)
            expire_lease(p, cur, lease, delay="0.05 seconds")
            cur.execute("SELECT pg_sleep(0.1)")
            return saved
        monkeypatch.setattr(type(service), "_captured_originals", cross_deadline)
    else:
        original = service.prepare_runtime
        async def after_read(*args, **kwargs):
            result = await original(*args, **kwargs)
            with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
                expire_lease(p, cur, lease)
            return result
        monkeypatch.setattr(service, "prepare_runtime", after_read)
    before = facts(p), native_originals(p)
    with pytest.raises(ContinuationLeaseLostError):
        await runtime.execute(task, executor=worker, resource_id="runtime-expired", fencing_token=2)
    if phase != "after_read":
        # An expiry injected inside a failed read transaction must roll back;
        # a committed pre-read expiry must not be repaired by that read either.
        assert (facts(p), native_originals(p)) == before
    assert (await p.s.h.tasks.get(task.id)).status == "claimed"
    assert len(p.requests) == 1 and p.effects == [] and receipt(p, task.reference_id).delivered_at is None


@pytest.mark.asyncio
async def test_cancelled_read_waiter_cannot_begin_even_when_sql_read_finishes(pending_formal, monkeypatch):
    p = pending_formal
    _, task = await owned(p)
    service = continuations(p)
    runtime, worker = ports(p, continuation=service)
    entered, release, finished = Event(), Event(), Event()
    original = type(service)._prepare_runtime
    def blocked(slf, *args):
        try:
            result = original(slf, *args)
            entered.set()
            assert release.wait(30)
            return result
        finally:
            finished.set()
    monkeypatch.setattr(type(service), "_prepare_runtime", blocked)
    before = facts(p), native_originals(p)
    waiter = asyncio.create_task(runtime.execute(task, executor=worker, resource_id="read-only-cancel", fencing_token=2))
    try:
        assert await asyncio.to_thread(entered.wait, 30)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 30)
    assert (facts(p), native_originals(p)) == before
    assert (await p.s.h.tasks.get(task.id)).status == "claimed" and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize("pending_formal", [{"single_tool": True}], indirect=True)
async def test_last_decision_can_close_native_graph_but_never_use_initial_finalizer(pending_formal, monkeypatch, decision):
    p = pending_formal
    asset, task = await owned(p, decision, path="a.py")
    runtime, worker = ports(p)
    def forbidden(*args, **kwargs):
        pytest.fail("A successor was routed into initial finalization, parking or context assembly")
    monkeypatch.setattr(p.bridge.runtime, "_turn_context_assembly", SimpleNamespace(prepare=forbidden))
    monkeypatch.setattr(type(runtime._approval_waits), "park", forbidden)
    # Supplying first-execution finalization ports cannot grant the missing
    # successor result-lineage/settlement permission.
    runtime._sessions = runtime._assessments = runtime._settlements = object()
    result = await runtime.execute(task, executor=worker, resource_id="closed-native-only", fencing_token=2)
    assert result.graph_outcome.native_terminal is not None and result.graph_outcome.native_interrupts == []
    assert result.approval_tool_projection.record.approval_id == task.reference_id
    assert result.state["pending_approvals"] == [] and result.state["pending_turn"] == {}
    assert result.state["control_state"] == "resuming" and result.state["termination_reason"] == ""
    assert result.state["final_response"] == "" and result.runtime_result is result.result_asset is None
    assert result.settlement_receipt is result.assessment_asset is result.conversation_response is None
    assert (await p.s.h.tasks.get(task.id)).status == "running"
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert len(p.requests) == 2 and len(p.effects) == (decision == "approved")
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize("pending_formal", [{"single_tool": True}], indirect=True)
async def test_next_native_batch_is_not_silently_removed_or_sent_to_initial_parking(pending_formal, monkeypatch, decision):
    from tests import test_framework_chat_models as provider

    p = pending_formal
    asset, task = await owned(p, decision, path="a.py")
    reply = provider._tool_reply("openai_chat_completions")
    call, = reply["choices"][0]["message"]["tool_calls"]
    call["id"], call["function"]["arguments"] = "C", '{"path":"c.py"}'
    monkeypatch.setattr(provider, "_reply", lambda transport: reply)
    runtime, worker = ports(p)
    outcomes = []
    invoke = p.h.adapter.execute_frozen
    async def capture(*args, **kwargs):
        outcome = await invoke(*args, **kwargs)
        outcomes.append(outcome)
        return outcome
    monkeypatch.setattr(p.h.adapter, "execute_frozen", capture)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="new native approval batch requires successor parking"):
        await runtime.execute(task, executor=worker, resource_id="new-native-batch", fencing_token=2)
    outcome, = outcomes
    pending, = outcome.native_interrupts
    assert pending.tool_call_id == "C" and outcome.native_terminal is None
    assert receipt(p, task.reference_id).applied_at is not None
    assert (await continuations(p).read_tool_projection(task, capabilities=state_caps())).record.approval_id == task.reference_id
    assert (await p.s.h.tasks.get(task.id)).status == "running"
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset
    before = facts(p), native_originals(p)
    with pytest.raises(ContinuationLeaseLostError, match="no longer active"):
        await runtime.execute(task, executor=worker, resource_id="no-new-batch-replay", fencing_token=3)
    assert (facts(p), native_originals(p)) == before
    assert len(p.requests) == 2 and len(p.effects) == (decision == "approved")


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed_formal", [False, True], indirect=True)
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_mixed_runtime_preserves_the_already_completed_slot_and_exact_display_string(mixed_formal, decision):
    p = mixed_formal
    path = next(n.arguments["path"] for n in p.outcome.graph_outcome.native_interrupts)
    asset, task = await owned(p, decision, path=path)
    runtime, worker = ports(p)
    result = await runtime.execute(task, executor=worker, resource_id="mixed-formal-runtime", fencing_token=2)
    assert result.state["tool_results"][0] == asset.state["tool_results"][0]
    assert result.state["tool_messages"][0] == asset.tool_materials[0].runtime_message
    assert result.approval_tool_projection.message.metadata["ordinal"] == 2
    assert result.state["tool_messages"][1] == result.approval_tool_projection.runtime_message
    assert len(p.effects) == 1 + (decision == "approved") and len(p.requests) == 2
    assert result.graph_outcome.native_terminal is not None
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delivery", "claim", "checkpoint", "wait", "port", "serialized"])
async def test_unavailable_runtime_composition_rejects_before_running_or_any_native_action(pending_formal, change):
    p = pending_formal
    _, task = await owned(p)
    values = {"delivery": {"controlled_case_approval_delivery_available": False},
        "claim": {"controlled_case_approval_claim_available": False},
        "checkpoint": {"controlled_case_checkpoint_max_version": 6}, "wait": {"controlled_case_approval_wait_max_version": 1}}
    runtime, worker = ports(p, caps=replace(state_caps(), **values.get(change, {})))
    if change == "port":
        worker._approval_continuations = None
    elif change == "serialized":
        task = type(task).model_validate(task.model_dump(mode="json"))
    before = facts(p), native_originals(p)
    error = ValueError if change in {"port", "serialized"} else TaskDeferred
    with pytest.raises(error):
        await runtime.execute(task, executor=worker, resource_id="no-start", fencing_token=2)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_legacy_wait_has_no_invented_runtime_state_and_stays_claimed(pending_formal):
    from tests.test_controlled_case_approval_delivery import successor

    p = pending_formal
    _, task = await successor(p)
    runtime, worker = ports(p)
    before = facts(p), native_originals(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="frozen full application preparation and waiting slots"):
        await runtime.execute(task, executor=worker, resource_id="legacy-no-upgrade", fencing_token=2)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["project_id", "trace_id", "parent_task_id", "reference_id", "input", "priority"])
async def test_runtime_preparation_checks_the_exact_captured_successor(pending_formal, field):
    p = pending_formal
    _, task = await owned(p)
    runtime, worker = ports(p)
    changed = task.model_copy(update={field: {} if field == "input" else task.priority + 1 if field == "priority" else "foreign"})
    before = facts(p), native_originals(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="captured"):
        await runtime.execute(changed, executor=worker, resource_id="changed-capture", fencing_token=2)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_slot", "slot_string", "native_messages", "pending"])
async def test_runtime_does_not_begin_from_rehashed_but_conflicting_original_state(pending_formal, change):
    p = pending_formal
    _, task = await owned(p)
    forge_state(p, change)
    runtime, worker = ports(p)
    before = facts(p), native_originals(p)
    with pytest.raises(ValueError):
        await runtime.execute(task, executor=worker, resource_id="drifted-state", fencing_token=2)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_runtime_refusal_uses_frozen_hygiene_not_current_display_settings(pending_formal, monkeypatch):
    p = pending_formal
    asset, task = await owned(p, "denied")
    runtime, worker = ports(p)
    monkeypatch.setattr(p.bridge.runtime._transcript_hygiene_service, "tool_message_metadata", lambda: {"foreign_policy": True})
    result = await runtime.execute(task, executor=worker, resource_id="original-hygiene", fencing_token=2)
    assert "foreign_policy" not in result.approval_tool_projection.message.metadata
    assert all(result.approval_tool_projection.message.metadata[k] == v for k, v in asset.hygiene_metadata.items())
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_actual_runtime_approval_still_checks_live_permission_before_handler(pending_formal, monkeypatch):
    p = pending_formal
    _, task = await owned(p)
    runtime, worker = ports(p)
    invoke = p.h.adapter.execute_frozen
    async def revoked(*args, **kwargs):
        p.bridge.runtime._tool_registry.get("inspect_source").permission_level = "restricted"
        return await invoke(*args, **kwargs)
    monkeypatch.setattr(p.h.adapter, "execute_frozen", revoked)
    result = await runtime.execute(task, executor=worker, resource_id="runtime-revoked", fencing_token=2)
    material = result.approval_tool_projection
    assert material.record.status == "denied" and material.record.started_at is None
    assert material.record.output["error"] == "permission_denied"
    assert receipt(p, task.reference_id).applied_at is not None and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["application_receipt_loss", "projection_read", "cancel_waiter"])
async def test_runtime_application_unknown_does_not_reexecute_or_complete_task(pending_formal, monkeypatch, failure):
    p = pending_formal
    _, task = await owned(p, "denied")
    service = continuations(p)
    runtime, worker = ports(p, continuation=service)
    apply, read = service.apply_decision, service.read_tool_projection
    entered, release, finished = Event(), Event(), Event()
    async def applied(*args, **kwargs):
        result = await apply(*args, **kwargs)
        if failure == "application_receipt_loss":
            raise RuntimeError("isolated applied receipt loss")
        return result
    async def projection(*args, **kwargs):
        if failure == "projection_read":
            raise RuntimeError("isolated projection read unavailable")
        return await read(*args, **kwargs)
    original = type(service)._apply_decision
    def blocked(slf, *args):
        try:
            result = original(slf, *args)
            entered.set()
            assert release.wait(30)
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(service, "apply_decision", applied)
        patch.setattr(service, "read_tool_projection", projection)
        if failure == "cancel_waiter":
            patch.setattr(type(service), "_apply_decision", blocked)
        waiter = asyncio.create_task(runtime.execute(task, executor=worker, resource_id="runtime-unknown", fencing_token=2))
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
            with pytest.raises(RuntimeError, match="isolated"):
                await waiter
    assert receipt(p, task.reference_id).applied_at is not None
    assert (await p.s.h.tasks.get(task.id)).status == "running"
    before = facts(p), native_originals(p)
    assert (await service.read_tool_projection(task, capabilities=state_caps())).record.status == "denied"
    assert (facts(p), native_originals(p)) == before
    with pytest.raises(ContinuationLeaseLostError, match="no longer active"):
        await runtime.execute(task, executor=worker, resource_id="never-replay", fencing_token=3)
    assert (facts(p), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []

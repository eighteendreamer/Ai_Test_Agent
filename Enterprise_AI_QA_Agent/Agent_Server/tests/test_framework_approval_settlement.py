"""Projected approvals grant settlement only, never native or finalizer replay."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired, validate_message_stage
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from tests.test_framework_approval_graph_input import (
    harness, checkpoint_harness, projection_harness, fresh_runtime_harness,
    prepared_approval_runtime, frozen_approval_result, queue_approval_finalization,
)
from tests.test_framework_approval_recovery import expire, run_delivery, outbox
from tests.test_framework_projected_settlement import attempts, change_projection


@pytest_asyncio.fixture
async def projected_approval(frozen_approval_result, request):
    h = frozen_approval_result
    asset = h.result_asset
    if getattr(request, "param", "original") == "finalizer":
        h.expired_producer, successor = await queue_approval_finalization(h)
        h.result_task = await h.tasks.claim(successor.id, worker_id="projected-finalizer", expected_binding=h.binding)
        await h.tasks.begin(h.result_task, resource_id="projected-finalizer", fencing_token=3)
        monitor = asyncio.create_task(asyncio.Event().wait())
        try:
            await h.sessions.execute_claimed_approval(h.result_task, ownership_monitor=monitor)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        assert h.stages.get(h.initial_task_id, h.session_id).phase == "projected"
        return h
    with framework_owner_scope(h.result_task.runtime_owner):
        response = await h.sessions._finalize_runtime_result(session=await h.store.get_session(h.session_id),
            runtime_result=asset.runtime_result.restore(), assistant_message_id=asset.assistant_message_id,
            user_message_override=asset.user_message, turn_lease_token=h.result_task.lease_token,
            projection_owner=h.result_task.runtime_owner, projection_asset=asset)
        await h.stages.projected_approval(h.result_task.runtime_owner, response.output.id)
    assert h.stages.get(h.initial_task_id, h.session_id).phase == "projected"
    return h


def forbid_execution(h, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Projected approval must not execute native graph or application finalizer")

    for target, name in ((h.sessions, "execute_claimed_approval"), (h.sessions, "_finalize_runtime_result"),
        (h.runtime, "execute_turn"), (h.runtime, "resume_framework_approval"),
        (h.sessions._verification_service, "build_results"),
        (h.sessions._input_orchestrator_service, "orchestrate_async")):
        monkeypatch.setattr(target, name, forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("projected_approval", ["original", "finalizer"], indirect=True)
async def test_projected_approval_loss_reconciles_and_only_settles(projected_approval, monkeypatch):
    h = projected_approval
    before = h.stages.get(h.initial_task_id, h.session_id)
    budget = await h.calls.snapshot(h.binding)
    snapshot = await h.store.get_latest_snapshot(h.session_id)
    effects, requests = list(h.effects), list(h.adapter_requests)
    terminal = await expire(h, h.result_task)
    old_attempts = attempts(h, h.result_task.id)
    candidates = await asyncio.gather(*(h.tasks.ensure_framework_reconciliation(h.result_task.id) for _ in range(3)))
    assert all(item is not None for item in candidates)
    assert len({item.id for item in candidates}) == 1
    forbid_execution(h, monkeypatch)
    reconciler = candidates[0]
    await run_delivery(h, reconciler.id)
    reconciled = await h.tasks.get(reconciler.id)
    assert reconciled.status == "completed", reconciled.error_code
    assert reconciled.result_payload["proof"]["scope"] == "application_settlement"
    successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
    assert outbox(h, successor.id) is not None
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert saved.schema_version == 25 and saved.phase == "projected"
    assert "post_commit_memory_intent" in saved.source_manifest
    assert saved.source_manifest == before.source_manifest
    assert saved.result_reference == before.result_reference
    assert saved.approval_settlements[-1].resume_phase == "settling"
    await run_delivery(h, successor.id)
    completed = await h.tasks.get(successor.id)
    assert completed.status == completed.result_status == "completed", completed.error_code
    await run_delivery(h, successor.id)
    assert await h.tasks.get(successor.id) == completed
    assert await h.tasks.get(h.result_task.id) == terminal
    assert attempts(h, h.result_task.id) == old_attempts
    assert await h.calls.snapshot(h.binding) == budget
    assert await h.store.get_latest_snapshot(h.session_id) == snapshot
    assert h.effects == effects and h.adapter_requests == requests
    current = h.stages.get(h.initial_task_id, h.session_id)
    assert current.source_manifest == before.source_manifest
    session = await h.store.get_session(h.session_id)
    assert session.status.value == session.metadata["control"]["control_state"] == "completed"
    assert not session.metadata["control"]["is_interrupted"]
    events = await h.store.list_events(h.session_id)
    assert len([item for item in events if item.type == "turn.completed" and item.payload.get("session_status") == "completed"]) == 1
    assert len([item for item in events if item.type == "agent_task.completed" and item.payload["task_id"] == successor.id]) == 1


async def settlement_successor(h):
    terminal = await expire(h, h.result_task)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    assert reconciler is not None
    await run_delivery(h, reconciler.id)
    reconciled = await h.tasks.get(reconciler.id)
    assert reconciled.status == "completed", reconciled.error_code
    return terminal, await h.tasks.get(reconciled.result_payload["successor_task_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("projected_approval", ["original", "finalizer"], indirect=True)
async def test_original_projected_owner_only_finishes_once_without_native_material_reads(projected_approval, monkeypatch):
    import src.runtime.framework_runtime_results as results

    h = projected_approval
    saved = h.stages.get(h.initial_task_id, h.session_id)
    forbid_execution(h, monkeypatch)
    monkeypatch.setattr(results, "_result_projection_at", lambda *args, **kwargs: pytest.fail("Settlement read native originals"))
    results = await asyncio.gather(*(h.tasks.finish(h.result_task, status="completed", result_status="completed")
        for _ in range(3)), return_exceptions=True)
    assert sum(item is None for item in results) == 1
    assert all(item is None or isinstance(item, ContinuationLeaseLostError) for item in results)
    await run_delivery(h, h.result_task.id)
    assert h.stages.get(h.initial_task_id, h.session_id) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["admission", "claim", "finish"])
@pytest.mark.parametrize("kind", ["snapshot", "message", "event", "budget", "tool", "verification", "reference", "missing_commit"])
async def test_approval_settlement_rechecks_committed_facts(projected_approval, monkeypatch, boundary, kind):
    from types import SimpleNamespace

    h = projected_approval
    h.job = SimpleNamespace(id=h.proposals["A"].job.id)
    if boundary == "admission":
        terminal = await expire(h, h.result_task)
    else:
        terminal, successor = await settlement_successor(h)
        if boundary == "finish":
            task = await h.tasks.claim(successor.id, worker_id="settlement-drift", expected_binding=h.binding)
            await h.tasks.begin(task, resource_id="settlement-drift", fencing_token=4)
    saved = h.stages.get(h.initial_task_id, h.session_id)
    forbid_execution(h, monkeypatch)
    if kind == "budget":
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,'{{max_calls}}',"
                "to_jsonb((policy->>'max_calls')::int+1)) WHERE binding_id=%s", (h.binding.binding_id,))
            assert cur.rowcount == 1
    else:
        change_projection(h, saved, kind)
    if boundary == "admission":
        assert await h.tasks.ensure_framework_reconciliation(h.result_task.id) is None
    elif boundary == "claim":
        rejected = await h.tasks.claim(successor.id, worker_id="changed-settlement", expected_binding=h.binding)
        assert rejected.status == "recovery_required" and rejected.attempt_id is None
    else:
        with pytest.raises((FrameworkApprovalRecoveryRequired, FrameworkMessageStageRecoveryRequired, ValidationError)):
            await h.tasks.finish(task, status="completed", result_status="completed")
        assert (await h.tasks.get(task.id)).status == "running"
    assert await h.tasks.get(h.result_task.id) == terminal
    assert not any(item.type == "agent_task.completed" and item.payload["task_id"] == h.result_task.id
        for item in await h.store.list_events(h.session_id))


@pytest.mark.asyncio
async def test_only_settlement_proof_cannot_enter_native_or_application_writes(projected_approval):
    from src.runtime.framework_approval_recovery import require_native_recovery_scope
    from src.runtime.postgres_agent_task_store import AgentSubmissionConflict

    h = projected_approval
    _, successor = await settlement_successor(h)
    task = await h.tasks.claim(successor.id, worker_id="no-write-owner", expected_binding=h.binding)
    await h.tasks.begin(task, resource_id="no-writes", fencing_token=4)
    monitor = asyncio.create_task(asyncio.Event().wait())
    saved = h.stages.get(h.initial_task_id, h.session_id)
    try:
        for operation in (lambda: h.sessions.execute_claimed_approval(task, ownership_monitor=monitor),
            lambda: FrameworkRuntimeResults(h.tasks).load(task.runtime_owner),
            lambda: h.stages.projected_approval(task.runtime_owner, saved.result_reference)):
            with pytest.raises(FrameworkMessageStageRecoveryRequired):
                await operation()
        # The native port rejects even before its narrower native-proof gate,
        # since this completed Turn no longer has an active approval to resume.
        with pytest.raises(AgentSubmissionConflict, match="active framework Turn"):
            await h.runtime._framework_approval_continuation.prepare(task.runtime_owner, task.reference_id, task.input["decision_id"])
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            with pytest.raises(FrameworkApprovalRecoveryRequired, match="not native continuation"):
                require_native_recovery_scope(cur, h.settings, h.binding)
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="completed result"):
            await h.tasks.finish(task, status="completed")
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    await h.tasks.finish(task, status="completed", result_status="completed")


@pytest.mark.asyncio
async def test_normal_approval_records_projected_before_task_finish(prepared_approval_runtime):
    from tests.test_framework_approval_continuation import claim_decision

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        for key in ("B", "A"):
            task = await claim_decision(h, key=key)
            response = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
            saved = h.stages.get(h.initial_task_id, h.session_id)
            assert saved.phase == ("projected" if key == "A" else "runtime_submitted")
            await h.tasks.finish(task, status="completed", result_status=response.session.status.value)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.asyncio
async def test_projected_original_cannot_reenter_session(projected_approval):
    h = projected_approval
    before = await h.store.get_session(h.session_id)
    events = await h.store.list_events(h.session_id)
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="settlement"):
            await h.sessions.execute_claimed_approval(h.result_task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert await h.store.get_session(h.session_id) == before
    assert await h.store.list_events(h.session_id) == events


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"output_text": ""}], indirect=True)
async def test_empty_native_response_without_structured_result_is_not_committed(prepared_approval_runtime):
    from src.application.deep_agents.result_projection import DeepAgentRuntimeError
    from tests.test_framework_approval_continuation import claim_decision

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A")
        # This response has no structured completion result. Do not bypass the
        # adapter contract to fabricate a committed empty-result fixture.
        with pytest.raises(DeepAgentRuntimeError, match="no assistant message"):
            await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert saved.phase == "runtime_submitted"
    assert "runtime_result_asset" not in saved.source_manifest
    assert "application_projection_commit" not in saved.source_manifest
    assert h.effects == [("B", h.proposals["B"].job.id), ("A", h.proposals["A"].job.id)]
    assert not any(item.type == "turn.completed" and item.payload.get("session_status") == "completed"
        for item in await h.store.list_events(h.session_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["admission", "claim", "finish"])
async def test_native_producer_settlement_rechecks_reconciliation_lineage(prepared_approval_runtime, monkeypatch, boundary):
    from tests.test_framework_approval_continuation import claim_decision

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A")
        continuation = h.runtime._framework_approval_continuation
        record = continuation.record_graph_input

        async def stop_before_graph(*args, **kwargs):
            await record(*args, **kwargs)
            raise ConnectionError("injected after preparation before native recovery")

        with monkeypatch.context() as patch:
            patch.setattr(continuation, "record_graph_input", stop_before_graph)
            with pytest.raises(ConnectionError, match="before native recovery"):
                await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
        await h.tasks.finish(a, status="failed", result_status="failed")
        original = await h.tasks.get(a.id)
        reconciliation = await h.tasks.ensure_framework_reconciliation(a.id)
        await run_delivery(h, reconciliation.id)
        successor_id = (await h.tasks.get(reconciliation.id)).result_payload["successor_task_id"]
        task = await h.tasks.claim(successor_id, worker_id="native-producer", expected_binding=h.binding)
        await h.tasks.begin(task, resource_id="native-producer", fencing_token=4)
        await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    if boundary != "finish":
        await expire(h, task)
    if boundary == "claim":
        reconciliation_task = await h.tasks.ensure_framework_reconciliation(task.id)
        await run_delivery(h, reconciliation_task.id)
        settlement_id = (await h.tasks.get(reconciliation_task.id)).result_payload["successor_task_id"]
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert saved.phase == "projected"
    before = await h.tasks.get(task.id)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET result_payload=result_payload-'proof' WHERE id=%s",
            (reconciliation.id,))
        assert cur.rowcount == 1
    if boundary == "admission":
        assert await h.tasks.ensure_framework_reconciliation(task.id) is None
    elif boundary == "claim":
        rejected = await h.tasks.claim(settlement_id, worker_id="changed-native-lineage", expected_binding=h.binding)
        assert rejected.status == "recovery_required" and rejected.attempt_id is None
    else:
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="durable reconciliation proof"):
            await h.tasks.finish(task, status="completed", result_status="completed")
    assert await h.tasks.get(task.id) == before
    assert await h.tasks.get(a.id) == original
    assert h.stages.get(h.initial_task_id, h.session_id) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["admission", "claim", "finish"])
async def test_explicit_interrupt_revokes_settlement(projected_approval, boundary):
    from tests.test_framework_task_control import interrupt

    h = projected_approval
    if boundary == "admission":
        # Cancel while the producer still owns a live task. After expiry the
        # application is completed and there is no public cancellation target.
        response = await interrupt(h)
        terminal = await h.tasks.get(h.result_task.id)
        assert terminal.status == "interrupted"
    else:
        terminal, successor = await settlement_successor(h)
        if boundary == "finish":
            task = await h.tasks.claim(successor.id, worker_id="interrupt-before-finish", expected_binding=h.binding)
            await h.tasks.begin(task, resource_id="interrupt-before-finish", fencing_token=4)
        response = await interrupt(h)
    assert response.status_code == 200
    if boundary == "admission":
        assert await h.tasks.ensure_framework_reconciliation(h.result_task.id) is None
    elif boundary == "finish":
        with pytest.raises(ContinuationLeaseLostError):
            await h.tasks.finish(task, status="completed", result_status="completed")
        assert (await h.tasks.get(task.id)).status == "interrupted"
    else:
        rejected = await h.tasks.claim(successor.id, worker_id="interrupted-settlement", expected_binding=h.binding)
        assert rejected.status == "cancelled" and rejected.attempt_id is None
        assert not any(attempt["status"] == "completed" for attempt in attempts(h, successor.id))
    current = await h.tasks.get(h.result_task.id)
    assert current.status == terminal.status and current.attempt_id == terminal.attempt_id


@pytest.mark.asyncio
async def test_interrupt_after_expiry_without_active_task_cannot_rewrite_completed_result(projected_approval):
    from tests.test_framework_task_control import interrupt

    h = projected_approval
    terminal = await expire(h, h.result_task)
    saved = h.stages.get(h.initial_task_id, h.session_id)
    session = await h.store.get_session(h.session_id)
    events = await h.store.list_events(h.session_id)
    response = await interrupt(h)
    assert response.status_code == 400
    assert "Only running sessions can be interrupted" in response.text
    assert await h.store.get_session(h.session_id) == session
    assert await h.store.list_events(h.session_id) == events
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("reader,version", [(22, 23), (23, 24), (24, 25)])
async def test_approval_settlement_format_checked_before_real_claim(projected_approval, reader, version):
    from tests.test_framework_approval_graph_input import replace_stage
    from tests.test_framework_application_projection import legacy_application_commit_stage

    h = projected_approval
    if version < 25:
        original = h.stages.get(h.initial_task_id, h.session_id)
        replace_stage(h, legacy_application_commit_stage(original, version).model_dump(mode="json"))
    _, successor = await settlement_successor(h)
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert saved.schema_version == version
    assert "approval_settlements" not in saved.model_copy(update={"schema_version": 22, "approval_settlements": []}).model_dump(mode="json")
    with pytest.raises((FrameworkMessageStageRecoveryRequired, ValidationError)):
        validate_message_stage(saved.model_copy(update={"schema_version": 22}).model_dump(mode="json"))
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities(h.binding.dependency_lock_sha256, True, message_stage_max_version=reader))
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("incompatible", outbox(h, successor.id)["payload"]), worker_id="old-worker")
    assert await h.tasks.get(successor.id) == successor and attempts(h, successor.id) == []


@pytest.mark.asyncio
async def test_settlement_repeated_expiry_preserves_all_terminal_attempts(projected_approval, monkeypatch):
    h = projected_approval
    terminal, successor = await settlement_successor(h)
    history = [terminal]
    forbid_execution(h, monkeypatch)
    for number in range(2):
        task = await h.tasks.claim(successor.id, worker_id="lost-settlement", expected_binding=h.binding)
        await h.tasks.begin(task, resource_id="lost-settlement", fencing_token=number + 1)
        history.append(await expire(h, task))
        reconcile = await h.tasks.ensure_framework_reconciliation(task.id)
        assert reconcile is not None
        await run_delivery(h, reconcile.id)
        successor = await h.tasks.get((await h.tasks.get(reconcile.id)).result_payload["successor_task_id"])
    await run_delivery(h, successor.id)
    assert (await h.tasks.get(successor.id)).status == "completed"
    for old in history:
        assert await h.tasks.get(old.id) == old
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert len(saved.approval_settlements) == 3


@pytest.mark.asyncio
async def test_settlement_finish_and_publication_are_atomic(projected_approval, monkeypatch):
    h = projected_approval
    await expire(h, h.result_task)
    reconcile = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    task = await h.tasks.claim(reconcile.id, worker_id="atomic-publication", expected_binding=h.binding)
    await h.tasks.begin(task, resource_id="atomic-publication", fencing_token=4)
    before = h.stages.get(h.initial_task_id, h.session_id)
    event = h.tasks._event

    def fail(cur, row, name, payload):
        event(cur, row, name, payload)
        if name in {"framework.recovery_succeeded", "agent_task.completed"}:
            raise ConnectionError("injected approval settlement rollback")

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="settlement rollback"):
            await h.runtime.reconcile_framework_task(task, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert not await h.tasks.list_children(reconcile.id)
    successor = await h.runtime.reconcile_framework_task(task, tasks=h.tasks)
    task = await h.tasks.claim(successor.id, worker_id="atomic-finish", expected_binding=h.binding)
    await h.tasks.begin(task, resource_id="atomic-finish", fencing_token=5)
    session, before = await h.store.get_session(h.session_id), await h.tasks.get(task.id)
    old_attempts = attempts(h, task.id)
    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="settlement rollback"):
            await h.tasks.finish(task, status="completed", result_status="completed")
    assert await h.tasks.get(task.id) == before and attempts(h, task.id) == old_attempts
    assert await h.store.get_session(h.session_id) == session
    await h.tasks.finish(task, status="completed", result_status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("later_started", [False, True])
async def test_settlement_respects_later_input_without_overwriting_it(projected_approval, monkeypatch, later_started):
    from types import SimpleNamespace
    from tests.test_framework_task_control import owner_row
    from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload

    h = projected_approval
    terminal = await expire(h, h.result_task)
    saved = h.stages.get(h.initial_task_id, h.session_id)
    binding = make_binding(h.session_id, "later-settlement-turn")
    later = await h.tasks.submit_v2(make_command(h, binding),
        make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, binding),
        idempotency_key="later-settlement-turn")
    later = await h.tasks.get(later.id)
    await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
        mutate=lambda metadata, status: metadata.update(unrelated_preference="keep"))
    if later_started:
        later = await h.tasks.claim(later.id, worker_id="later-settlement-owner", expected_binding=binding)
        assert later.status == "claimed"
        later_owner = owner_row(SimpleNamespace(settings=h.settings, binding=binding))
        # claim returns the captured owner capability; get intentionally does
        # not recreate it. Compare persisted reads with persisted reads.
        later = await h.tasks.get(later.id)
    before = await h.store.get_session(h.session_id)
    reconciliation = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    if later_started:
        assert reconciliation is None
        assert await h.store.get_session(h.session_id) == before
        assert h.stages.get(h.initial_task_id, h.session_id) == saved
    else:
        assert reconciliation is not None
        forbid_execution(h, monkeypatch)
        await run_delivery(h, reconciliation.id)
        reconciled = await h.tasks.get(reconciliation.id)
        assert reconciled.status == "completed", reconciled.error_code
        await run_delivery(h, reconciled.result_payload["successor_task_id"])
        completed = await h.tasks.get(reconciled.result_payload["successor_task_id"])
        assert completed.status == "completed", completed.error_code
        after = await h.store.get_session(h.session_id)
        assert after.messages == before.messages and after.metadata["unrelated_preference"] == "keep"
        assert h.stages.get(h.initial_task_id, h.session_id).source_manifest == saved.source_manifest
    assert await h.tasks.get(later.id) == later
    if later_started:
        assert owner_row(SimpleNamespace(settings=h.settings, binding=binding)) == later_owner
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover_point", [None, "settlement_after_claim", "settlement_before_finish", "settlement_after_finish"])
async def test_independent_workers_only_settle_after_projected_approval_kill(prepared_approval_runtime, takeover_point):
    import multiprocessing
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.schemas.session import ApprovalDecisionRequest
    from tests.test_framework_approval_continuation import claim_decision, receipt
    from tests.test_framework_partial_projection_recovery import reap_dead_worker_resources
    from tests.test_framework_task_control import framework_worker_process, wait_gate

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        task = await claim_decision(h)
        await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
        await h.tasks.finish(task, status="completed", result_status="waiting_approval")
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    h.settings.orchestration.agent_task_v2_stream = f"{h.prefix}:settlement-kill:v2"
    h.settings.orchestration.resource_default_lease_seconds = 3
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="control-v2", client=h.redis)
    await queue.connect()
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["A"].approval.id,
        ApprovalDecisionRequest(decision="approved"))
    assert await h.outbox.relay_once(queue) == 1
    processes = []

    async def worker(point, mode=None):
        process = multiprocessing.get_context("spawn").Process(target=framework_worker_process,
            args=(h.settings, h.prefix, h.binding, point, mode))
        processes.append(process)
        process.start()
        if point == "none":
            await asyncio.to_thread(process.join, 60)
            assert not process.is_alive() and process.exitcode == 0
        else:
            phase = await wait_gate(h.redis, f"{h.prefix}:control:entered", 40, process=process)
            assert phase[1] == point, f"Worker exited {process.exitcode} before {point}"
        return process

    async def kill(process):
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive() and process.exitcode != 0

    try:
        process = await worker("after_projected")
        original = await h.tasks.get(accepted.task.id)
        assert original.status == "running"
        before = h.stages.get(h.initial_task_id, h.session_id)
        assert before.phase == "projected"
        snapshot, budget = await h.store.get_latest_snapshot(h.session_id), await h.calls.snapshot(h.binding)
        jobs = [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")]
        receipts = [receipt(h, key) for key in ("A", "B")]
        await kill(process)
        terminal = await expire(h, original)
        old_attempts = attempts(h, original.id)
        await reap_dead_worker_resources(h)
        await run_delivery(h, original.id)
        reconciler = next(item for item in await h.tasks.list_session(h.session_id)
            if item.operation == "framework_reconcile" and item.reference_id == original.id)
        assert await h.outbox.relay_once(queue) == 1
        await worker("none", "settle")
        reconciled = await h.tasks.get(reconciler.id)
        successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
        assert successor.input["recovery_proof"]["scope"] == "application_settlement"
        assert await h.outbox.relay_once(queue) == 1
        interrupted_successor = None
        if takeover_point is not None:
            process = await worker(takeover_point, "settle")
            current = await h.tasks.get(successor.id)
            await kill(process)
            if takeover_point == "settlement_before_finish":
                interrupted_successor = await expire(h, current)
                await reap_dead_worker_resources(h)
                await run_delivery(h, current.id)
                reconciler = next(item for item in await h.tasks.list_session(h.session_id)
                    if item.operation == "framework_reconcile" and item.reference_id == current.id)
                assert await h.outbox.relay_once(queue) == 1
                await worker("none", "settle")
                successor = await h.tasks.get((await h.tasks.get(reconciler.id)).result_payload["successor_task_id"])
                assert await h.outbox.relay_once(queue) == 1
            elif takeover_point == "settlement_after_claim":
                assert current.status == "claimed"
                assert await h.store.renew_turn_execution(h.session_id, current.lease_token, 0)
            else:
                assert current.status == "completed"
        await worker("none", "settle-drain")
        completed = await h.tasks.get(successor.id)
        assert completed.status == completed.result_status == "completed"
        await queue.enqueue(outbox(h, successor.id)["payload"])
        await worker("none", "settle-drain")
        assert await queue.pending() == []
        assert await h.tasks.get(original.id) == terminal and attempts(h, original.id) == old_attempts
        if interrupted_successor is not None:
            assert await h.tasks.get(interrupted_successor.id) == interrupted_successor
        if takeover_point == "settlement_after_claim":
            history = attempts(h, successor.id)
            assert len(history) == 2 and history[0]["status"] == "interrupted" and history[1]["status"] == "completed"
        assert await h.calls.snapshot(h.binding) == budget
        assert await h.store.get_latest_snapshot(h.session_id) == snapshot
        assert [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")] == jobs
        assert [receipt(h, key) for key in ("A", "B")] == receipts
        saved = h.stages.get(h.initial_task_id, h.session_id)
        assert saved.phase == "projected" and saved.source_manifest == before.source_manifest
        assert await h.redis.get(f"{h.prefix}:framework-adapter-calls") == "1"
        assert await h.redis.get(f"{h.prefix}:framework-effects") == "1"
        events = await h.store.list_events(h.session_id)
        assert len([event for event in events if event.type == "turn.completed" and event.payload.get("session_status") == "completed"]) == 1
        assert len([event for event in events if event.type == "agent_task.completed" and event.payload["task_id"] == successor.id]) == 1
    finally:
        await h.redis.rpush(f"{h.prefix}:control:release", "continue")
        for process in processes:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 10)
        await queue.close()

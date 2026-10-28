from __future__ import annotations

import asyncio
import multiprocessing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommand, parse_agent_task_command
from src.schemas.session import ApprovalDecisionRequest
from src.schemas.session import SessionStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt
from tests.test_framework_approval_runtime import runtime_harness, resume
from tests.test_framework_task_control import interrupt, framework_worker_process, wait_gate
from tests.test_framework_approval_progress import progress
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,expected", [("approval_recover", True), ("framework_reconcile", False)])
async def test_coordinator_keeps_recovered_execution_parent_but_not_reconciliation(operation, expected):
    from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService
    from src.core.config import Settings
    from src.registry.agents import AgentRegistry

    parent = SimpleNamespace(id="recovered-parent", turn_id="turn", operation=operation, status="running")
    coordinator = CoordinatorRuntimeService(Settings(), None, None, AgentRegistry())
    coordinator.set_agent_task_store(SimpleNamespace(list_session=AsyncMock(return_value=[parent])))
    actual = await coordinator._resolve_parent_task("session", "turn")
    assert actual == (parent if expected else None)


async def expire(h, task):
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    expired = await h.tasks.claim(task.id, worker_id="expiry-observer", expected_binding=h.binding)
    assert expired.status == "recovery_required" and expired.attempt_no == task.attempt_no
    return await h.tasks.get(task.id)


def outbox(h, task_id):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{task_id}",))
        return cur.fetchone()


async def run_delivery(h, task_id):
    h.sessions._runtime_service = h.runtime
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities(h.binding.dependency_lock_sha256, True))
    row = outbox(h, task_id)
    await h.dispatcher.handle_v2(QueuedTask("recovery-delivery", row["payload"]), worker_id="recovery-worker")


@pytest.mark.asyncio
async def test_terminal_origin_creates_one_reconciliation_and_v2_outbox(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    before = await expire(h, task)
    queued = await asyncio.gather(*(h.tasks.ensure_framework_reconciliation(task.id) for _ in range(3)))
    assert len({item.id for item in queued}) == 1
    recovery = queued[0]
    assert recovery.operation == "framework_reconcile" and recovery.status == "queued"
    assert recovery.parent_task_id == task.id and recovery.reference_id == task.id
    assert recovery.runtime_binding == h.binding and recovery.input["origin_attempt_id"] == task.attempt_id
    assert await h.tasks.get(task.id) == before
    row = outbox(h, recovery.id)
    command = parse_agent_task_command(row["payload"])
    assert command.operation == "framework_reconcile" and row["stream"] == h.settings.orchestration.agent_task_v2_stream
    assert receipt(h).applied_at is None and h.effects == []
    with pytest.raises(ValueError, match="operation"):
        AgentTaskCommand.model_validate({**command.model_dump(mode="json", exclude={"runtime_binding"}), "schema_version": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_approval_delivery_admits_reconciliation_without_another_redelivery(runtime_harness, monkeypatch, raises):
    h = runtime_harness
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["B"].approval.id,
        ApprovalDecisionRequest(decision="approved"))

    async def fail(*args, **kwargs):
        if raises:
            raise ConnectionError("controlled approval delivery failure")
        return SimpleNamespace(session=SimpleNamespace(status=SessionStatus.failed, metadata={}))

    monkeypatch.setattr(h.sessions, "execute_claimed_approval", fail)
    await run_delivery(h, accepted.task.id)
    origin = await h.tasks.get(accepted.task.id)
    assert origin.status == "failed"
    reconciliations = [item for item in await h.tasks.list_session(h.session_id)
        if item.operation == "framework_reconcile"]
    assert len(reconciliations) == 1 and reconciliations[0].reference_id == origin.id
    assert outbox(h, reconciliations[0].id) is not None and h.effects == []


async def expire_reconciliation(h):
    origin = await claim_decision(h)
    original = await expire(h, origin)
    recovery = await h.tasks.ensure_framework_reconciliation(origin.id)
    claimed = await h.tasks.claim(recovery.id, worker_id="lost-reconciler", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="lost-reconcile", fencing_token=6)
    expired = await expire(h, claimed)
    return original, claimed, expired


@pytest.mark.asyncio
@pytest.mark.parametrize("crashes", [1, 3])
async def test_expired_reconciliation_gets_one_new_task_and_preserves_both_terminal_attempts(runtime_harness, crashes):
    h = runtime_harness
    original, old, expired = await expire_reconciliation(h)
    history = [expired]
    for _ in range(crashes - 1):
        candidate = await h.tasks.ensure_framework_reconciliation(expired.id)
        claimed = await h.tasks.claim(candidate.id, worker_id="another-lost-reconciler", expected_binding=h.binding)
        await h.tasks.begin(claimed, resource_id="another-lost-reconcile", fencing_token=7)
        expired = await expire(h, claimed)
        history.append(expired)
    # The actual terminal delivery, rather than a test-only recovery entry, admits its successor.
    await run_delivery(h, expired.id)
    next_tasks = await asyncio.gather(*(h.tasks.ensure_framework_reconciliation(expired.id) for _ in range(3)))
    assert all(item is not None for item in next_tasks)
    assert len({item.id for item in next_tasks}) == 1
    successor = next_tasks[0]
    assert successor.id != expired.id and successor.operation == "framework_reconcile"
    assert successor.parent_task_id == expired.id and successor.reference_id == original.id
    assert successor.input["origin_task_id"] == original.id
    assert successor.input["origin_attempt_id"] == original.attempt_id
    assert successor.input["predecessor_task_id"] == expired.id
    assert successor.input["predecessor_attempt_id"] == expired.attempt_id
    assert await h.tasks.get(original.id) == original and await h.tasks.get(expired.id) == expired
    await run_delivery(h, successor.id)
    reconciled = await h.tasks.get(successor.id)
    assert reconciled.status == "completed"
    await run_delivery(h, reconciled.result_payload["successor_task_id"])
    assert len(h.effects) == 1 and receipt(h).applied_at is not None
    with pytest.raises(ContinuationLeaseLostError):
        await h.runtime.reconcile_framework_task(old, tasks=h.tasks)
    assert await h.tasks.get(original.id) == original and await h.tasks.get(expired.id) == expired
    for terminal in history:
        assert await h.tasks.get(terminal.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["FrameworkApprovalRecoveryRequired", "ConnectionError"])
async def test_reconciliation_failure_without_expiry_does_not_generate_retry_chain(runtime_harness, error_code):
    h = runtime_harness
    origin = await claim_decision(h)
    await expire(h, origin)
    recovery = await h.tasks.ensure_framework_reconciliation(origin.id)
    claimed = await h.tasks.claim(recovery.id, worker_id="rejected-reconciler", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="rejected-reconcile", fencing_token=6)
    await h.tasks.finish(claimed, status="recovery_required", error_code=error_code)
    before = await h.tasks.get(recovery.id)
    await run_delivery(h, recovery.id)
    assert await h.tasks.ensure_framework_reconciliation(recovery.id) is None
    assert await h.tasks.get(recovery.id) == before
    assert len([item for item in await h.tasks.list_session(h.session_id)
        if item.operation == "framework_reconcile"]) == 1
    assert h.effects == []


@pytest.mark.asyncio
async def test_reconciliation_retry_publication_is_atomic_and_repeatable(runtime_harness, monkeypatch):
    h = runtime_harness
    original, old, expired = await expire_reconciliation(h)
    append_event = h.tasks._event

    def fail(cur, row, event_type, payload):
        if event_type == "framework.framework_reconcile_queued":
            raise ConnectionError("controlled retry publication rollback")
        return append_event(cur, row, event_type, payload)

    monkeypatch.setattr(h.tasks, "_event", fail)
    with pytest.raises(ConnectionError, match="retry publication rollback"):
        await h.tasks.ensure_framework_reconciliation(expired.id)
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.parent_task_id == expired.id]
    monkeypatch.setattr(h.tasks, "_event", append_event)
    next_task = await h.tasks.ensure_framework_reconciliation(expired.id)
    assert next_task is not None and outbox(h, next_task.id) is not None
    assert await h.tasks.get(original.id) == original and await h.tasks.get(expired.id) == expired


@pytest.mark.asyncio
async def test_explicit_interrupt_blocks_successor_of_expired_reconciliation(runtime_harness):
    h = runtime_harness
    original, old, expired = await expire_reconciliation(h)
    assert (await interrupt(h)).status_code == 200
    assert await h.tasks.ensure_framework_reconciliation(expired.id) is None
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.parent_task_id == expired.id]
    assert await h.tasks.get(expired.id) == expired and h.effects == []


@pytest.mark.asyncio
async def test_reconciliation_successor_with_wrong_predecessor_attempt_cannot_claim(runtime_harness):
    h = runtime_harness
    original, old, expired = await expire_reconciliation(h)
    successor = await h.tasks.ensure_framework_reconciliation(expired.id)
    assert successor is not None
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET input_payload="
            "jsonb_set(input_payload,'{predecessor_attempt_id}','\"unrelated-attempt\"') WHERE id=%s", (successor.id,))
    saved = await h.tasks.claim(successor.id, worker_id="invalid-reconciler", expected_binding=h.binding)
    assert saved.status == "recovery_required" and saved.attempt_no == 0
    assert await h.tasks.ensure_framework_reconciliation(saved.id) is None and h.effects == []


@pytest.mark.asyncio
async def test_explicit_interrupt_does_not_create_automatic_recovery(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    assert (await interrupt(h)).status_code == 200
    assert await h.tasks.ensure_framework_reconciliation(task.id) is None
    assert (await h.tasks.get(task.id)).status == "interrupted" and h.effects == []


@pytest.mark.asyncio
async def test_unknown_started_effect_blocks_reconciliation_and_keeps_original_facts(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    original = await expire(h, task)
    with framework_owner_scope(task.runtime_owner), pytest.raises(ContinuationLeaseLostError):
        await h.jobs.observe_output(job.id, status="completed", output_payload={"evidence": "late observation only"})
    assert len(await h.jobs.list_artifacts(tool_job_id=job.id)) == 1
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    await run_delivery(h, recovery.id)
    saved = await h.tasks.get(recovery.id)
    assert saved.status == "recovery_required" and saved.result_payload is None
    assert await h.jobs.get_job(job.id) == job and await h.tasks.get(task.id) == original
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.operation == "approval_recover"]
    assert receipt(h).applied_at is None and h.effects == []


@pytest.mark.asyncio
async def test_sibling_cannot_overtake_reconciliation_or_its_successor(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    sibling = await h.dispatcher.submit_approval(h.session_id, h.proposals["A"].approval.id,
        ApprovalDecisionRequest(decision="denied"))
    await expire(h, task)
    # Terminal original delivery must admit recovery itself; no manual test-only entry.
    await run_delivery(h, task.id)
    recovery = next(item for item in await h.tasks.list_session(h.session_id) if item.operation == "framework_reconcile")
    with pytest.raises(TaskDeferred):
        await h.tasks.claim(sibling.task.id, worker_id="early-sibling", expected_binding=h.binding)
    await run_delivery(h, recovery.id)
    successor_id = (await h.tasks.get(recovery.id)).result_payload["successor_task_id"]
    with pytest.raises(TaskDeferred):
        await h.tasks.claim(sibling.task.id, worker_id="early-sibling", expected_binding=h.binding)
    await run_delivery(h, successor_id)
    await run_delivery(h, sibling.task.id)
    assert receipt(h).applied_at is not None and receipt(h, "A").applied_at is not None
    assert len(h.effects) == 1 and (await h.store.get_session(h.session_id)).metadata.get("pending_turn") == {}


@pytest.mark.asyncio
async def test_unconsumed_decision_gets_successor_without_resetting_origin(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    original = await expire(h, task)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    await run_delivery(h, recovery.id)
    saved = await h.tasks.get(recovery.id)
    assert saved.status == "completed" and saved.result_status == "reconciled"
    successor = await h.tasks.get(saved.result_payload["successor_task_id"])
    assert successor.operation == "approval_recover" and successor.status == "queued"
    assert successor.parent_task_id == recovery.id and successor.reference_id == task.reference_id
    assert successor.input["decision_id"] == task.input["decision_id"]
    assert successor.input["origin_task_id"] == task.id and h.effects == []
    snapshot = await progress(h)
    assert len(snapshot) == 2 and snapshot[task.reference_id]["task_status"] == "queued"
    assert snapshot[task.reference_id]["phase"] == "decision_submitted"
    assert await h.tasks.get(task.id) == original
    await run_delivery(h, successor.id)
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert h.effects == [("B", h.proposals["B"].job.id)] and receipt(h).applied_at is not None
    await run_delivery(h, recovery.id)
    await run_delivery(h, successor.id)
    assert h.effects == [("B", h.proposals["B"].job.id)]


@pytest.mark.asyncio
async def test_native_result_survives_lost_projection_and_sibling_finishes(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    continuation = h.runtime._framework_approval_continuation
    settle = continuation.settle

    async def fail(*args):
        raise ConnectionError("controlled business projection lost")

    monkeypatch.setattr(continuation, "settle", fail)
    with pytest.raises(ConnectionError, match="projection lost"):
        await resume(h, task)
    monkeypatch.setattr(continuation, "settle", settle)
    assert receipt(h).state == "delivered" and receipt(h).applied_at is None and len(h.effects) == 1
    original = await expire(h, task)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    await run_delivery(h, recovery.id)
    successor_id = (await h.tasks.get(recovery.id)).result_payload["successor_task_id"]
    await run_delivery(h, successor_id)
    assert h.adapter_requests[-1].continue_from_checkpoint and h.adapter_requests[-1].resume is None
    assert receipt(h).applied_at is not None and len(h.effects) == 1
    a = await h.dispatcher.submit_approval(h.session_id, h.proposals["A"].approval.id,
        ApprovalDecisionRequest(decision="denied"))
    await run_delivery(h, a.task.id)
    assert (await h.store.get_session(h.session_id)).metadata.get("pending_turn") == {}
    assert receipt(h, "A").applied_at is not None and len(h.effects) == 1
    assert await h.tasks.get(task.id) == original
    with framework_owner_scope(task.runtime_owner), pytest.raises(ContinuationLeaseLostError):
        await h.jobs.mark_completed(h.proposals["B"].job.id, "stale overwrite", {"value": "wrong"})


@pytest.mark.asyncio
async def test_recovery_publication_failure_rolls_back_successor_and_control(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    await expire(h, task)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    claimed = await h.tasks.claim(recovery.id, worker_id="reconciler", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="reconcile", fencing_token=5)
    before = await h.store.get_session(h.session_id)
    original_event = h.tasks._event

    def fail(cur, row, event_type, payload):
        if event_type == "framework.recovery_succeeded":
            raise ConnectionError("controlled recovery commit failure")
        return original_event(cur, row, event_type, payload)

    monkeypatch.setattr(h.tasks, "_event", fail)
    with pytest.raises(ConnectionError, match="recovery commit failure"):
        await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert (await h.tasks.get(recovery.id)).status == "running"
    assert (await h.store.get_session(h.session_id)).metadata == before.metadata
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.operation == "approval_recover"]
    assert receipt(h).state == "queued" and h.effects == []


@pytest.mark.asyncio
async def test_future_turn_cannot_replace_the_failed_turn_before_reconciliation(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    binding = make_binding(h.session_id, "future-turn")
    future = await h.tasks.submit_v2(make_command(h, binding), make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, binding),
        idempotency_key="future-turn")
    await expire(h, task)
    with pytest.raises(TaskDeferred):
        await h.tasks.claim(future.id, worker_id="future-worker", expected_binding=binding)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    await run_delivery(h, recovery.id)
    successor_id = (await h.tasks.get(recovery.id)).result_payload["successor_task_id"]
    await run_delivery(h, successor_id)
    assert receipt(h).applied_at is not None and len(h.effects) == 1
    assert (await h.tasks.get(future.id)).status == "queued"


@pytest.mark.asyncio
async def test_internal_recovery_tasks_do_not_expose_their_input_as_user_queue(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    await expire(h, task)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    for stage in ("reconciliation", "successor"):
        if stage == "successor":
            await run_delivery(h, recovery.id)
        detail = await h.sessions.get_session(h.session_id)
        assert not [item for item in detail.metadata["pending_input_queue"]
            if item["metadata"].get("task_operation") in {"framework_reconcile", "approval_recover"}]


@pytest.mark.asyncio
async def test_corrupt_successor_proof_cannot_claim_execution(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    await expire(h, task)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    await run_delivery(h, recovery.id)
    successor_id = (await h.tasks.get(recovery.id)).result_payload["successor_task_id"]
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET result_payload="
            "jsonb_set(result_payload,'{proof,decision_hash}','\"corrupt\"') WHERE id=%s", (recovery.id,))
    saved = await h.tasks.claim(successor_id, worker_id="invalid-successor", expected_binding=h.binding)
    assert saved.status == "recovery_required" and saved.attempt_no == 0
    assert h.effects == [] and receipt(h).applied_at is None


@pytest.mark.asyncio
async def test_explicit_interrupt_revokes_reconciler_before_publication(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    await expire(h, task)
    recovery = await h.tasks.ensure_framework_reconciliation(task.id)
    claimed = await h.tasks.claim(recovery.id, worker_id="reconciler", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="reconcile", fencing_token=5)
    assert (await interrupt(h)).status_code == 200
    with pytest.raises(ContinuationLeaseLostError):
        await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert (await h.tasks.get(recovery.id)).status == "interrupted"
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.operation == "approval_recover"]
    assert h.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reconcile_pause", ["none", "reconcile_before_commit", "reconcile_after_commit"])
@pytest.mark.parametrize("decision,pause_at", [("approved", "after_native"), ("denied", "after_applied")])
async def test_separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling(runtime_harness, reconcile_pause, decision, pause_at):
    h = runtime_harness
    if decision == "denied":
        session = await h.store.get_session(h.session_id)
        for state in (session.metadata["pending_turn"], session.metadata["pending_turn"]["graph_state"]):
            state["context_bundle"]["framework_tool_projection_version"] = 2
        await h.store.save_session(session)
    h.settings.orchestration.agent_task_v2_stream = f"{h.prefix}:recovery:v2"
    h.settings.orchestration.resource_default_lease_seconds = 3
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="control-v2", client=h.redis)
    await queue.connect()
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["B"].approval.id,
        ApprovalDecisionRequest(decision=decision))
    assert await h.outbox.relay_once(queue) == 1
    ctx = multiprocessing.get_context("spawn")
    processes = []

    async def replacement(pause_at="none"):
        assert await h.outbox.relay_once(queue) == 1
        process = ctx.Process(target=framework_worker_process, args=(h.settings, h.prefix, h.binding, pause_at))
        processes.append(process)
        process.start()
        if pause_at != "none":
            phase = await wait_gate(h.redis, f"{h.prefix}:control:entered", 20)
            assert phase[1] == pause_at, f"worker exit code: {process.exitcode}"
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode is not None and process.exitcode != 0
            return
        await asyncio.to_thread(process.join, 20)
        exitcode = process.exitcode
        if exitcode is None:
            # Keep the original deadline, but preserve authoritative task
            # phases before finally terminates the still-live test Worker.
            phases = [(item.operation, item.status, item.result_status, item.error_code)
                for item in await h.tasks.list_session(h.session_id)]
            pytest.fail(f"Replacement Worker exceeded its 20-second deadline: pid={process.pid}, "
                f"alive={process.is_alive()}, task_phases={phases}")
        assert exitcode == 0

    process = ctx.Process(target=framework_worker_process, args=(h.settings, h.prefix, h.binding, pause_at))
    processes.append(process)
    try:
        process.start()
        phase = await wait_gate(h.redis, f"{h.prefix}:control:entered", 20)
        assert phase[1] == pause_at, f"worker exit code: {process.exitcode}"
        job = await h.jobs.get_job(h.proposals["B"].job.id)
        assert job.status.value == ("completed" if decision == "approved" else "denied")
        original_receipt = receipt(h)
        assert original_receipt.state == "delivered"
        assert (original_receipt.applied_at is None) is (decision == "approved")
        if decision == "denied":
            from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY

            material = FrameworkToolProjection.model_validate(original_receipt.proof["application"][TOOL_PROJECTION_KEY])
            material.require_binding(h.binding, h.proposals["B"].link)
            assert material.message.created_at == material.record.completed_at == job.completed_at
            assert job.started_at is None
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert process.exitcode is not None and process.exitcode != 0
        original = await expire(h, await h.tasks.get(accepted.task.id))
        await run_delivery(h, accepted.task.id)
        recovery = next(item for item in await h.tasks.list_session(h.session_id) if item.operation == "framework_reconcile")
        await replacement(reconcile_pause)
        recovered = await h.tasks.get(recovery.id)
        if reconcile_pause == "reconcile_before_commit":
            assert recovered.status == "running" and recovered.result_payload is None
            assert not [item for item in await h.tasks.list_session(h.session_id) if item.operation == "approval_recover"]
            expired_reconciler = await expire(h, recovered)
            await run_delivery(h, recovered.id)
            retry = next(item for item in await h.tasks.list_session(h.session_id)
                if item.operation == "framework_reconcile" and item.parent_task_id == recovered.id)
            await replacement()
            recovered = await h.tasks.get(retry.id)
            assert await h.tasks.get(expired_reconciler.id) == expired_reconciler
        elif reconcile_pause == "reconcile_after_commit":
            assert recovered.status == "completed" and recovered.result_payload is not None
            await run_delivery(h, recovered.id)
            assert not [item for item in await h.tasks.list_session(h.session_id)
                if item.operation == "framework_reconcile" and item.parent_task_id == recovered.id]
        assert recovered.status == "completed" and recovered.result_status == "reconciled"
        await replacement()
        assert (await h.tasks.get(recovered.result_payload["successor_task_id"])).status == "completed"
        expected_effects = "1" if decision == "approved" else None
        assert receipt(h).applied_at is not None and await h.redis.get(f"{h.prefix}:framework-effects") == expected_effects
        if decision == "denied":
            assert receipt(h) == original_receipt
            assert await h.jobs.get_job(job.id) == job
            saved = await h.store.get_session(h.session_id)
            assert next(message for message in saved.messages if message.id == material.message.id) == material.message
        a = await h.dispatcher.submit_approval(h.session_id, h.proposals["A"].approval.id,
            ApprovalDecisionRequest(decision="denied"))
        await replacement()
        assert (await h.tasks.get(a.task.id)).status == "completed" and receipt(h, "A").applied_at is not None
        assert (await h.store.get_session(h.session_id)).metadata.get("pending_turn") == {}
        assert await h.tasks.get(accepted.task.id) == original
        assert await h.redis.get(f"{h.prefix}:framework-effects") == expected_effects
    finally:
        await h.redis.rpush(f"{h.prefix}:control:release", "continue")
        for child in processes:
            if child.is_alive():
                child.terminate()
            await asyncio.to_thread(child.join, 5)

from __future__ import annotations

import asyncio
import multiprocessing
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.base import empty_checkpoint

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.execution_context import framework_owner_scope
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt
from tests.test_framework_approval_runtime import runtime_harness, resume
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


async def wait_gate(redis, key, timeout):
    # Keep each Redis wait below the installed client's 5-second socket timeout.
    async with asyncio.timeout(timeout):
        while True:
            value = await redis.blpop(key, timeout=1)
            if value is not None:
                return value


def framework_worker_process(settings, prefix, binding, pause_at, recovery_mode=None):
    """A real v2 Redis Worker with official graph/storage and a counted fake tool."""
    from redis.asyncio import Redis
    from redis import Redis as SyncRedis
    from time import monotonic
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.runtime.tool_job_service import ToolJobService
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.registry.tools import ToolRegistry
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from src.schemas.agent import ToolDescriptor
    from tests.test_agent_task_dispatch import build_services, Runtime
    from tests.test_framework_approval_runtime import build_runtime

    async def run():
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
        await resources.connect()
        h = SimpleNamespace(settings=settings, effects=[], registry=ToolRegistry())
        h.store, tasks, sessions, dispatcher = build_services(settings, Runtime(), resources)
        h.jobs = ToolJobService(PostgresToolJobStore(settings))
        h.provider = DeepAgentCheckpointProvider(settings)
        h.registry.register_dynamic(ToolDescriptor(key="inspect_value", name="Inspect value", category="execution",
            description="Inspect a test value", permission_level="ask", input_schema={"type": "object",
                "properties": {"value": {"type": "string"}}, "required": ["value"]}), "inspect_value")
        build_runtime(h)
        sessions._runtime_service = h.runtime
        dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities(binding.dependency_lock_sha256, True))
        prepare = h.runtime._framework_approval_continuation.prepare
        settle = h.runtime._framework_approval_continuation.settle
        reconcile = h.runtime.reconcile_framework_task
        append_event = tasks._event
        adapter = h.runtime._deep_agent_bound_runtime_adapter
        execute_adapter = adapter.execute

        async def pause():
            # A killed child may retain a multiprocessing.Event semaphore. Redis
            # gates survive termination without a parent touching that semaphore.
            await redis.rpush(f"{prefix}:control:entered", pause_at)
            await wait_gate(redis, f"{prefix}:control:release", 25)

        async def prepare_with_pause(*args):
            if pause_at == "before_prepare":
                await pause()
            return await prepare(*args)

        async def counted_tool(arguments, context):
            await redis.incr(f"{prefix}:framework-effects")
            if pause_at == "after_effect":
                await pause()
            return {"status": "completed", "summary": "cross-process tool evidence", "value": arguments["value"]}

        async def settle_with_pause(*args, **kwargs):
            if pause_at == "after_native":
                await pause()
            result = await settle(*args, **kwargs)
            if pause_at == "after_applied":
                await pause()
            return result

        def event_with_pause(cur, row, event_type, payload):
            append_event(cur, row, event_type, payload)
            if pause_at == "reconcile_before_commit" and event_type == "framework.recovery_succeeded":
                # This hook runs inside the actual PostgreSQL transaction thread.
                # Kill after all writes, before commit; no mock rollback.
                with SyncRedis.from_url(settings.database.redis_url, decode_responses=True) as gate:
                    gate.rpush(f"{prefix}:control:entered", pause_at)
                    deadline = monotonic() + 25
                    while monotonic() < deadline:
                        if gate.blpop(f"{prefix}:control:release", timeout=1) is not None:
                            break
                    else:
                        raise TimeoutError("Reconciliation commit gate timed out")

        async def reconcile_with_pause(*args, **kwargs):
            result = await reconcile(*args, **kwargs)
            if pause_at == "reconcile_after_commit":
                await pause()
            return result

        async def counted_adapter(*args, **kwargs):
            await redis.incr(f"{prefix}:framework-adapter-calls")
            return await execute_adapter(*args, **kwargs)

        adapter.execute = counted_adapter
        if recovery_mode is not None:
            def forbidden(*args, **kwargs):
                raise AssertionError("Finalization-only Worker cannot rerun graph/model/tool/verification/orchestration")

            h.runtime.execute_turn = h.runtime.resume_framework_approval = forbidden
            adapter.execute = forbidden
            adapter._model_resolver = forbidden
            h.runtime._tool_runtime_service.record_from_job = forbidden
            sessions._input_orchestrator_service.orchestrate_async = forbidden
            sessions._verification_service.build_results = forbidden
            if recovery_mode in {"ack", "drain"}:
                sessions._finalize_runtime_result = forbidden
        if pause_at == "before_asset":
            import json
            from src.runtime.framework_runtime_results import FrameworkRuntimeResults
            from src.schemas.framework_runtime_result import FrameworkRuntimeResult

            async def before_asset(self, owner, **kwargs):
                # Durable comparison oracle only; never supplied to recovery.
                await redis.set(f"{prefix}:asset-gap-oracle", json.dumps({
                    "runtime_result": FrameworkRuntimeResult.from_result(kwargs["runtime_result"]).model_dump(mode="json"),
                    "verification_results": [item.model_dump(mode="json") for item in kwargs["verification_results"]]}))
                await pause()
                raise AssertionError("Original Worker must be killed before asset persistence")

            FrameworkRuntimeResults.freeze = before_asset
        elif pause_at == "after_asset":
            async def before_finalization(**kwargs):
                await pause()
                raise AssertionError("Asset fault Worker must be killed before finalization")
            sessions._finalize_runtime_result = before_finalization
        elif pause_at in {"after_snapshot", "after_application_commit"}:
            method = "save_snapshot" if pause_at == "after_snapshot" else "save_session_with_events"
            save = getattr(sessions._store, method)
            async def save_and_pause(*args, **kwargs):
                result = await save(*args, **kwargs)
                await pause()
                return result
            setattr(sessions._store, method, save_and_pause)

        h.runtime._framework_approval_continuation.prepare = prepare_with_pause
        h.runtime._framework_approval_continuation.settle = settle_with_pause
        h.runtime.reconcile_framework_task = reconcile_with_pause
        tasks._event = event_with_pause
        h.runtime._tool_runtime_service._handlers["inspect_value"] = counted_tool
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
            group="control-v2", client=redis)
        await queue.connect()
        try:
            worker = RedisTaskWorker(queue, consumer="separate-framework-worker",
                handler=lambda delivery: dispatcher.handle_v2(delivery, worker_id="separate-framework-worker"), block_ms=1,
                batch_size=100 if recovery_mode == "drain" else 1,
                reclaim_idle_ms=1 if recovery_mode == "drain" else 120000)
            # Revocation fences the old attempt, so its delivery remains pending
            # (TaskDeferred). A later terminal redelivery will ACK without work.
            processed = await worker.run_once()
            if recovery_mode == "drain":
                assert processed >= 1
            else:
                assert processed == (0 if pause_at == "before_prepare" else 1)
        finally:
            await h.provider.close()
            await queue.close()
            await resources.close()

    asyncio.run(run())


async def interrupt(h):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        return await client.post(f"/sessions/{h.session_id}/interrupt", json={"source": "test", "reason": "stop now"})


def owner_row(h):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT *, expires_at <= clock_timestamp() AS expired "
            f"FROM {h.settings.database.postgres_agent_task_table}_runtime WHERE binding_id=%s", (h.binding.binding_id,))
        return cur.fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_http_interrupt_revokes_owner_before_worker_poll_and_blocks_late_writes(checkpoint_harness, started):
    h = checkpoint_harness
    if started:
        await h.tasks.begin(h.owner, resource_id="control-test", fencing_token=1)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        checkpoint = await saver.aput(config(h), empty_checkpoint(), {}, {}) if started else None
        response = await interrupt(h)
        assert response.status_code == 200, response.text
        assert response.json()["control_state"] == "interrupted"
        assert not response.json()["is_resumable"]
        saved = await h.tasks.get(h.owner.id)
        assert saved.status == "interrupted" and saved.attempt_no == 1
        assert await h.tasks.interrupt_requested(saved.id)
        assert owner_row(h)["execution_state"] == "interrupted" and owner_row(h)["expired"]
        assert not await h.tasks.renew_framework_execution(h.owner, 60)
        with pytest.raises(ContinuationLeaseLostError):
            await saver.aput(config(h), empty_checkpoint(), {}, {})
        with pytest.raises(ContinuationLeaseLostError):
            await saver.aput_writes(checkpoint or config(h), [("result", "late")], "late-tool")
        with pytest.raises(ContinuationLeaseLostError):
            await h.tasks.finish(h.owner, status="completed")
        stored = await saver.aget_tuple(config(h))
        assert stored is None if checkpoint is None else stored.checkpoint["id"] == checkpoint["configurable"]["checkpoint_id"]
    replacement = await h.tasks.claim(saved.id, worker_id="must-not-reclaim", expected_binding=h.binding)
    assert replacement.status == "interrupted" and replacement.attempt_no == 1
    events_before = await h.store.list_events(h.session_id, limit=None)
    again = await interrupt(h)
    assert again.status_code == 200 and len(await h.store.list_events(h.session_id, limit=None)) == len(events_before)


@pytest.mark.asyncio
async def test_waiting_turn_interrupt_keeps_decisions_but_cancels_continuations_only(runtime_harness):
    h = runtime_harness
    proposal = h.proposals["B"]
    accepted = await h.dispatcher.submit_approval(h.session_id, proposal.approval.id,
        ApprovalDecisionRequest(decision="approved"))
    new_binding = make_binding(h.session_id, f"next-{uuid4().hex}")
    future = await h.tasks.submit_v2(make_command(h, new_binding),
        make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, new_binding), idempotency_key=uuid4().hex)
    response = await interrupt(h)
    assert response.status_code == 200 and response.json()["status"] == "interrupted", response.text
    assert response.json()["control_state"] == "interrupted" and response.json()["pending_approvals"] == []
    assert (await h.tasks.get(accepted.task.id)).status == "cancelled"
    assert (await h.tasks.get(future.id)).status == "queued"
    assert owner_row(h)["execution_state"] == "interrupted"
    assert receipt(h).state == "queued" and receipt(h).applied_at is None
    approvals = {item.id: item for item in await h.store.list_approvals(h.session_id)}
    assert approvals[proposal.approval.id].status.value == "approved"
    assert approvals[h.proposals["A"].approval.id].status.value == "pending"
    with pytest.raises(ValueError):
        await h.dispatcher.submit_approval(h.session_id, h.proposals["A"].approval.id, ApprovalDecisionRequest(decision="approved"))
    assert h.effects == []


@pytest.mark.asyncio
async def test_waiting_native_interrupt_without_pending_task_can_be_stopped(runtime_harness):
    h = runtime_harness
    response = await interrupt(h)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "interrupted" and not response.json()["is_resumable"]
    assert owner_row(h)["execution_state"] == "interrupted"
    assert len(await h.tasks.list_session(h.session_id)) == 1  # Completed initial task is preserved.
    assert receipt(h) is None and h.effects == []
    assert response.json()["pending_approvals"] == []
    assert {item["phase"] for item in response.json()["framework_approval_progress"]} == {"recovery_required"}


@pytest.mark.asyncio
async def test_interrupt_preserves_unknown_tool_effect_for_reconciliation(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    assert job.status.value == "running"
    response = await interrupt(h)
    assert response.status_code == 200
    assert (await h.tasks.get(task.id)).status == "interrupted"
    current = await h.jobs.get_job(job.id)
    assert current.status.value == "running" and current.output_payload == {}
    assert receipt(h).applied_at is None
    assert next(item for item in response.json()["framework_approval_progress"]
        if item["approval_id"] == task.reference_id)["phase"] == "recovery_required"


@pytest.mark.asyncio
async def test_interrupt_does_not_undo_an_already_applied_tool_result(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    await resume(h, task)
    applied = receipt(h)
    response = await interrupt(h)
    assert response.status_code == 200
    assert (await h.tasks.get(task.id)).status == "interrupted"
    assert receipt(h) == applied and (await h.jobs.get_job(h.proposals["B"].job.id)).status.value == "completed"
    by_id = {item["approval_id"]: item for item in response.json()["framework_approval_progress"]}
    assert by_id[task.reference_id]["phase"] == "applied"
    assert by_id[h.proposals["A"].approval.id]["phase"] == "recovery_required"
    assert len(h.effects) == 1 and response.json()["pending_approvals"] == []


@pytest.mark.asyncio
async def test_interrupting_followup_revokes_previous_framework_owner_atomically(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    await h.tasks.begin(h.owner, resource_id="control-test", fencing_token=1)
    next_binding = make_binding(h.session_id, f"next-{uuid4().hex}")
    command, message = make_command(h, next_binding), make_message(h, next_binding)
    payload = make_payload().model_copy(update={"submit_mode": "queued", "interrupt_if_busy": True})
    original = h.tasks._event

    def fail(cur, row, event_type, data):
        if event_type == "input.queued":
            raise ConnectionError("injected followup admission rollback")
        return original(cur, row, event_type, data)

    monkeypatch.setattr(h.tasks, "_event", fail)
    with pytest.raises(ConnectionError):
        await h.tasks.submit_v2(command, payload, message, idempotency_key="followup")
    assert (await h.tasks.get(h.owner.id)).status == "running" and owner_row(h)["execution_state"] == "running"
    monkeypatch.setattr(h.tasks, "_event", original)
    accepted = await h.tasks.submit_v2(command, payload, message, idempotency_key="followup")
    assert (await h.tasks.get(h.owner.id)).status == "interrupted"
    assert owner_row(h)["execution_state"] == "interrupted"
    assert accepted.status == "queued" and accepted.runtime_binding == next_binding
    assert not await h.tasks.renew_framework_execution(h.owner, 60)


@pytest.mark.asyncio
async def test_interrupt_if_busy_does_not_cancel_its_own_new_idle_turn(harness):
    h = harness
    binding = make_binding(h.session_id, f"new-{uuid4().hex}")
    accepted = await h.tasks.submit_v2(make_command(h, binding),
        make_payload().model_copy(update={"interrupt_if_busy": True}), make_message(h, binding), idempotency_key=uuid4().hex)
    assert (await h.tasks.get(accepted.id)).status == "queued"
    claimed = await h.tasks.claim(accepted.id, worker_id="new-turn", expected_binding=binding)
    assert claimed.status == "claimed" and claimed.runtime_owner.owner_epoch == 1


@pytest.mark.asyncio
async def test_queued_framework_turn_is_cancelled_without_allocating_an_attempt(harness):
    h = harness
    binding = make_binding(h.session_id, f"new-{uuid4().hex}")
    task = await h.tasks.submit_v2(make_command(h, binding), make_payload(), make_message(h, binding), idempotency_key=uuid4().hex)
    response = await interrupt(h)
    assert response.status_code == 200, response.text
    saved = await h.tasks.claim(task.id, worker_id="no-execution", expected_binding=binding)
    assert saved.status == "cancelled" and saved.attempt_no == 0 and saved.attempt_id is None


@pytest.mark.asyncio
async def test_interrupt_targets_active_continuation_ahead_of_earlier_future_input(runtime_harness):
    h = runtime_harness
    future_binding = make_binding(h.session_id, f"future-{uuid4().hex}")
    future = await h.tasks.submit_v2(make_command(h, future_binding),
        make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, future_binding), idempotency_key=uuid4().hex)
    active = await claim_decision(h)
    response = await interrupt(h)
    assert response.status_code == 200
    assert (await h.tasks.get(active.id)).status == "interrupted"
    assert (await h.tasks.get(future.id)).status == "queued"


@pytest.mark.asyncio
async def test_http_revocation_stops_a_separate_native_framework_worker_before_dispatch(runtime_harness):
    from src.infrastructure.redis_task_queue import RedisTaskQueue

    h = runtime_harness
    h.settings.orchestration.agent_task_v2_stream = f"{h.prefix}:control:v2"
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="control-v2", client=h.redis)
    await queue.connect()
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["B"].approval.id,
        ApprovalDecisionRequest(decision="approved"))
    assert await h.outbox.relay_once(queue) == 1
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=framework_worker_process,
        args=(h.settings, h.prefix, h.binding, "before_prepare"))
    try:
        process.start()
        phase = await wait_gate(h.redis, f"{h.prefix}:control:entered", 20)
        assert phase and phase[1] == "before_prepare", f"worker exit code: {process.exitcode}"
        response = await interrupt(h)
        assert response.status_code == 200, response.text
        assert (await h.tasks.get(accepted.task.id)).status == "interrupted"
        await h.redis.rpush(f"{h.prefix}:control:release", "continue")
        await asyncio.to_thread(process.join, 15)
        assert process.exitcode == 0
        assert await h.redis.get(f"{h.prefix}:framework-effects") is None
        job = await h.jobs.get_job(h.proposals["B"].job.id)
        assert job.status.value == "waiting_approval" and job.started_at is None
        assert receipt(h).state == "queued" and receipt(h).applied_at is None
        assert (await h.tasks.claim(accepted.task.id, worker_id="redelivery", expected_binding=h.binding)).status == "interrupted"
    finally:
        await h.redis.rpush(f"{h.prefix}:control:release", "continue")
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 5)


@pytest.mark.asyncio
async def test_hard_killed_native_worker_leaves_unknown_effect_and_terminal_task_for_reconciliation(runtime_harness):
    from src.infrastructure.redis_task_queue import RedisTaskQueue

    h = runtime_harness
    h.settings.orchestration.agent_task_v2_stream = f"{h.prefix}:control:v2"
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="control-v2", client=h.redis)
    await queue.connect()
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["B"].approval.id,
        ApprovalDecisionRequest(decision="approved"))
    assert await h.outbox.relay_once(queue) == 1
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=framework_worker_process,
        args=(h.settings, h.prefix, h.binding, "after_effect"))
    try:
        process.start()
        phase = await wait_gate(h.redis, f"{h.prefix}:control:entered", 20)
        assert phase and phase[1] == "after_effect", f"worker exit code: {process.exitcode}"
        assert await h.redis.get(f"{h.prefix}:framework-effects") == "1"
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert process.exitcode is not None and process.exitcode != 0
        prior = await h.tasks.get(accepted.task.id)
        assert prior.status == "running"
        assert await h.store.renew_turn_execution(h.session_id, prior.lease_token, 0)
        settled = await h.tasks.claim(prior.id, worker_id="recovery-observer", expected_binding=h.binding)
        assert settled.status == "recovery_required" and settled.attempt_no == 1
        again = await h.tasks.claim(prior.id, worker_id="duplicate-observer", expected_binding=h.binding)
        assert again.status == "recovery_required" and again.attempt_no == 1
        assert await h.redis.get(f"{h.prefix}:framework-effects") == "1"
        assert (await h.jobs.get_job(h.proposals["B"].job.id)).status.value == "running"
        assert receipt(h).applied_at is None and receipt(h, "A") is None
    finally:
        await h.redis.rpush(f"{h.prefix}:control:release", "continue")
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 5)

"""A projected Turn may settle, but may never re-enter its execution pipeline."""
from __future__ import annotations

import asyncio
import multiprocessing

import pytest
from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.framework_message_stages import FrameworkMessageStages, FrameworkMessageStageRecoveryRequired, validate_message_stage
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores, outbox
from tests.test_framework_application_projection import projection, projection_jobs, drift, worker_waiting_after_application_commit
from tests.test_framework_partial_projection_recovery import dispatch, expire, forbid_reexecution, reap_dead_worker_resources
from tests.test_framework_message_stages import run, selected, stage


def attempts(h, task_id):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {h.settings.database.postgres_agent_attempt_table} WHERE task_id=%s ORDER BY attempt_no",
            (task_id,))
        return cur.fetchall()


def forbid_pipeline(h, monkeypatch):
    forbid_reexecution(h, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("Projected settlement must not enter Session execution or finalization")

    monkeypatch.setattr(h.sessions, "execute_claimed_message", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["original answer", ""])
async def test_projected_owner_loss_publishes_only_settlement_and_preserves_original(projection_jobs, monkeypatch, output):
    h = projection_jobs
    h.output_text = output
    original = await selected(h)
    await run(h, original)
    projected = await stage(h, original)
    assert projected.phase == "projected"
    terminal = await expire(h, original)
    original_attempts = attempts(h, original.id)
    budget = await h.calls.snapshot(h.binding)
    snapshot = await h.store.get_latest_snapshot(h.session_id)
    successors = await asyncio.gather(*(h.tasks.ensure_framework_message_recovery(original.id) for _ in range(3)))
    assert all(item is not None for item in successors)
    assert len({item.id for item in successors}) == 1
    successor = successors[0]
    saved = await stage(h, successor)
    assert saved.phase == "projected" and saved.schema_version == 22
    assert saved.result_reference == projected.result_reference
    assert saved.source_manifest == projected.source_manifest
    assert saved.recoveries[-1].resume_phase == "settling"
    assert outbox(h, successor.id) is not None
    forbid_pipeline(h, monkeypatch)
    await dispatch(h, successor)
    await dispatch(h, successor)
    settled = await h.tasks.get(successor.id)
    assert settled.status == settled.result_status == "completed"
    assert await h.tasks.get(original.id) == terminal
    assert attempts(h, original.id) == original_attempts
    assert len(attempts(h, successor.id)) == 1
    assert await stage(h, successor) == saved
    assert await h.store.get_latest_snapshot(h.session_id) == snapshot
    assert await h.calls.snapshot(h.binding) == budget
    assert await h.jobs.get_job(h.job.id) == h.job
    assert h.native_runs == len(h.runtime.calls) == 1
    session = await h.store.get_session(h.session_id)
    assert session.status.value == session.metadata["control"]["control_state"] == "completed"
    assert not session.metadata["control"]["is_interrupted"]
    events = await h.store.list_events(h.session_id)
    assert len([item for item in events if item.type == "turn.completed"]) == 1
    assert len([item for item in events if item.type == "agent_task.completed" and item.payload["task_id"] == successor.id]) == 1


@pytest.mark.asyncio
async def test_legal_original_owner_finishes_once_and_redelivery_only_acknowledges(projection, monkeypatch):
    h = projection
    original = await selected(h)
    await run(h, original)
    saved = await stage(h, original)
    forbid_pipeline(h, monkeypatch)
    results = await asyncio.gather(*(h.tasks.finish(original, status="completed", result_status="completed")
        for _ in range(3)), return_exceptions=True)
    assert sum(item is None for item in results) == 1
    assert all(item is None or isinstance(item, ContinuationLeaseLostError) for item in results)
    await dispatch(h, original)
    assert (await h.tasks.get(original.id)).status == "completed"
    assert await stage(h, original) == saved
    assert await h.tasks.ensure_framework_message_recovery(original.id) is None
    assert len([item for item in await h.store.list_events(h.session_id) if item.type == "agent_task.completed"]) == 1


def change_projection(h, saved, kind):
    if kind in {"snapshot", "snapshot_header", "message", "event", "tool", "tool_attempt", "model", "verification", "receipt"}:
        return drift(h, saved, kind)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if kind == "input":
            cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=jsonb_set(input_payload,'{{content}}','\"changed\"') WHERE id=%s",
                (saved.task_id,))
        elif kind in {"reference", "missing_commit"}:
            values = {"result_reference": "foreign-output"}
            if kind == "missing_commit":
                manifest = dict(saved.source_manifest)
                manifest.pop("application_projection_commit")
                values = {"source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}
            changed = saved.model_copy(update=values)
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s",
                (changed.model_dump_json(), saved.task_id))
        else:
            raise AssertionError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["admission", "claim", "finish"])
@pytest.mark.parametrize("kind", ["snapshot", "snapshot_header", "message", "event", "tool", "tool_attempt", "model",
    "verification", "receipt", "input", "reference", "missing_commit"])
async def test_projected_facts_are_rechecked_at_each_boundary(projection_jobs, monkeypatch, boundary, kind):
    h = projection_jobs
    h.model_phase = "completed"
    original = await selected(h)
    await run(h, original)
    terminal = await expire(h, original)
    saved = await stage(h, original)
    successor = claimed = None
    if boundary != "admission":
        successor = await h.tasks.ensure_framework_message_recovery(original.id)
        assert successor is not None
        saved = await stage(h, successor)
        if boundary == "finish":
            claimed = await h.tasks.claim(successor.id, worker_id="settlement-owner", expected_binding=h.binding)
            await h.tasks.begin(claimed, resource_id="settlement-fixture", fencing_token=2)
    forbid_pipeline(h, monkeypatch)
    change_projection(h, saved, kind)
    if boundary == "admission":
        assert await h.tasks.ensure_framework_message_recovery(original.id) is None
    elif boundary == "claim":
        rejected = await h.tasks.claim(successor.id, worker_id="changed-claim", expected_binding=h.binding)
        assert rejected.status == "recovery_required" and rejected.error_code == "framework_message_recovery_changed"
        assert rejected.attempt_id is None
        assert await h.tasks.ensure_framework_message_recovery(successor.id) is None
    else:
        with pytest.raises((FrameworkMessageStageRecoveryRequired, ValidationError)):
            await h.tasks.finish(claimed, status="completed", result_status="completed")
        assert (await h.tasks.get(successor.id)).status == "running"
    # The input-drift case deliberately changes the original task's input,
    # but recovery must never change its terminal status/attempt.
    after = await h.tasks.get(original.id)
    assert after.status == terminal.status and after.attempt_id == terminal.attempt_id
    if kind != "input":
        assert after == terminal
    assert h.native_runs == len(h.runtime.calls) == 1
    assert not any(item.type == "agent_task.completed" for item in await h.store.list_events(h.session_id))


@pytest.mark.asyncio
async def test_settlement_proof_has_no_finalizer_or_stage_reentry_authority(projection, monkeypatch):
    h = projection
    original = await selected(h)
    await run(h, original)
    await expire(h, original)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    claimed = await h.tasks.claim(successor.id, worker_id="settlement-no-writes", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="settlement-fixture", fencing_token=2)
    stages = FrameworkMessageStages(h.tasks)
    saved = await stage(h, successor)
    assert validate_message_stage(saved.model_dump()) == saved
    for version in (21, 24):
        with pytest.raises((FrameworkMessageStageRecoveryRequired, ValidationError)):
            validate_message_stage(saved.model_copy(update={"schema_version": version}).model_dump())
    for operation in (lambda: stages.resume_finalizing(claimed.runtime_owner),
        lambda: stages.submit_runtime(claimed.runtime_owner),
        lambda: stages.projected(claimed.runtime_owner, saved.result_reference),
        lambda: FrameworkRuntimeResults(h.tasks).load(claimed.runtime_owner),
        lambda: run(h, claimed)):
        with pytest.raises(FrameworkMessageStageRecoveryRequired):
            await operation()
    assert await stage(h, successor) == saved
    await h.tasks.finish(claimed, status="completed", result_status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [True, False])
async def test_settlement_transaction_rolls_back_task_attempt_owner_and_session(projection, monkeypatch, origin):
    h = projection
    original = await selected(h)
    await run(h, original)
    current = original
    if not origin:
        await expire(h, original)
        successor = await h.tasks.ensure_framework_message_recovery(original.id)
        current = await h.tasks.claim(successor.id, worker_id="settlement-rollback", expected_binding=h.binding)
        await h.tasks.begin(current, resource_id="settlement-fixture", fencing_token=2)
    before = await h.tasks.get(current.id)
    session = await h.store.get_session(h.session_id)
    saved_attempts = attempts(h, current.id)
    saved_stage = await stage(h, current)
    event = h.tasks._event

    def fail(cur, row, name, payload):
        event(cur, row, name, payload)
        if name == "agent_task.completed":
            raise ConnectionError("injected settlement rollback after task event")

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="settlement rollback"):
            await h.tasks.finish(current, status="completed", result_status="completed")
    assert await h.tasks.get(current.id) == before
    assert await h.store.get_session(h.session_id) == session
    assert attempts(h, current.id) == saved_attempts
    assert await stage(h, current) == saved_stage
    assert not any(item.type == "agent_task.completed" for item in await h.store.list_events(h.session_id))
    await h.tasks.finish(current, status="completed", result_status="completed")
    assert (await h.tasks.get(current.id)).status == "completed"


@pytest.mark.asyncio
async def test_settlement_success_cannot_omit_its_completed_result(projection):
    h = projection
    original = await selected(h)
    await run(h, original)
    await expire(h, original)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    claimed = await h.tasks.claim(successor.id, worker_id="settlement-contract", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="settlement-fixture", fencing_token=2)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="settlement.*completed"):
        await h.tasks.finish(claimed, status="completed")
    assert (await h.tasks.get(successor.id)).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("reader,version", [(21, 22), (22, 23)])
async def test_projected_settlement_format_is_rejected_before_real_claim(projection, reader, version):
    from src.infrastructure.redis_task_queue import QueuedTask

    h = projection
    original = await selected(h)
    await run(h, original)
    await expire(h, original)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    if version != 22:
        saved = await stage(h, successor)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s",
                (saved.model_copy(update={"schema_version": version}).model_dump_json(), saved.task_id))
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=reader))
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("incompatible-settlement", outbox(h, successor.id)["payload"]),
            worker_id="incompatible-worker")
    assert await h.tasks.get(successor.id) == successor
    assert attempts(h, successor.id) == []


@pytest.mark.asyncio
async def test_projected_settlement_preserves_queued_input_and_unrelated_metadata(projection, monkeypatch):
    from datetime import datetime, timezone
    from src.schemas.session import ChatMessage, MessageRole

    h = projection
    original = await selected(h)
    await run(h, original)
    latest = await h.store.get_session(h.session_id)
    later = ChatMessage(id="later-input", role=MessageRole.user, content="queued later input",
        created_at=datetime.now(timezone.utc), metadata={"turn_id": "later-turn", "delivery_status": "queued"})
    latest.messages.append(later)
    latest.metadata["unrelated-preference"] = "keep"
    await h.store.save_session(latest, turn_lease_token=original.lease_token)
    persisted = await h.store.get_session(h.session_id)
    later = next(item for item in persisted.messages if item.id == later.id)
    await expire(h, original)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    assert successor is not None
    forbid_pipeline(h, monkeypatch)
    await dispatch(h, successor)
    saved = await h.store.get_session(h.session_id)
    assert saved.metadata["unrelated-preference"] == "keep"
    assert next(item for item in saved.messages if item.id == later.id) == later


@pytest.mark.asyncio
async def test_projected_successor_publication_is_one_atomic_transaction(projection, monkeypatch):
    h = projection
    original = await selected(h)
    await run(h, original)
    terminal = await expire(h, original)
    saved = await stage(h, original)
    event = h.tasks._event

    def fail(cur, row, name, payload):
        event(cur, row, name, payload)
        if name == "framework.message_recover_queued":
            raise ConnectionError("injected settlement publication rollback")

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="publication rollback"):
            await h.tasks.ensure_framework_message_recovery(original.id)
    assert await h.tasks.get(original.id) == terminal
    assert await stage(h, original) == saved
    assert not any(item.operation == "message_recover" for item in await h.tasks.list_session(h.session_id))
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS count FROM {h.settings.database.postgres_task_outbox_table}")
        assert cur.fetchone()["count"] == 1
    assert await h.tasks.ensure_framework_message_recovery(original.id) is not None


@pytest.mark.asyncio
async def test_dispatcher_reconciles_post_claim_drift_without_auto_retry(projection, monkeypatch):
    h = projection
    original = await selected(h)
    await run(h, original)
    terminal = await expire(h, original)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    saved = await stage(h, successor)
    begin = h.tasks.begin

    async def begin_and_drift(*args, **kwargs):
        await begin(*args, **kwargs)
        drift(h, saved, "snapshot")

    forbid_pipeline(h, monkeypatch)
    monkeypatch.setattr(h.tasks, "begin", begin_and_drift)
    await dispatch(h, successor)
    rejected = await h.tasks.get(successor.id)
    assert rejected.status == "recovery_required" and rejected.error_code == "FrameworkMessageStageRecoveryRequired"
    await dispatch(h, successor)
    assert await h.tasks.get(successor.id) == rejected
    assert await h.tasks.get(original.id) == terminal
    assert len([item for item in await h.tasks.list_session(h.session_id) if item.operation == "message_recover"]) == 1


def worker_settlement(settings_data, prefix, gate, window):
    """Independent settlement Worker; even the Session entry is forbidden."""
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from tests.test_agent_task_dispatch import build_services, Runtime

    async def work():
        settings = Settings.model_validate(settings_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
        await resources.connect()
        runtime = Runtime()
        _, tasks, sessions, dispatcher = build_services(settings, runtime, resources)

        def forbidden(*args, **kwargs):
            raise AssertionError("Settlement Worker has no Session/Runtime/finalizer permission")

        runtime.execute_turn = runtime.resume_turn = runtime.resume_after_approval = forbidden
        sessions.execute_claimed_message = sessions._finalize_runtime_result = forbidden
        sessions._input_orchestrator_service.orchestrate_async = forbidden
        sessions._verification_service.build_results = forbidden
        dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))

        async def pause():
            await redis.rpush(gate, window)
            await asyncio.Event().wait()

        claim, finish = tasks.claim, tasks.finish

        async def claim_and_pause(*args, **kwargs):
            task = await claim(*args, **kwargs)
            if task.operation == "message_recover" and task.status == "claimed" and window == "after_claim":
                await pause()
            return task

        async def finish_and_pause(task, **kwargs):
            success = task.operation == "message_recover" and kwargs.get("status") == "completed"
            if success and window == "before_finish":
                await pause()
            result = await finish(task, **kwargs)
            if success and window == "after_finish":
                await pause()
            return result

        tasks.claim, tasks.finish = claim_and_pause, finish_and_pause
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
            group="projection-kill", client=redis)
        await queue.connect()

        async def handle(item):
            await dispatcher.handle_v2(item, worker_id="settlement-independent")

        worker = RedisTaskWorker(queue, consumer="settlement-independent", handler=handle, block_ms=0, reclaim_idle_ms=1)
        try:
            for _ in range(4):
                await worker.run_once()
            assert (await redis.xpending(queue.stream, queue.group))["pending"] == 0
            assert not runtime.calls
        finally:
            await redis.aclose()

    asyncio.run(work())


async def stop_process(process):
    assert process.is_alive()
    process.terminate()
    await asyncio.to_thread(process.join, 10)
    assert not process.is_alive() and process.exitcode != 0


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [None, "after_claim", "before_finish", "after_finish"])
async def test_official_projected_worker_kill_and_settlement_worker_kill_preserve_result(admission, monkeypatch, window):
    from redis.asyncio import Redis
    from src.application.models.framework_model_call_policy import model_config_fingerprint
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_framework_model_admission import submit
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    h.settings.orchestration.resource_default_lease_seconds = 30
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=60)
    process = None
    try:
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url)
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review"})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            original = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="projection-kill", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":projected-kill"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate, "after_projected"))
            process.start()
            reached = await waiting.blpop(gate, timeout=50)
            assert reached is not None and reached[1] == "stage-projected", f"origin worker exit={process.exitcode}"
            projected = await stage(h, original)
            assert projected.phase == "projected" and projected.schema_version == 21
            snapshot = await h.store.get_latest_snapshot(h.session_id)
            budget = await h.calls.snapshot(h.binding)
            assert len(requests) == 1
            await stop_process(process)
            active = await h.tasks.get(original.id)
            terminal = await expire(h, active)
            original_attempts = attempts(h, original.id)
            successor = await h.tasks.ensure_framework_message_recovery(original.id)
            assert successor is not None
            saved = await stage(h, successor)
            assert saved.phase == "projected" and saved.recoveries[-1].resume_phase == "settling"
            assert saved.source_manifest == projected.source_manifest
            await reap_dead_worker_resources(h)
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            process = multiprocessing.get_context("spawn").Process(target=worker_settlement,
                args=(h.settings.model_dump(mode="json"), h.prefix, gate, window))
            process.start()
            predecessor = None
            if window is not None:
                reached = await waiting.blpop(gate, timeout=50)
                assert reached is not None and reached[1] == window, f"settlement worker exit={process.exitcode}"
                first = await h.tasks.get(successor.id)
                assert first.status == {"after_claim": "claimed", "before_finish": "running", "after_finish": "completed"}[window]
                assert (await h.redis.xpending(queue.stream, queue.group))["pending"] >= 1
                await stop_process(process)
                if window == "before_finish":
                    predecessor = await expire(h, first)
                    successor = await h.tasks.ensure_framework_message_recovery(first.id)
                    assert successor is not None
                    assert (await stage(h, successor)).recoveries[-1].predecessor_task_id == first.id
                    assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
                elif window == "after_claim":
                    assert await h.store.renew_turn_execution(h.session_id, first.lease_token, 0)
                    # Claimed but not started is reclaimable by the existing
                    # inbox protocol; no terminal attempt is reset.
                await reap_dead_worker_resources(h)
                process = multiprocessing.get_context("spawn").Process(target=worker_settlement,
                    args=(h.settings.model_dump(mode="json"), h.prefix, gate, None))
                process.start()
            await asyncio.to_thread(process.join, 60)
            assert process.exitcode == 0, f"settlement worker deadline/exit={process.exitcode}"
            forbid_pipeline(h, monkeypatch)
            await dispatch(h, successor)
            assert (await h.tasks.get(successor.id)).status == "completed"
            if predecessor is not None:
                assert await h.tasks.get(predecessor.id) == predecessor
            if window == "after_claim":
                records = attempts(h, successor.id)
                assert [row["status"] for row in records] == ["interrupted", "completed"]
            assert await h.tasks.get(original.id) == terminal
            assert attempts(h, original.id) == original_attempts
            final_stage = await stage(h, successor)
            assert final_stage.phase == "projected" and final_stage.result_reference == projected.result_reference
            assert final_stage.source_manifest == projected.source_manifest
            assert await h.store.get_latest_snapshot(h.session_id) == snapshot
            assert await h.calls.snapshot(h.binding) == budget and len(requests) == 1
            session = await h.store.get_session(h.session_id)
            assert session.status.value == session.metadata["control"]["control_state"] == "completed"
            assert len(session.messages) == 2
            assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 0
            events = await h.store.list_events(h.session_id)
            assert len([item for item in events if item.type == "turn.completed"]) == 1
            assert len([item for item in events if item.type == "agent_task.completed"]) == 1
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")

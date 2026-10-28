"""Only original missing application projections may follow an expired owner."""
from __future__ import annotations

import asyncio
import json
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_message_stages import FrameworkMessageStages, FrameworkMessageStageRecoveryRequired
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores, outbox
from tests.test_framework_application_projection import projection, projection_jobs
from tests.test_framework_runtime_result_asset import frozen_result
from tests.test_framework_finalization_projection import finalization_result
from tests.test_framework_snapshot_projection import stop_after_snapshot
from tests.test_framework_message_stages import stage, selected, run
from tests.test_framework_observation_projection import (
    observation_result, original_observations, prepare_without_memory_writes, memory_rows,
)
from tests.test_framework_snapshot_projection import snapshot_write


async def expire(h, task):
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    assert (await h.tasks.claim(task.id, worker_id="expiry", expected_binding=h.binding)).status == "recovery_required"
    return await h.tasks.get(task.id)


def forbid_reexecution(h, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Original finalization must not rerun graph/model/orchestration/verification")
    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)


async def dispatch(h, successor):
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("partial-recovery", outbox(h, successor.id)["payload"]),
        worker_id="partial-replacement")


def worker_complete_projection(settings_data, prefix, command, with_memory=False, pause_at=None, gate=None, read_original=False):
    """Replacement process has no permission to run a graph/model/verification."""
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
        store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
        provider = None
        if read_original:
            from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
            from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter

            provider = DeepAgentCheckpointProvider(settings)
            adapter = DeepAgentRuntimeAdapter(model_resolver=None, checkpointer_factory=provider.open)
            dispatcher._framework_native_reader = adapter.open_native_reader

        async def pause(*args, **kwargs):
            await redis.rpush(gate, pause_at)
            await asyncio.Event().wait()

        if pause_at == "admission_before_commit":
            event = tasks._event

            def before_commit(cur, row, name, payload):
                if name == "framework.message_recover_queued":
                    import threading
                    from redis import Redis as SyncRedis

                    with SyncRedis.from_url(settings.database.redis_url) as signal:
                        signal.rpush(gate, pause_at)
                    threading.Event().wait()
                return event(cur, row, name, payload)

            tasks._event = before_commit
        elif pause_at == "before_application_commit":
            store.save_session_with_events = pause
        elif pause_at == "after_application_commit":
            FrameworkMessageStages.projected = pause
        def forbidden(*args, **kwargs):
            raise AssertionError("Recovery must not call graph/model/orchestration/verification")
        runtime.execute_turn = runtime.resume_turn = runtime.resume_after_approval = forbidden
        sessions._input_orchestrator_service.orchestrate_async = forbidden
        sessions._verification_service.build_results = forbidden
        if with_memory:
            from src.application.context.memory_runtime_service import MemoryRuntimeService
            from src.application.context.observation_runtime_service import ObservationRuntimeService
            from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore

            sessions._memory_runtime_service = MemoryRuntimeService(PostgresVectorMemoryStore(settings))
            sessions._observation_runtime_service = ObservationRuntimeService()
        dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
        for delivery in ("replacement", "duplicate"):
            await dispatcher.handle_v2(QueuedTask(delivery, command), worker_id="independent-projection")
            if pause_at == "admission_after_commit":
                await pause()
        assert (await tasks.get(command["task_id"])).status == ("recovery_required" if read_original else "completed")
        assert runtime.calls == []
        if provider is not None:
            await provider.close()
        await redis.aclose()
    asyncio.run(work())


async def reap_dead_worker_resources(h):
    from datetime import datetime, timezone
    from src.runtime.resource_lease_manager import ResourceLease

    # Callers have stopped original work (a confirmed killed process or a
    # returned injected failure). Reap only its real fixture resource leases.
    leases = [item for item in await h.resources.list_active() if item["session_id"] == h.session_id]
    for data in leases:
        lease = ResourceLease(**{key: value for key, value in data.items()
            if key in ResourceLease.__dataclass_fields__}, lease_expire_at=datetime.now(timezone.utc))
        assert await h.resources.renew(lease, lease_seconds=1)
    if leases:
        await asyncio.sleep(1.1)
        await h.resources.reap_expired()


async def complete_in_new_process(h, successor, *, with_memory=False):
    await reap_dead_worker_resources(h)
    process = multiprocessing.get_context("spawn").Process(target=worker_complete_projection,
        args=(h.settings.model_dump(mode="json"), h.prefix, outbox(h, successor.id)["payload"], with_memory))
    process.start()
    try:
        await asyncio.to_thread(process.join, 45)
        exitcode = process.exitcode
        if exitcode is None:
            # Preserve the original deadline result before diagnostic queries;
            # a late exit must not turn a timed-out Worker into a passing test.
            phases = [(item.operation, item.status, item.result_status, item.error_code)
                for item in await h.tasks.list_session(h.session_id)]
            pytest.fail(f"Replacement Worker exceeded its 45-second deadline: pid={process.pid}, "
                f"alive={process.is_alive()}, task_phases={phases}")
        assert exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)


@pytest.mark.asyncio
async def test_partial_projection_successor_finishes_original_result_without_rerunning(finalization_result, monkeypatch):
    h = finalization_result
    original_snapshot = await stop_after_snapshot(h, monkeypatch)
    original_events = await h.store.list_events(h.session_id)
    budget = await h.calls.snapshot(h.binding)
    terminal = await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None, "A durable original result must admit missing application projection recovery"
    link = (await stage(h, h.task)).recoveries[-1]
    assert link.resume_phase == "finalizing" and link.result_sha256 == h.asset.asset_sha256
    forbid_reexecution(h, monkeypatch)
    await dispatch(h, successor)
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert await h.tasks.get(h.task.id) == terminal
    assert await h.calls.snapshot(h.binding) == budget
    assert await h.store.get_latest_snapshot(h.session_id) == original_snapshot
    saved = await h.store.get_session(h.session_id)
    assert saved.metadata["verification_results"] == [item.model_dump(mode="json") for item in h.asset.verification_results]
    assert len(saved.messages) == 3 and len({item.id for item in saved.messages}) == 3
    assert (await stage(h, h.task)).phase == "projected"
    events = await h.store.list_events(h.session_id)
    for event in original_events:
        assert next(item for item in events if item.id == event.id) == event
    assert len([item for item in events if item.type == "turn.completed"]) == 1
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finalization_result", [{}, {"empty": True}, {"hidden": True}], indirect=True)
async def test_result_asset_before_any_projection_is_sufficient_only_for_finalization(finalization_result, monkeypatch):
    h = finalization_result
    assert await h.store.get_latest_snapshot(h.session_id) is None
    terminal = await expire(h, h.task)
    results = await asyncio.gather(*(h.tasks.ensure_framework_message_recovery(h.task.id) for _ in range(3)))
    assert all(item is not None for item in results) and len({item.id for item in results}) == 1
    forbid_reexecution(h, monkeypatch)
    await dispatch(h, results[0])
    await dispatch(h, results[0])
    assert (await h.tasks.get(results[0].id)).status == "completed"
    assert await h.tasks.get(h.task.id) == terminal
    saved = await h.store.get_session(h.session_id)
    expected_count = 1 + bool(h.output_text) + bool(h.asset.runtime_result.tool_messages[0].metadata.get("persist_transcript", True))
    assert len(saved.messages) == expected_count
    committed = await stage(h, h.task)
    assert committed.schema_version == 26
    assert committed.source_manifest["post_commit_memory_intent"]["runtime_result_asset_sha256"] == h.asset.asset_sha256
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1


def corrupt_part(h, changed):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "snapshot":
            cur.execute(f"UPDATE {h.settings.database.postgres_snapshot_table} SET version=version+1 WHERE id=%s",
                (h.asset.runtime_result.snapshot.id,))
        elif changed == "event":
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET timestamp=timestamp+interval '1 second' WHERE id=%s",
                (h.asset.event_id("verification.completed"),))
        elif changed == "message":
            cur.execute(f"INSERT INTO {h.settings.database.postgres_message_table}(id,session_id,role,content,created_at,metadata) "
                "VALUES (%s,%s,'assistant','foreign',%s,%s::jsonb)",
                (h.asset.assistant_message_id, h.session_id, h.asset.projection_at, json.dumps({"turn_id": h.binding.turn_id})))
        elif changed == "verification":
            value = h.asset.verification_results[0].model_dump(mode="json")
            value["status"] = "passed"
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata="
                "jsonb_set(metadata,'{verification_results}',%s::jsonb) WHERE id=%s", (json.dumps([value]), h.session_id))
        elif changed == "user":
            cur.execute(f"UPDATE {h.settings.database.postgres_message_table} SET content='different' WHERE id=%s",
                (h.task.user_message_id,))
        elif changed == "cancelled":
            cur.execute(f"UPDATE {h.tasks._tasks} SET interrupt_requested=TRUE WHERE id=%s", (h.task.id,))
        else:
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=jsonb_set(record, "
                "'{source_manifest,runtime_result_asset,asset_sha256}',%s::jsonb) WHERE task_id=%s",
                (json.dumps("0" * 64), h.task.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["snapshot", "event", "message", "verification", "user", "cancelled", "asset"])
async def test_drifted_parts_do_not_get_a_successor(finalization_result, monkeypatch, changed):
    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    await expire(h, h.task)
    corrupt_part(h, changed)
    before = await h.tasks.get(h.task.id)
    assert await h.tasks.ensure_framework_message_recovery(h.task.id) is None
    assert await h.tasks.get(h.task.id) == before
    assert len(await h.tasks.list_session(h.session_id)) == 1
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_parts_are_rechecked_before_claim_and_old_reader_cannot_claim(finalization_result, monkeypatch):
    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=10))
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("old-reader", outbox(h, successor.id)["payload"]), worker_id="old-reader")
    assert (await h.tasks.get(successor.id)).attempt_no == 0
    corrupt_part(h, "snapshot")
    rejected = await h.tasks.claim(successor.id, worker_id="drifted", expected_binding=h.binding)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_part_drift_after_claim_is_rechecked_before_adopting_finalization(finalization_result, monkeypatch):
    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    claimed = await h.tasks.claim(successor.id, worker_id="claimed-before-drift", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="claimed-before-drift", fencing_token=2)
    corrupt_part(h, "verification")
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="verification"):
        await FrameworkMessageStages(h.tasks).resume_finalizing(claimed.runtime_owner)
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["projection_drift", "unexpected_error"])
async def test_dispatcher_preserves_reconciliation_classification_without_retrying(finalization_result, monkeypatch, failure):
    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    original = await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    execute = h.sessions.execute_claimed_message
    async def fail_after_claim(task, **kwargs):
        if failure == "unexpected_error":
            raise ValueError("injected application programming error")
        corrupt_part(h, "verification")
        return await execute(task, **kwargs)
    monkeypatch.setattr(h.sessions, "execute_claimed_message", fail_after_claim)
    forbid_reexecution(h, monkeypatch)
    before = await h.calls.snapshot(h.binding)
    await dispatch(h, successor)
    saved = await h.tasks.get(successor.id)
    assert saved.status == ("recovery_required" if failure == "projection_drift" else "failed")
    assert saved.error_code == ("FrameworkMessageStageRecoveryRequired" if failure == "projection_drift" else "ValueError")
    await dispatch(h, successor)
    assert await h.tasks.get(successor.id) == saved
    assert await h.tasks.ensure_framework_message_recovery(successor.id) is None
    assert len(await h.tasks.list_session(h.session_id)) == 2
    assert await h.tasks.get(h.task.id) == original
    assert await h.calls.snapshot(h.binding) == before
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest
    assert not [e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_before_expiry", [False, True])
async def test_successor_second_owner_loss_preserves_lineage_and_ack_only_after_commit(finalization_result, monkeypatch, commit_before_expiry):
    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    original = await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    lost = await h.tasks.claim(successor.id, worker_id="lost", expected_binding=h.binding)
    await h.tasks.begin(lost, resource_id="lost", fencing_token=2)
    stages = FrameworkMessageStages(h.tasks)
    await stages.resume_finalizing(lost.runtime_owner)
    if commit_before_expiry:
        from src.runtime.framework_runtime_results import FrameworkRuntimeResults
        asset = await FrameworkRuntimeResults(h.tasks).load(lost.runtime_owner)
        with framework_owner_scope(lost.runtime_owner):
            await h.sessions._finalize_runtime_result(session=await h.store.get_session(h.session_id),
                runtime_result=asset.runtime_result.restore(), assistant_message_id=asset.assistant_message_id,
                user_message_override=asset.user_message, turn_lease_token=lost.lease_token,
                projection_owner=lost.runtime_owner, projection_asset=asset)
    lost_terminal = await expire(h, lost)
    replacement = await h.tasks.ensure_framework_message_recovery(lost.id)
    assert replacement is not None and replacement.parent_task_id == lost.id
    saved = await stage(h, h.task)
    assert len(saved.recoveries) == 2
    assert saved.recoveries[-1].resume_phase == ("runtime_submitted" if commit_before_expiry else "finalizing")
    with pytest.raises(ContinuationLeaseLostError):
        await stages.resume_finalizing(lost.runtime_owner)
    forbid_reexecution(h, monkeypatch)
    await dispatch(h, replacement)
    assert (await h.tasks.get(replacement.id)).status == "completed"
    assert await h.tasks.get(h.task.id) == original and await h.tasks.get(lost.id) == lost_terminal
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1


@pytest.mark.asyncio
async def test_partial_observation_batch_finishes_only_missing_rows(observation_result, monkeypatch):
    h = observation_result
    asset = await prepare_without_memory_writes(h, monkeypatch)
    await h.sessions._memory_runtime_service.write_observations(asset.observations[:1],
        framework_projection=snapshot_write(h), prepared_requests=asset.write_requests[:1])
    first, = memory_rows(h)
    await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    forbid_reexecution(h, monkeypatch)
    await dispatch(h, successor)
    assert (await h.tasks.get(successor.id)).status == "completed"
    rows = memory_rows(h)
    assert len(rows) == 2 and next(item for item in rows if item["id"] == first["id"]) == first
    assert (await original_observations(h)) == asset


@pytest.mark.asyncio
async def test_partial_successor_ack_does_not_require_memory_after_application_commit(observation_result, monkeypatch):
    h = observation_result
    await stop_after_snapshot(h, monkeypatch)
    await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    cleanup = h.sessions._cleanup_session_resources_if_terminal
    async def remove_derived_rows(*args, **kwargs):
        # Required application facts have committed. Derived memory is outside
        # the A1 scope, even when that commit was produced by a successor.
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {h.settings.database.postgres_memory_table} WHERE session_id=%s", (h.session_id,))
        return await cleanup(*args, **kwargs)
    monkeypatch.setattr(h.sessions, "_cleanup_session_resources_if_terminal", remove_derived_rows)
    forbid_reexecution(h, monkeypatch)
    await dispatch(h, successor)
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert memory_rows(h) == []
    assert (await stage(h, h.task)).phase == "projected"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["content", "missing", "bare"])
async def test_drifted_or_unproved_observation_rows_reject_recovery(observation_result, monkeypatch, changed):
    h = observation_result
    asset = await prepare_without_memory_writes(h, monkeypatch)
    if changed == "bare":
        await h.memories.write(asset.write_requests[0])
    else:
        await h.sessions._memory_runtime_service.write_observations(asset.observations[:1],
            framework_projection=snapshot_write(h), prepared_requests=asset.write_requests[:1])
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            if changed == "missing":
                cur.execute(f"DELETE FROM {h.settings.database.postgres_memory_table} WHERE id=%s", (asset.observations[0].id,))
            else:
                cur.execute(f"UPDATE {h.settings.database.postgres_memory_table} SET content='changed' WHERE id=%s", (asset.observations[0].id,))
    await expire(h, h.task)
    assert await h.tasks.ensure_framework_message_recovery(h.task.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "invoking", "unknown"])
async def test_unknown_model_facts_never_gain_finalization_recovery(frozen_result, phase):
    from tests.test_framework_model_calls import intent

    h = frozen_result
    call = intent(position_version=1)
    await h.calls.prepare_initial(h.task.runtime_owner, call)
    if phase != "ready":
        await h.calls.start(h.task.runtime_owner, call.attempt_id)
    if phase == "unknown":
        await h.calls.mark_unknown(h.task.runtime_owner, call.attempt_id, code="injected_unknown")
    await expire(h, h.task)
    before = await h.calls.snapshot(h.binding)
    assert await h.tasks.ensure_framework_message_recovery(h.task.id) is None
    assert await h.calls.snapshot(h.binding) == before
    assert len(h.runtime.calls) == h.native_runs == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "running", "cancelled"])
async def test_partial_finalization_uses_only_known_original_tooljob(projection_jobs, monkeypatch, status):
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults

    h = projection_jobs
    async def pause(**kwargs):
        h.result_values = kwargs
        raise ConnectionError("before any application projection")
    h.task = await selected(h)
    with monkeypatch.context() as patch:
        patch.setattr(h.sessions, "_finalize_runtime_result", pause)
        with pytest.raises(ConnectionError, match="before any application projection"):
            await run(h, h.task)
    h.asset = await FrameworkRuntimeResults(h.tasks).load(h.task.runtime_owner)
    if status != "completed":
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status=%s WHERE id=%s", (status, h.job.id))
    await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    if status != "completed":
        assert successor is None
    else:
        assert successor is not None
        forbid_reexecution(h, monkeypatch)
        await dispatch(h, successor)
        assert (await h.tasks.get(successor.id)).status == "completed"
        assert await h.jobs.get_job(h.job.id) == h.job


@pytest.mark.asyncio
async def test_concurrent_later_input_and_preferences_survive_partial_recovery(finalization_result, monkeypatch):
    from datetime import datetime
    from src.schemas.session import ChatMessage, MessageRole

    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    latest = await h.store.get_session(h.session_id)
    later = ChatMessage(id="later-input", role=MessageRole.user, content="queued later input", created_at=datetime.utcnow(),
        metadata={"turn_id": "later-turn", "delivery_status": "queued", "context_eligible": False})
    latest.messages.append(later)
    latest.metadata["unrelated-preference"] = "keep"
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    await expire(h, h.task)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    forbid_reexecution(h, monkeypatch)
    await dispatch(h, successor)
    assert (await h.tasks.get(successor.id)).status == "completed"
    saved = await h.store.get_session(h.session_id)
    assert saved.metadata["unrelated-preference"] == "keep"
    assert next(item for item in saved.messages if item.id == later.id) == later


@pytest.mark.asyncio
async def test_finalization_publication_rolls_back_link_task_and_outbox_together(finalization_result, monkeypatch):
    h = finalization_result
    await expire(h, h.task)
    before = await stage(h, h.task)
    emit = h.tasks._event
    def fail(cur, row, event_type, payload):
        if event_type == "framework.message_recover_queued":
            raise ConnectionError("injected finalization publication rollback")
        return emit(cur, row, event_type, payload)
    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="publication rollback"):
            await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert await stage(h, h.task) == before
    assert len(await h.tasks.list_session(h.session_id)) == 1
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    saved = await stage(h, h.task)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="frozen proof"):
        from src.runtime.framework_message_stages import validate_message_stage
        validate_message_stage(saved.model_copy(update={"schema_version": 10}).model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize("finalization_result", [{"user_whitespace": True}], indirect=True)
@pytest.mark.parametrize("recovery", [False, True])
async def test_original_input_normalization_matches_admission(finalization_result, monkeypatch, recovery):
    h = finalization_result
    assert h.asset.user_message != h.asset.user_message.strip()
    if recovery:
        await expire(h, h.task)
        successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
        assert successor is not None
        forbid_reexecution(h, monkeypatch)
        await dispatch(h, successor)
        assert (await h.tasks.get(successor.id)).status == "completed"
    else:
        with framework_owner_scope(h.task.runtime_owner):
            await h.sessions._finalize_runtime_result(**h.result_values)
    saved = await h.store.get_session(h.session_id)
    assert next(item for item in saved.messages if item.id == h.task.user_message_id).content == h.asset.user_message.strip()
    assert "application_projection_commit" in (await stage(h, h.task)).source_manifest

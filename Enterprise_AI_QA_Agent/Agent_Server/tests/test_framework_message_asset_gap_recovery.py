"""Completed ordinary messages seal only under fresh message-admission locks."""
from __future__ import annotations

from copy import deepcopy
import asyncio
import json
from datetime import datetime

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.postgres_runtime_owner import runtime_owner_table
from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
from tests.test_framework_message_result_reconstruction import (
    harness, checkpoint_harness, projection_harness, fresh_runtime_harness, message_asset_gap,
)
from tests.test_framework_approval_recovery import run_delivery


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["api-tester", "none", "tools", "failed", "large", "budget", "planning", "history", "recovered"], indirect=True)
async def test_terminal_delivery_seals_original_and_runs_only_the_existing_finalizer(message_asset_gap, monkeypatch):
    h = message_asset_gap
    before = h.stages.get(h.initial_task_id, h.session_id)
    effects, requests = deepcopy(h.effects), list(h.adapter_requests)
    budget = await h.calls.snapshot(h.binding)
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    # Same optional source-only dependency supplied by production composition.
    h.dispatcher = type(h.dispatcher)(h.settings, h.tasks, h.sessions, h.resources,
        framework_native_reader=adapter.open_native_reader)
    h.sessions.set_agent_task_dispatcher(h.dispatcher)
    finalizations = []
    finalize = h.sessions._finalize_runtime_result

    def forbidden(*args, **kwargs):
        pytest.fail("Completed message recovery attempted graph/model/tool/current-rule execution")

    async def original_finalizer(**kwargs):
        finalizations.append(kwargs)
        return await finalize(**kwargs)

    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", original_finalizer)
    await run_delivery(h, h.owner.id)
    sealed = h.stages.get(h.initial_task_id, h.session_id)
    assert "runtime_result_asset" in sealed.source_manifest, "Original completed message must atomically seal and admit finalization"
    asset = FrameworkRuntimeResultAsset.model_validate(sealed.source_manifest["runtime_result_asset"])
    assert asset.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert asset.verification_results == h.verification_oracle
    assert (asset.task_id, asset.attempt_id, asset.owner_epoch) == (h.owner.id, h.owner.attempt_id, before.owner_epoch)
    completion, = [event for event in await h.store.list_events(h.session_id) if event.type == "runtime.event_log_completed"]
    assert asset.projection_at == datetime.fromisoformat(completion.payload["completion"]["events"][-1]["timestamp"])
    link = sealed.recoveries[-1]
    assert len(sealed.recoveries) == len(before.recoveries) + 1
    assert link.resume_phase == "finalizing" and link.result_sha256 == asset.asset_sha256
    assert not finalizations and await h.store.get_latest_snapshot(h.session_id) is None
    successor = await h.tasks.get(link.task_id)
    assert successor.operation == "message_recover" and successor.parent_task_id == h.owner.id
    await run_delivery(h, successor.id)
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert len(finalizations) == 1
    assert finalizations[0]["runtime_result"].state == asset.runtime_result.restore().state
    saved = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    assert saved.phase == "projected"
    assert len([event for event in events if event.type == "turn.completed"]) == 1
    await run_delivery(h, h.owner.id)
    await run_delivery(h, successor.id)
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    assert await h.store.list_events(h.session_id) == events
    assert len(finalizations) == 1
    assert await h.tasks.get(h.owner.id) == h.expired
    if hasattr(h, "pre_graph_terminal"):
        assert await h.tasks.get(h.pre_graph_terminal.id) == h.pre_graph_terminal
    assert await h.calls.snapshot(h.binding) == budget
    assert h.effects == effects and h.adapter_requests == requests


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["event", "preparation", "job", "producer", "owner_epoch", "owner_worker", "owner_state", "active_lease", "cancel", "control", "later_turn"])
async def test_read_output_never_authorizes_sealing_after_admission_facts_change(message_asset_gap, monkeypatch, changed):
    h = message_asset_gap
    read = FrameworkRuntimeResults.read_completed_message

    async def read_then_change(*args, **kwargs):
        original = await read(*args, **kwargs)
        if changed == "later_turn":
            from uuid import uuid4
            from tests.test_runtime_binding_persistence import make_binding, make_command, make_payload, make_message

            later = make_binding(h.session_id, "later-turn")
            submitted = await h.tasks.submit_v2(make_command(h, later),
                make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, later),
                idempotency_key=uuid4().hex, model_budget=(await h.calls.snapshot(h.binding)).policy)
            active = await h.tasks.claim(submitted.id, worker_id="later-input", expected_binding=later)
            assert active.attempt_id
            return original
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            if changed == "event":
                cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || %s::jsonb WHERE id=%s",
                    ('{"seal_drift":true}', h.oracle.events[-1].id))
            elif changed == "preparation":
                from src.schemas.framework_approval import approval_value_hash
                stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
                stage["source_manifest"]["seal_drift"] = True
                stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
                cur.execute(f"UPDATE {h.stages._table} SET record=%s::jsonb WHERE task_id=%s", (json.dumps(stage), h.owner.id))
            elif changed == "job":
                cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET summary='seal drift' WHERE id=%s",
                    (h.oracle.state["tool_results"][0]["job_id"],))
            elif changed.startswith("owner_"):
                expression = {"owner_epoch": "owner_epoch=owner_epoch+1", "owner_worker": "worker_id='foreign-worker'",
                    "owner_state": "execution_state='running',expires_at=clock_timestamp()+interval '10 minutes'"}[changed]
                cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET {expression} WHERE binding_id=%s", (h.binding.binding_id,))
            elif changed == "active_lease":
                cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                    "jsonb_build_object('turn_lease_expires_at',clock_timestamp()+interval '10 minutes') WHERE id=%s", (h.session_id,))
            elif changed == "cancel":
                cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET interrupt_requested=true WHERE id=%s", (h.owner.id,))
            elif changed == "control":
                cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set(metadata,'{{control}}',"
                    "COALESCE(metadata->'control','{}'::jsonb) || jsonb_build_object('last_interrupt_binding_id',%s::text)) WHERE id=%s",
                    (h.binding.binding_id, h.session_id))
            else:
                cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET input_hash='foreign-input' WHERE id=%s", (h.owner.id,))
        return original

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_message", read_then_change)
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) is None
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert "runtime_result_asset" not in saved.source_manifest and not saved.recoveries
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == h.owner.id]


@pytest.mark.asyncio
async def test_asset_and_finalizer_publication_roll_back_together(message_asset_gap, monkeypatch):
    h = message_asset_gap
    before = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    event = h.tasks._event

    def fail_publication(cur, row, name, payload):
        if name == "framework.message_recover_queued":
            raise ConnectionError("injected message admission before commit")
        return event(cur, row, name, payload)

    reader = h.runtime._deep_agent_bound_runtime_adapter.open_native_reader
    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail_publication)
        with pytest.raises(ConnectionError, match="admission before commit"):
            await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert await h.store.list_events(h.session_id) == events
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == h.owner.id]
    result = await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader)
    assert result is not None
    assert len(h.stages.get(h.initial_task_id, h.session_id).recoveries) == 1


@pytest.mark.asyncio
async def test_parallel_readers_publish_one_original_asset_and_finalizer(message_asset_gap, monkeypatch):
    h = message_asset_gap
    read = FrameworkRuntimeResults.read_completed_message
    all_read = asyncio.Event()
    count = 0

    async def synchronize_reads(*args, **kwargs):
        nonlocal count
        value = await read(*args, **kwargs)
        count += 1
        if count == 3:
            all_read.set()
        await asyncio.wait_for(all_read.wait(), 30)
        return value

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_message", synchronize_reads)
    results = await asyncio.gather(*(h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) for _ in range(3)))
    assert all(item is not None for item in results) and len({item.id for item in results}) == 1
    assert len(h.stages.get(h.initial_task_id, h.session_id).recoveries) == 1


@pytest.mark.asyncio
async def test_a_late_material_reader_returns_the_already_committed_successor(message_asset_gap, monkeypatch):
    h = message_asset_gap
    read = FrameworkRuntimeResults.read_completed_message
    reached, committed = asyncio.Event(), asyncio.Event()
    count = 0

    async def delayed_read(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            reached.set()
            await asyncio.wait_for(committed.wait(), 30)
        return await read(*args, **kwargs)

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_message", delayed_read)
    reader = h.runtime._deep_agent_bound_runtime_adapter.open_native_reader
    slow = asyncio.create_task(h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader))
    try:
        await asyncio.wait_for(reached.wait(), 30)
        fast = await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader)
        committed.set()
        assert await slow == fast and fast is not None
    finally:
        committed.set()
        await asyncio.gather(slow, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["result_protocol", "verification", "checkpoint_prefix"])
async def test_incomplete_originals_never_invent_time_verification_or_replay(message_asset_gap, missing):
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_verification_projection import VERIFICATION_PREPARATION_KEY
    from src.runtime.framework_event_completion import completion_event_id

    h = message_asset_gap
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if missing == "checkpoint_prefix":
            from psycopg import sql

            cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s AND session_id=%s",
                (completion_event_id(h.binding), h.session_id))
            # A missing declaration is recoverable only with authenticated
            # checkpoint originals, never with just the currently visible rows.
            cur.execute(sql.SQL("UPDATE {}.checkpoints SET metadata=metadata - 'framework_checkpoint_event_prefix' WHERE thread_id=%s").format(
                sql.Identifier(h.settings.deep_agents.checkpoint_schema)).as_string(), (h.binding.thread_id,))
        else:
            stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
            graph = stage["source_manifest"]["graph_input_asset"]
            context = graph["runtime_preparation"]["initial_state"]["context_bundle"]
            if missing == "result_protocol":
                graph["feature_flags"].pop("result_projection_version")
                context.pop("framework_result_projection_version")
                graph["feature_flags"].pop("terminal_event_projection_version")
                context.pop("framework_terminal_event_preparation")
            else:
                graph["feature_flags"].pop("verification_projection_version")
                context.pop(VERIFICATION_PREPARATION_KEY)
            graph["asset_sha256"] = approval_value_hash({key: value for key, value in graph.items() if key != "asset_sha256"})
            stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
            cur.execute(f"UPDATE {h.stages._table} SET record=%s::jsonb WHERE task_id=%s", (json.dumps(stage), h.owner.id))
    before = h.stages.get(h.initial_task_id, h.session_id)
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) is None
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert await h.store.get_latest_snapshot(h.session_id) is None


@pytest.mark.asyncio
async def test_source_only_native_io_does_not_hold_message_admission_locks(message_asset_gap):
    from contextlib import asynccontextmanager

    h = message_asset_gap
    reader = h.runtime._deep_agent_bound_runtime_adapter.open_native_reader

    @asynccontextmanager
    async def independent_lock_probe(*args, **kwargs):
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='250ms'")
            cur.execute(f"SELECT id FROM {h.settings.database.postgres_session_table} WHERE id=%s FOR UPDATE", (h.session_id,))
            cur.execute(f"SELECT binding_id FROM {runtime_owner_table(h.settings)} WHERE binding_id=%s FOR UPDATE", (h.binding.binding_id,))
        async with reader(*args, **kwargs) as pair:
            yield pair

    assert await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=independent_lock_probe) is not None


@pytest.mark.asyncio
async def test_actual_redis_worker_acks_original_finalizer_and_duplicate_deliveries(message_asset_gap, monkeypatch):
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_framework_approval_recovery import outbox

    h = message_asset_gap
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    h.dispatcher = type(h.dispatcher)(h.settings, h.tasks, h.sessions, h.resources,
        framework_native_reader=adapter.open_native_reader,
        framework_capabilities=FrameworkWorkerCapabilities(h.binding.dependency_lock_sha256, True))
    h.sessions._runtime_service = h.runtime
    budget, effects = await h.calls.snapshot(h.binding), deepcopy(h.effects)

    def forbidden(*args, **kwargs):
        pytest.fail("ACK/finalization attempted original graph/model/orchestration/verification")

    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="ordinary-asset-gap", client=h.redis)
    assert queue.stream.startswith(h.prefix + ":")
    await queue.connect()
    errors = []

    async def handle(delivery):
        try:
            await h.dispatcher.handle_v2(delivery, worker_id="ordinary-asset-gap-worker")
        except Exception as exc:
            errors.append(exc)
            raise

    worker = RedisTaskWorker(queue, consumer="ordinary-asset-gap-worker", handler=handle, block_ms=0)
    original = outbox(h, h.owner.id)["payload"]
    await queue.enqueue(original)
    assert await worker.run_once() == 1, errors
    link, = h.stages.get(h.initial_task_id, h.session_id).recoveries
    successor = outbox(h, link.task_id)["payload"]
    await queue.enqueue(successor)
    assert await worker.run_once() == 1, errors
    events = await h.store.list_events(h.session_id)
    for command in (original, successor, original, successor):
        await queue.enqueue(command)
        assert await worker.run_once() == 1, errors
    assert not errors and await queue.pending() == []
    assert await h.store.list_events(h.session_id) == events
    assert (await h.tasks.get(link.task_id)).status == "completed"
    assert await h.tasks.get(h.owner.id) == h.expired
    assert await h.calls.snapshot(h.binding) == budget and h.effects == effects

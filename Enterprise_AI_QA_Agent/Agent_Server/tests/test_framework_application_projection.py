"""Application commit evidence, not a checkpoint or a snapshot, permits ACK recovery."""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import json
import multiprocessing
from uuid import uuid4

import pytest
import pytest_asyncio
from langgraph.graph import END, START, StateGraph

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.execution_context import current_framework_owner
from src.runtime.framework_message_stages import FrameworkMessageStages
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.framework_approval import approval_value_hash
from src.schemas.tool_job import ToolJobRecord, ToolJobStatus
from src.schemas.session import SessionSnapshot
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores, outbox, runtime_submitted
from tests.test_framework_message_stages import run, selected, stage
from tests.test_framework_model_calls import intent, usage


def worker_waiting_after_application_commit(settings_data, config_data, prefix, gate, window="after_commit"):
    """Real SDK/Deep Agents Worker paused at an explicit application boundary."""
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.schemas.model_config import ModelConfigRecord
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.application.runtime.runtime_service import RuntimeService
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from tests.test_agent_task_dispatch import build_services

    async def work():
        settings = Settings.model_validate(settings_data)
        config = ModelConfigRecord.model_validate(config_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
        await resources.connect()

        class Registry:
            def get_runtime_config(self, key):
                assert key == config.key
                return config

        calls = PostgresFrameworkModelCalls(settings)
        models = ModelRuntimeService(Registry(), settings)
        provider = DeepAgentCheckpointProvider(settings)
        adapter = DeepAgentRuntimeAdapter(model_resolver=models.resolve_langchain_chat_model,
            checkpointer_factory=provider.open,
            model_call_policy=FrameworkModelCallPolicy(calls=calls, model_config_resolver=models.get_model_config))
        runtime = RuntimeService(graph=None, model_runtime_service=models, tool_runtime_service=object(),
            tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(), deep_agent_enabled=True,
            deep_agent_bound_runtime_adapter=adapter)
        _, tasks, sessions, _ = build_services(settings, runtime, resources)

        async def pause(*args, **kwargs):
            await redis.rpush(gate, "runtime-result-frozen" if window == "before_finalization" else "application-committed")
            await asyncio.Event().wait()

        if window == "before_finalization":
            sessions._finalize_runtime_result = pause
        elif window == "after_commit":
            FrameworkMessageStages.projected = pause
        else:
            raise ValueError(f"Unknown application fault window: {window}")
        dispatcher = AgentTaskDispatcher(settings, tasks, sessions, resources, framework_model_calls=calls,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
            group="projection-kill", client=redis)
        await queue.connect()

        async def handle(item):
            await dispatcher.handle_v2(item, worker_id="projection-killed")

        await RedisTaskWorker(queue, consumer="projection-killed", handler=handle, block_ms=0).run_once()

    asyncio.run(work())


@pytest_asyncio.fixture
async def projection(admission, monkeypatch):
    h = admission
    h.provider = DeepAgentCheckpointProvider(h.settings)
    execute = h.runtime.execute_turn
    h.native_runs = 0
    h.model_phase = None
    h.output_text = "worker-result"

    async def execute_native(session, request, **kwargs):
        if h.model_phase is not None:
            owner = current_framework_owner()
            call = intent(position_version=1)
            await h.calls.prepare_initial(owner, call)
            if h.model_phase != "ready":
                await h.calls.start(owner, call.attempt_id)
            if h.model_phase == "completed":
                await h.calls.complete(owner, call.attempt_id, usage=usage())
            elif h.model_phase == "unknown":
                await h.calls.mark_unknown(owner, call.attempt_id, code="injected_unknown")
        async with h.provider.open(owner=current_framework_owner()) as saver:
            builder = StateGraph(dict)
            builder.add_node("finish", lambda value: {"answer": "worker-result"})
            builder.add_edge(START, "finish")
            builder.add_edge("finish", END)
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": request.turn_id}}
            await graph.ainvoke({"answer": ""}, config=config, durability="sync")
            assert (await graph.aget_state(config)).next == ()
            h.native_runs += 1
        result = await execute(session, request, **kwargs)
        result.output_text = h.output_text
        if not h.output_text:
            result.snapshot.graph_state["final_response"] = "not the committed output projection"
        result.snapshot.graph_state.update(context_bundle={"runtime_binding": request.runtime_binding.model_dump(mode="json")})
        result.state["context_bundle"] = dict(result.snapshot.graph_state["context_bundle"])
        return result

    monkeypatch.setattr(h.runtime, "execute_turn", execute_native)
    try:
        yield h
    finally:
        await h.provider.close()
        assert h.settings.deep_agents.checkpoint_schema == h.prefix + "_checkpoints"
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest_asyncio.fixture
async def projection_jobs(projection):
    h = projection
    h.settings.database.postgres_tool_artifact_table = h.prefix + "_artifacts"
    h.jobs = PostgresToolJobStore(h.settings)
    await h.jobs.initialize()
    now = datetime.now(timezone.utc)
    h.job = ToolJobRecord(id=uuid4().hex, session_id=h.session_id, turn_id=h.binding.turn_id,
        trace_id="projection-trace", call_id="projection-call", tool_key="read-fixture", tool_name="Read fixture",
        status=ToolJobStatus.completed, created_at=now, updated_at=now, output_payload={"text": "known result"})
    await h.jobs.save_job(h.job)
    h.job = await h.jobs.get_job(h.job.id)
    try:
        yield h
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {h.settings.database.postgres_tool_artifact_table}")
            cur.execute(f"DROP TABLE {h.settings.database.postgres_tool_job_table}")


async def committed_before_stage_ack(h, monkeypatch):
    original = await selected(h)
    projected = FrameworkMessageStages.projected

    async def lose_ack(*args, **kwargs):
        raise ConnectionError("injected loss after application commit before stage ACK")

    with monkeypatch.context() as patch:
        patch.setattr(FrameworkMessageStages, "projected", lose_ack)
        with pytest.raises(ConnectionError, match="before stage ACK"):
            await run(h, original)
    assert FrameworkMessageStages.projected is projected
    assert (await stage(h, original)).phase == "runtime_submitted"
    assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
    terminal = await h.tasks.claim(original.id, worker_id="projection-expiry", expected_binding=h.binding)
    assert terminal.status == "recovery_required"
    return original, await h.tasks.get(original.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_phase", [None, "completed"])
async def test_native_checkpoint_and_committed_application_recover_without_reexecuting(projection_jobs, monkeypatch, model_phase):
    h = projection_jobs
    h.model_phase = model_phase
    original, terminal = await committed_before_stage_ack(h, monkeypatch)
    before = await h.store.get_session(h.session_id)
    events = await h.store.list_events(h.session_id)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    assert successor is not None
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("projection-recovery", outbox(h, successor.id)["payload"]),
        worker_id="projection-replacement")
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert await h.tasks.get(original.id) == terminal
    saved = await stage(h, original)
    assert saved.phase == "projected" and saved.schema_version == 7
    after = await h.store.get_session(h.session_id)
    assert after.status.value == "completed" and after.metadata["control"]["control_state"] == "completed"
    def transcript(messages):
        values = [item.model_dump(mode="json") for item in messages]
        for value in values:
            value["metadata"].pop("delivery_status", None)
        return values
    assert transcript(after.messages) == transcript(before.messages)
    assert h.native_runs == 1 and len(h.runtime.calls) == 1
    assert len([e for e in events if e.type == "turn.completed"]) == 1
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1
    assert await h.jobs.get_job(h.job.id) == h.job
    calls = await h.calls.snapshot(h.binding)
    if model_phase is not None:
        assert calls.calls[0].phase == "completed" and calls.calls[0].settled_response_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "invoking", "unknown"])
async def test_unsettled_model_does_not_commit_completed_application(projection, phase):
    h = projection
    h.model_phase = phase
    task = await selected(h)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="unsettled model"):
        await run(h, task)
    assert "application_projection_commit" not in (await stage(h, task)).source_manifest
    saved = await h.store.get_session(h.session_id)
    assert len(saved.messages) == 1
    assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))


@pytest.mark.asyncio
async def test_a_snapshot_without_application_commit_cannot_authorize_projection_recovery(admission):
    h = admission
    original = await runtime_submitted(h)
    snapshot = SessionSnapshot(id="incomplete-application-snapshot", session_id=h.session_id, version=1,
        stage="completed", created_at=datetime.now(timezone.utc), graph_state={"turn_id": h.binding.turn_id,
            "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json")}})
    await h.store.save_snapshot(h.session_id, snapshot, turn_lease_token=original.lease_token)
    assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
    await h.tasks.claim(original.id, worker_id="snapshot-only-expiry", expected_binding=h.binding)
    assert await h.tasks.ensure_framework_message_recovery(original.id) is None


@pytest.mark.asyncio
async def test_receipt_failure_rolls_back_session_messages_and_completion_events(projection, monkeypatch):
    import src.runtime.framework_application_projection as commits

    h = projection
    task = await selected(h)
    original = commits.record_application_commit

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("injected failure after receipt SQL before commit")

    with monkeypatch.context() as patch:
        patch.setattr(commits, "record_application_commit", fail)
        with pytest.raises(ConnectionError, match="before commit"):
            await run(h, task)
    saved = await h.store.get_session(h.session_id)
    assert len(saved.messages) == 1 and saved.status.value != "completed"
    assert "application_projection_commit" not in (await stage(h, task)).source_manifest
    assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))
    # The earlier snapshot is deliberately a separate transaction, not proof.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {h.settings.database.postgres_snapshot_table} WHERE session_id=%s",
            (h.session_id,))
        assert cur.fetchone()["n"] == 1
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    await h.tasks.claim(task.id, worker_id="rollback-expiry", expected_binding=h.binding)
    assert await h.tasks.ensure_framework_message_recovery(task.id) is None


@pytest.mark.asyncio
async def test_stale_owner_cannot_repeat_application_commit_after_takeover(projection, monkeypatch):
    from copy import deepcopy

    h = projection
    captured = []
    save = h.store.save_session_with_events

    async def capture(session, events, **kwargs):
        if kwargs.get("framework_projection") is not None:
            captured.append((deepcopy(session), deepcopy(events), kwargs))
        return await save(session, events, **kwargs)

    monkeypatch.setattr(h.store, "save_session_with_events", capture)
    original, _ = await committed_before_stage_ack(h, monkeypatch)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    claimed = await h.tasks.claim(successor.id, worker_id="new-owner", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="new-owner", fencing_token=2)
    before = await h.store.get_session(h.session_id)
    session, events, kwargs = captured[0]
    with pytest.raises(ContinuationLeaseLostError):
        await save(session, events, **kwargs)
    assert await h.store.get_session(h.session_id) == before
    assert h.native_runs == 1


def drift(h, saved, kind):
    proof = saved.source_manifest["application_projection_commit"]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if kind == "snapshot":
            cur.execute(f"UPDATE {h.settings.database.postgres_snapshot_table} SET graph_state=graph_state || %s::jsonb WHERE id=%s",
                (json.dumps({"final_response": "changed"}), proof["snapshot_id"]))
        elif kind == "snapshot_header":
            cur.execute(f"UPDATE {h.settings.database.postgres_snapshot_table} SET version=version+100 WHERE id=%s", (proof["snapshot_id"],))
        elif kind == "message":
            cur.execute(f"UPDATE {h.settings.database.postgres_message_table} SET content='changed' WHERE id=%s",
                (proof["output_message_id"],))
        elif kind == "event":
            cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s", (next(iter(proof["event_hashes"])),))
        elif kind == "tool":
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET output_payload='{{\"changed\":true}}' WHERE id=%s",
                (h.job.id,))
        elif kind == "tool_attempt":
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET attempt=attempt+1 WHERE id=%s", (h.job.id,))
        elif kind == "model":
            cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{outcome_code}}','\"changed\"') WHERE binding_id=%s",
                (h.binding.binding_id,))
        elif kind == "verification":
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set(metadata,'{{verification_results}}',%s::jsonb) WHERE id=%s",
                (json.dumps([{"id": "changed", "turn_id": h.binding.turn_id}]), h.session_id))
        elif kind == "receipt":
            manifest = dict(saved.source_manifest)
            manifest["application_projection_commit"] = {**proof, "task_id": "foreign-task"}
            changed = saved.model_copy(update={"source_manifest": manifest, "source_sha256": approval_value_hash(manifest)})
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s",
                (changed.model_dump_json(), saved.task_id))
        else:
            raise AssertionError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["snapshot", "snapshot_header", "message", "event", "tool", "tool_attempt", "model", "verification", "receipt"])
async def test_changed_commit_facts_reject_recovery_without_new_execution(projection_jobs, monkeypatch, kind):
    h = projection_jobs
    h.model_phase = "completed"
    task, terminal = await committed_before_stage_ack(h, monkeypatch)
    drift(h, await stage(h, task), kind)
    assert await h.tasks.ensure_framework_message_recovery(task.id) is None
    assert await h.tasks.get(task.id) == terminal
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "waiting_approval", "cancelled"])
async def test_non_settled_or_cancelled_tool_cannot_create_completed_receipt(projection_jobs, status):
    h = projection_jobs
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status=%s WHERE id=%s", (status, h.job.id))
    task = await selected(h)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="unsettled tool"):
        await run(h, task)
    assert "application_projection_commit" not in (await stage(h, task)).source_manifest
    assert len((await h.store.get_session(h.session_id)).messages) == 1


@pytest.mark.asyncio
async def test_commit_is_rechecked_after_successor_publication_before_claim(projection_jobs, monkeypatch):
    h = projection_jobs
    original, _ = await committed_before_stage_ack(h, monkeypatch)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    drift(h, await stage(h, original), "tool")
    rejected = await h.tasks.claim(successor.id, worker_id="drifted", expected_binding=h.binding)
    assert rejected.status == "recovery_required"
    current = await h.tasks.get(successor.id)
    assert current.attempt_no == 0
    assert h.native_runs == 1


@pytest.mark.asyncio
async def test_loaded_projection_is_rechecked_in_the_stage_ack_transaction(projection_jobs, monkeypatch):
    import src.runtime.framework_application_projection as commits

    h = projection_jobs
    original, _ = await committed_before_stage_ack(h, monkeypatch)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    claimed = await h.tasks.claim(successor.id, worker_id="changed-after-read", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="changed-after-read", fencing_token=2)
    load = commits.load_committed_projection

    async def change_after_read(*args):
        result = await load(*args)
        drift(h, await stage(h, original), "message")
        return result

    monkeypatch.setattr(commits, "load_committed_projection", change_after_read)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="transcript changed"):
        await run(h, claimed)
    assert (await stage(h, original)).phase == "runtime_submitted"
    assert h.native_runs == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_output", [False, True])
async def test_exact_output_survives_store_restart_duplicate_delivery_and_second_owner_loss(projection, monkeypatch, empty_output):
    from src.runtime.postgres_session_store import PostgresSessionStore
    from tests.test_agent_task_dispatch import build_services

    h = projection
    h.output_text = "" if empty_output else "worker-result"
    original, terminal = await committed_before_stage_ack(h, monkeypatch)
    saved = await stage(h, original)
    proof = saved.source_manifest["application_projection_commit"]
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    old = await h.tasks.claim(successor.id, worker_id="lost-again", expected_binding=h.binding)
    await h.tasks.begin(old, resource_id="lost-again", fencing_token=2)
    assert await h.store.renew_turn_execution(h.session_id, old.lease_token, 0)
    await h.tasks.claim(old.id, worker_id="second-expiry", expected_binding=h.binding)
    lost = await h.tasks.get(old.id)
    next_task = await h.tasks.ensure_framework_message_recovery(old.id)
    assert next_task is not None
    # A newer unrelated snapshot must never replace the precise proven result.
    await PostgresSessionStore(h.settings).save_snapshot(h.session_id, SessionSnapshot(
        id="unrelated-snapshot", session_id=h.session_id, version=999, stage="completed",
        created_at=datetime.now(timezone.utc), graph_state={"turn_id": "different-turn", "final_response": "wrong"}))
    _, tasks, sessions, _ = build_services(h.settings, h.runtime, h.resources)
    claimed = await tasks.claim(next_task.id, worker_id="restarted", expected_binding=h.binding)
    await tasks.begin(claimed, resource_id="restarted", fencing_token=3)
    h.sessions = sessions
    response = await run(h, claimed)
    assert response.output.content == h.output_text
    assert response.output.id == (proof["output_message_id"] or proof["snapshot_id"])
    assert len((await h.store.get_session(h.session_id)).messages) == (1 if empty_output else 2)
    assert await tasks.get(original.id) == terminal and await tasks.get(old.id) == lost
    with pytest.raises(ContinuationLeaseLostError):
        await FrameworkMessageStages(tasks).projected(old.runtime_owner, response.output.id)
    await tasks.finish(claimed, status="completed", result_status=response.session.status.value)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("duplicate", outbox(h, next_task.id)["payload"]), worker_id="duplicate")
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_official_deep_agent_worker_killed_after_commit_recovers_without_second_http_request(admission):
    from redis.asyncio import Redis
    from src.application.models.framework_model_call_policy import model_config_fingerprint
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.redis_task_worker import RedisTaskWorker
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_runtime_binding_persistence import make_command
    from tests.test_framework_model_admission import submit
    from src.runtime.resource_lease_manager import ResourceLease

    h = admission
    # Importing the official graph can synchronously block the event loop on a
    # cold artifact. A 1s lease tests startup speed, not this commit boundary.
    # Keep a normal lease while executing; expire it only after the hard kill.
    h.settings.orchestration.resource_default_lease_seconds = 30
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=45)
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
            gate = h.prefix + ":projection-committed"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate))
            process.start()
            assert await waiting.blpop(gate, timeout=40) is not None
            before = await h.store.get_session(h.session_id)
            proof = (await stage(h, original)).source_manifest["application_projection_commit"]
            assert before.status.value == "completed" and len(requests) == 1
            assert process.is_alive()
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            active = await h.tasks.get(original.id)
            assert await h.store.renew_turn_execution(h.session_id, active.lease_token, 0)
            lease_data = next(item for item in await h.resources.list_active() if item["session_id"] == h.session_id)
            lease = ResourceLease(**{key: value for key, value in lease_data.items()
                if key in ResourceLease.__dataclass_fields__}, lease_expire_at=datetime.now(timezone.utc))
            assert await h.resources.renew(lease, lease_seconds=1)
            await asyncio.sleep(1.1)
            await h.resources.reap_expired()
            h.settings.orchestration.resource_default_lease_seconds = 300
            await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
            terminal = await h.tasks.get(original.id)
            successor = await h.tasks.ensure_framework_message_recovery(original.id)
            assert successor is not None
            h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1

            async def handle(item):
                await h.dispatcher.handle_v2(item, worker_id="projection-successor")

            worker = RedisTaskWorker(queue, consumer="projection-successor", handler=handle, block_ms=0)
            assert await worker.run_once() == 1
            assert (await h.tasks.get(successor.id)).status == "completed"
            assert await h.tasks.get(original.id) == terminal
            after = await h.store.get_session(h.session_id)
            assert after.status.value == "completed" and len(after.messages) == 2
            assert after.messages[-1].id == proof["output_message_id"]
            assert after.messages[-1].content == before.messages[-1].content
            assert len(requests) == 1 and h.runtime.calls == []
            assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")

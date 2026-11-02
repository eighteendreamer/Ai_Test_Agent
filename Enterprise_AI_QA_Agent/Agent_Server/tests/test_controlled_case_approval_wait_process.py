"""Actual formal Worker parking kill windows, not coroutine cancellation."""
import asyncio
import json
import multiprocessing
from types import SimpleNamespace

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_pending_approvals import pending_formal
from tests.test_controlled_case_approval_wait import facts, clock
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_settlement_dispatch import queue_for
from tests.test_framework_approval_projection import counts
from tests import framework_worker_diagnostics as entries


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"dispatch": True}], indirect=True)
@pytest.mark.parametrize("failure", [False, True])
async def test_borrowed_process_tables_survive_bridge_return_or_failure(pending_formal, failure):
    from contextlib import nullcontext
    from tests.test_controlled_case_graph import business_bridge

    p = pending_formal
    before = counts(p.h), database_digest(p.s)
    class ChildFixtureFailure(RuntimeError):
        pass
    with pytest.raises(ChildFixtureFailure) if failure else nullcontext():
        async with business_bridge(p.h, [], approval_required=True, table_names=(
                p.h.settings.database.postgres_tool_job_table, p.h.settings.database.postgres_tool_artifact_table)):
            if failure:
                raise ChildFixtureFailure("isolated borrowed fixture failure")
    assert (counts(p.h), database_digest(p.s)) == before
    assert p.requests == p.effects == []


def wait_worker(settings_data, config_data, prefix, raw, gate, mode):
    """Fresh processes receive only configuration and the original wire reference."""
    from contextlib import AsyncExitStack
    from dataclasses import replace
    from redis import Redis as SyncRedis
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter
    from src.application.runtime.controlled_case_executor import ControlledCaseExecutor
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
    from src.application.runtime.tool_job_service import ToolJobService
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.controlled_case_approval_wait import ControlledCaseApprovalWaits
    from src.runtime.controlled_case_assessment import ControlledCaseAssessments
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements
    from src.runtime.framework_approval_projection import FrameworkApprovalProjection
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from src.schemas.model_config import ModelConfigRecord
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    from tests.test_agent_task_dispatch import build_services, Runtime
    from tests.test_controlled_case_execution_lease import capabilities
    from tests.test_controlled_case_graph import business_bridge

    async def work():
        producer = mode in {"before_transaction", "inside_transaction", "after_commit_before_reply", "before_redis_ack"}
        if not producer and mode not in {"read", "read_incompatible", "expired_probe"}:
            raise ValueError("Unknown isolated formal wait window")
        settings = Settings.model_validate(settings_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65)
        resources = RedisResourceLeaseManager(settings.database.redis_url, client=redis,
            key_prefix=prefix, cleanup_stream=prefix + ":cleanup")
        await resources.connect()
        store, tasks, sessions, dispatcher = build_services(settings, Runtime(), resources)
        provider = None
        async with AsyncExitStack() as stack:
            try:
                def forbidden(*args, **kwargs):
                    # Dispatcher may defer exceptions; retain evidence even then.
                    with SyncRedis.from_url(settings.database.redis_url) as sync:
                        sync.incr(prefix + ":formal-wait-forbidden")
                    raise AssertionError("Receipt-only Worker tried to execute, claim or acquire authority")

                task = await tasks.get(raw["task_id"])
                if producer:
                    config = ModelConfigRecord.model_validate(config_data)
                    models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), settings)
                    provider = DeepAgentCheckpointProvider(settings)
                    adapter = ControlledCaseGraphAdapter(model_resolver=models.resolve_langchain_chat_model,
                        checkpointer_factory=provider.open, model_call_policy=FrameworkModelCallPolicy(
                            calls=PostgresFrameworkModelCalls(settings), model_config_resolver=models.get_model_config))
                    h = SimpleNamespace(settings=settings, models=models, store=store, session_id=task.session_id,
                        binding=task.runtime_binding, task=task)
                    bridge = await stack.enter_async_context(business_bridge(h, [], approval_required=True, table_names=(
                        settings.database.postgres_tool_job_table, settings.database.postgres_tool_artifact_table)))
                    # If anything bypasses native HITL, the parent must see it.
                    async def effect(*args, **kwargs):
                        await redis.incr(prefix + ":formal-wait-effects")
                        raise AssertionError("Unapproved formal tool executed")
                    bridge.runtime._tool_runtime_service._handlers["inspect_source"] = effect
                    runtime_service, projection = bridge.runtime, bridge.projection
                    executor = ControlledCaseExecutor(task_store=tasks, graph_adapter=adapter)
                else:
                    assert config_data is None
                    projection = FrameworkApprovalProjection(settings, sessions=store,
                        jobs=ToolJobService(PostgresToolJobStore(settings)))
                    runtime_service = SimpleNamespace(_deep_agent_governed_tools_enabled=True,
                        _tool_governance_service=object())
                    adapter = ControlledCaseGraphAdapter(model_resolver=forbidden, checkpointer_factory=forbidden,
                        model_call_policy=FrameworkModelCallPolicy(calls=PostgresFrameworkModelCalls(settings),
                            model_config_resolver=forbidden))
                    executor = ControlledCaseExecutor(task_store=tasks, graph_adapter=adapter)
                    executor.execute = forbidden
                    RuntimeExecutionOwner.__init__ = forbidden
                    tasks.begin = tasks.finish = resources.acquire = forbidden
                    if mode != "expired_probe":
                        tasks.claim_controlled_case = tasks._update_status = forbidden
                    sessions._finalize_runtime_result = forbidden
                    projection.project_many_in_transaction = forbidden
                    ControlledCaseGraphAdapter.execute = forbidden
                    ModelRuntimeService.resolve_langchain_chat_model = forbidden

                service = ControlledCaseApprovalWaits(tasks, projection=projection)
                runtime = ControlledCaseRuntime(runtime_service=runtime_service, session_service=sessions,
                    assessment_service=ControlledCaseAssessments(tasks), settlement_service=ControlledCaseSettlements(tasks),
                    executor=executor, approval_wait_service=service)
                dispatcher._controlled_case_runtime = runtime
                caps = replace(capabilities(), controlled_case_available=producer or mode == "expired_probe",
                    controlled_case_approval_wait_max_version=0 if mode == "read_incompatible" else 1)
                dispatcher.set_framework_capabilities(caps)
                queue = RedisTaskQueue(settings.database.redis_url,
                    stream=settings.orchestration.agent_task_v2_stream, group="scoped-settlement", client=redis)
                await queue.connect()

                async def pause():
                    await redis.rpush(gate, mode)
                    await asyncio.Event().wait()

                if mode == "before_transaction":
                    park = service.park
                    async def parked_pause(*args, **kwargs):
                        await pause()
                        return await park(*args, **kwargs)
                    service.park = parked_pause
                elif mode == "inside_transaction":
                    append = store.append_event_in_transaction
                    def event_pause(*args, **kwargs):
                        result = append(*args, **kwargs)
                        if args[2].type == "test_run.case_approval_waiting":
                            with SyncRedis.from_url(settings.database.redis_url, socket_timeout=65) as sync:
                                sync.rpush(gate, mode)
                                assert sync.blpop(gate + ":release", timeout=60), "Uncommitted wait gate timed out"
                        return result
                    store.append_event_in_transaction = event_pause
                elif mode == "after_commit_before_reply":
                    park = service._park
                    def committed_pause(*args, **kwargs):
                        result = park(*args, **kwargs)  # Connection context has really committed.
                        with SyncRedis.from_url(settings.database.redis_url, socket_timeout=65) as sync:
                            sync.rpush(gate, mode)
                            assert sync.blpop(gate + ":release", timeout=60), "Committed wait gate timed out"
                        return result
                    service._park = committed_pause
                elif mode == "before_redis_ack":
                    ack = queue.ack_owned
                    async def ack_pause(*args, **kwargs):
                        await pause()
                        return await ack(*args, **kwargs)
                    queue.ack_owned = ack_pause

                processed = await RedisTaskWorker(queue, consumer="formal-wait-" + mode,
                    handler=lambda delivery: dispatcher.handle_v2(delivery, worker_id="formal-wait-" + mode),
                    block_ms=0, reclaim_idle_ms=300, task_timeout_seconds=90).run_once()
                assert not producer, "Original Worker missed its hard-kill window"
                result = {"processed": processed}
                if processed:
                    result["receipt"] = (await service.acknowledge_delivery(raw, capabilities=caps)).model_dump(mode="json")
                await redis.rpush(gate, json.dumps(result))
            finally:
                if provider is not None:
                    await provider.close()
                await redis.aclose()
    asyncio.run(work())


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"dispatch": True}], indirect=True)
@pytest.mark.parametrize("window", ["before_transaction", "inside_transaction", "after_commit_before_reply", "before_redis_ack"])
async def test_killed_formal_worker_only_acks_committed_wait_and_never_replays_native_work(pending_formal, window):
    from redis.asyncio import Redis

    p = pending_formal
    assert p.outcome is None and p.requests == [], "Only the disposable Worker may claim or execute this input"
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "queued"
    children = []
    settings = p.s.h.settings.model_copy(deep=True)
    settings.orchestration.resource_default_lease_seconds = 2
    p.s.h.resources._cleanup_stream = p.s.h.prefix + ":cleanup"
    waiting = Redis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65)
    gate = p.s.h.prefix + ":formal-wait-kill"
    raw = delivery(p.s)
    queue = await queue_for(p, raw)
    initial = (await p.s.h.tasks.get(p.h.task.id)).input
    budget = await p.s.calls.snapshot(p.s.binding)
    context = multiprocessing.get_context("spawn")
    def start(mode, *, config=None):
        child = context.Process(target=entries.controlled_case_wait_worker_process,
            args=(settings.model_dump(mode="json"), config, p.s.h.prefix, raw, gate + ":" + mode, mode))
        children.append(child)
        child.start()
        return child
    async def result(child, mode):
        observed = await waiting.blpop(gate + ":" + mode, timeout=50)
        assert observed is not None, f"Wait Worker mode={mode} alive={child.is_alive()} exitcode={child.exitcode}"
        await asyncio.to_thread(child.join, 10)
        assert not child.is_alive() and child.exitcode == 0
        return json.loads(observed[1])
    try:
        producer = start(window, config=p.config.model_dump(mode="json"))
        reached = await waiting.blpop(gate + ":" + window, timeout=50)
        assert reached is not None and reached[1] == window, (
            f"Wait producer alive={producer.is_alive()} exitcode={producer.exitcode}")
        producer.kill()
        await asyncio.to_thread(producer.join, 10)
        assert not producer.is_alive() and producer.exitcode not in {None, 0}
        committed = window in {"after_commit_before_reply", "before_redis_ack"}
        task = await p.s.h.tasks.get(p.h.task.id)
        detail = await p.s.d.runs.get_run(p.s.detail.run.id)
        item = next(i for i in detail.items if i.id == p.s.item.id)
        attempt = next(a for a in detail.attempts if a.id == p.s.attempt.id)
        session = await p.s.h.store.get_session(p.s.session.id)
        assert item.status == attempt.status == session.status.value == ("waiting_approval" if committed else "running")
        assert task.status == ("completed" if committed else "running")
        assert task.result_status == ("waiting_approval" if committed else None)
        assert bool(task.result_payload) is committed and task.attempt_no == 1
        assert task.input == initial and attempt.id == p.s.attempt.id and attempt.checkpoint_version == 1
        agent_attempt_id = task.attempt_id
        assert item.result_id is None and detail.results == []
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await p.s.h.redis.xlen(queue.stream) == 1
        tally = counts(p.h)
        assert tally["approvals"] == tally["jobs"] == tally["links"] == (2 if committed else 0)
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {p.s.h.tasks._tasks}_decision")
            assert cur.fetchone()["n"] == 0
        events = await p.s.h.store.list_events(p.s.session.id)
        assert len([e for e in events if e.type == "test_run.case_approval_waiting"]) == int(committed)
        assert len([e for e in events if e.type == "agent_task.completed"]) == int(committed)
        snapshot = await p.s.calls.snapshot(p.s.binding)
        assert snapshot.policy == budget.policy and len(snapshot.calls) == 1 and snapshot.calls[0].phase == "completed"
        assert len(p.requests) == 1 and p.effects == []
        # The producer owns its lease. Use actual TTL/reaping, not another
        # interpreter's resource credentials, to close its abandoned slot.
        async with asyncio.timeout(8):
            while await p.s.h.resources.list_active():
                await asyncio.sleep(.1)
        await p.s.h.resources.reap_expired()
        assert await p.s.h.resources.usage_snapshot() == {}
        before = facts(p)
        reader = start("read_incompatible")
        assert await result(reader, "read_incompatible") == {"processed": 0}
        assert facts(p) == before
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        reader = start("read")
        response = await result(reader, "read")
        assert response["processed"] == int(committed)
        assert facts(p) == before
        if committed:
            receipt = task.result_payload["controlled_case_approval_wait"]
            assert response["receipt"] == receipt and len(receipt["batch"]) == 2
            assert session.metadata["pending_turn"]["pending_approval_ids"] == [e["approval_id"] for e in receipt["batch"]]
            await queue.enqueue(raw)
            reader = start("read")
            assert await result(reader, "read") == response
            assert facts(p) == before
            assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 0
            now = clock(p)
            from datetime import timedelta
            assert await p.s.calls.root_activity_seconds(p.s.binding, as_of=now) == (
                await p.s.calls.root_activity_seconds(p.s.binding, as_of=now + timedelta(seconds=60)))
        else:
            from tests.test_controlled_case_lease_reconciliation import expire

            now = clock(p)
            expire(p.s, now)
            original = database_digest(p.s)
            assert await p.s.d.runs.recover_expired(p.s.item.run_id, now) == 0
            assert database_digest(p.s) == original
            assert await p.s.h.store.renew_turn_execution(p.s.session.id, task.lease_token, 0)
            reader = start("expired_probe")
            assert await result(reader, "expired_probe") == {"processed": 0}
            classified = await p.s.h.tasks.get(task.id)
            assert classified.status == "recovery_required" and classified.error_code == "execution_owner_expired"
            assert classified.attempt_id == agent_attempt_id and classified.attempt_no == 1
            assert classified.result_payload is None
            assert (await p.s.d.runs.get_item(item.id)).result_id is None
            before = facts(p)
            reader = start("read")
            assert await result(reader, "read") == {"processed": 0}
            assert facts(p) == before
            assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert len(p.requests) == 1 and p.effects == []
        assert await p.s.h.redis.get(p.s.h.prefix + ":formal-wait-effects") is None
        assert await p.s.h.redis.get(p.s.h.prefix + ":formal-wait-forbidden") is None
        assert await p.s.h.resources.list_active() == []
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {p.s.d.runs._result_table}")
            assert cur.fetchone()["n"] == 0
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
                await asyncio.to_thread(child.join, 10)
                assert not child.is_alive()
        await waiting.aclose()

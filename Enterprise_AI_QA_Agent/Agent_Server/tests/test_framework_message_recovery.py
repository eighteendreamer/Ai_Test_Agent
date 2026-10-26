from __future__ import annotations

import asyncio
import json
import multiprocessing
from uuid import uuid4

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.agent_task import AgentTaskCommand, parse_agent_task_command
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_message_stages import selected, stage


def worker_before_message_stage(settings_data, prefix, gate):
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.runtime.framework_message_stages import FrameworkMessageStages
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from tests.test_agent_task_dispatch import build_services, Runtime

    async def work():
        settings = Settings.model_validate(settings_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
        await resources.connect()
        _, _, _, dispatcher = build_services(settings, Runtime(), resources)
        dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))

        async def pause(*args, **kwargs):
            await redis.rpush(gate, "before-stage")
            await asyncio.Event().wait()

        FrameworkMessageStages.begin = pause
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
            group="message-kill", client=redis)
        await queue.connect()

        async def handle(item):
            await dispatcher.handle_v2(item, worker_id="message-kill-origin")

        await RedisTaskWorker(queue, consumer="message-kill-origin", handler=handle, block_ms=0).run_once()

    asyncio.run(work())


@pytest_asyncio.fixture(autouse=True)
async def isolate_recovery_stores(admission):
    admission.settings.orchestration.agent_task_v2_stream = admission.prefix + ":message-recovery"
    admission.settings.deep_agents.checkpoint_schema = admission.prefix + "_checkpoints"
    admission.settings.database.postgres_tool_job_table = admission.prefix + "_jobs"


async def expired(h):
    task = await selected(h)
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    result = await h.tasks.claim(task.id, worker_id="expiry-observer", expected_binding=h.binding)
    assert result.status == "recovery_required"
    return task, await h.tasks.get(task.id)


def outbox(h, task_id):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{task_id}",))
        return cur.fetchone()


@pytest.mark.asyncio
async def test_submitted_origin_admits_one_successor_with_original_input_budget_and_outbox(admission):
    h = admission
    original, terminal = await expired(h)
    before = await h.calls.snapshot(h.binding)
    results = await asyncio.gather(*(h.tasks.ensure_framework_message_recovery(original.id) for _ in range(4)))
    assert all(item is not None for item in results)
    assert len({item.id for item in results}) == 1
    successor = results[0]
    assert successor.operation == "message_recover" and successor.status == "queued"
    assert successor.parent_task_id == original.id and successor.reference_id == original.id
    assert successor.input == original.input and successor.user_message_id == original.user_message_id
    assert successor.runtime_binding == h.binding and await h.tasks.get(original.id) == terminal
    assert await h.calls.snapshot(h.binding) == before
    saved = await stage(h, original)
    assert saved.task_id == original.id and saved.current_task_id == successor.id
    assert saved.recoveries[0].predecessor_attempt_id == original.attempt_id
    command = parse_agent_task_command(outbox(h, successor.id)["payload"])
    assert command.operation == "message_recover" and command.runtime_binding == h.binding
    with pytest.raises(ValueError, match="operation"):
        AgentTaskCommand.model_validate({**command.model_dump(mode="json", exclude={"runtime_binding"}), "schema_version": 1})


@pytest.mark.asyncio
async def test_worker_redelivery_executes_successor_once_and_preserves_terminal_origin(admission):
    h = admission
    original, terminal = await expired(h)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("expired-original", h.command.model_dump(mode="json")), worker_id="replacement")
    successors = [item for item in await h.tasks.list_session(h.session_id) if item.operation == "message_recover"]
    assert len(successors) == 1
    successor = successors[0]
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="message-recovery", client=h.redis)
    await queue.connect()
    assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 2

    errors = []

    async def handle(item):
        try:
            await h.dispatcher.handle_v2(item, worker_id="message-recovery-worker")
        except Exception as exc:
            errors.append(exc)
            raise

    worker = RedisTaskWorker(queue, consumer="message-recovery-worker", handler=handle, block_ms=0)
    for _ in range(2):
        processed = await worker.run_once()
        if errors:
            raise errors[0]
        assert processed == 1
    assert (await h.tasks.get(successor.id)).status == "completed" and len(h.runtime.calls) == 1
    frozen = await stage(h, original)
    assert frozen.phase == "projected" and frozen.current_task_id == successor.id
    assert await stage(h, await h.tasks.get(successor.id)) == frozen
    await h.dispatcher.handle_v2(QueuedTask("duplicate", outbox(h, successor.id)["payload"]), worker_id="duplicate")
    assert len(h.runtime.calls) == 1 and await h.tasks.get(original.id) == terminal
    assert len((await h.store.get_session(h.session_id)).messages) == 2


@pytest.mark.asyncio
async def test_publication_rolls_back_stage_successor_and_outbox_together(admission, monkeypatch):
    h = admission
    original, terminal = await expired(h)
    before = await stage(h, original)
    event = h.tasks._event

    def fail(cur, row, event_type, payload):
        if event_type == "framework.message_recover_queued":
            raise ConnectionError("injected message recovery publication failure")
        return event(cur, row, event_type, payload)

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="injected"):
            await h.tasks.ensure_framework_message_recovery(original.id)
    assert await stage(h, original) == before and await h.tasks.get(original.id) == terminal
    assert len(await h.tasks.list_session(h.session_id)) == 1
    assert await h.tasks.ensure_framework_message_recovery(original.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["orchestrating", "input_drift", "interrupted", "missing_admission", "budget_drift"])
async def test_recovery_rejects_unproved_or_changed_origin(admission, unsafe):
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest

    h = admission
    if unsafe == "orchestrating":
        original = await selected(h)
        session = await h.store.get_session(h.session_id)
        await FrameworkMessageStages(h.tasks).begin(original.runtime_owner, message_source_manifest(session, h.binding, h.payload))
        assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
        await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
    else:
        original, _ = await expired(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if unsafe == "input_drift":
            cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=input_payload || %s::jsonb WHERE id=%s",
                (json.dumps({"content": "changed"}), original.id))
        elif unsafe == "interrupted":
            cur.execute(f"UPDATE {h.tasks._tasks} SET interrupt_requested=TRUE WHERE id=%s", (original.id,))
        elif unsafe == "missing_admission":
            cur.execute(f"DELETE FROM {h.tasks._tasks}_message_stage WHERE task_id=%s", (original.id,))
        elif unsafe == "budget_drift":
            cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,'{{max_calls}}','99') WHERE binding_id=%s",
                (h.binding.binding_id,))
    before = await h.tasks.get(original.id)
    assert await h.tasks.ensure_framework_message_recovery(original.id) is None
    assert len(await h.tasks.list_session(h.session_id)) == 1 and await h.tasks.get(original.id) == before
    assert h.runtime.calls == []


@pytest.mark.asyncio
async def test_successor_expired_before_stage_begin_has_new_lineage_and_old_owner_is_rejected(admission):
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest

    h = admission
    original, terminal = await expired(h)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    old = await h.tasks.claim(successor.id, worker_id="lost-successor", expected_binding=h.binding)
    await h.tasks.begin(old, resource_id="lost-successor", fencing_token=2)
    assert await h.store.renew_turn_execution(h.session_id, old.lease_token, 0)
    await h.tasks.claim(old.id, worker_id="expiry", expected_binding=h.binding)
    lost = await h.tasks.get(old.id)
    next_task = await h.tasks.ensure_framework_message_recovery(old.id)
    assert next_task.id != old.id and next_task.parent_task_id == old.id and next_task.reference_id == original.id
    current = await h.tasks.claim(next_task.id, worker_id="current-successor", expected_binding=h.binding)
    await h.tasks.begin(current, resource_id="current-successor", fencing_token=3)
    assert current.runtime_owner.owner_epoch > old.runtime_owner.owner_epoch
    session = await h.store.get_session(h.session_id)
    stages = FrameworkMessageStages(h.tasks)
    with pytest.raises(ContinuationLeaseLostError):
        await stages.begin(old.runtime_owner, message_source_manifest(session, h.binding, h.payload))
    saved = await stages.begin(current.runtime_owner, message_source_manifest(session, h.binding, h.payload))
    assert saved.task_id == original.id and saved.current_task_id == current.id and len(saved.recoveries) == 2
    assert await h.tasks.get(old.id) == lost and await h.tasks.get(original.id) == terminal


@pytest.mark.asyncio
async def test_recovery_precedes_queued_later_input_without_duplicate_user_message(admission):
    from src.runtime.task_deferred import TaskDeferred
    from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload

    h = admission
    original, _ = await expired(h)
    later_binding = make_binding(h.session_id, "later-" + uuid4().hex)
    later = await h.tasks.submit_v2(make_command(h, later_binding), make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, later_binding),
        idempotency_key=uuid4().hex, model_budget=h.budget)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    with pytest.raises(TaskDeferred, match="recovery"):
        await h.tasks.claim(later.id, worker_id="must-wait", expected_binding=later_binding)
    claimed = await h.tasks.claim(successor.id, worker_id="recovery-first", expected_binding=h.binding)
    assert claimed.status == "claimed" and claimed.runtime_owner.binding == h.binding
    assert len((await h.store.get_session(h.session_id)).messages) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["input", "parent", "stage", "proof", "proof_version", "budget", "missing_budget"])
async def test_claim_rechecks_published_successor_and_does_not_execute_changed_facts(admission, changed):
    h = admission
    original, terminal = await expired(h)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "input":
            cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=input_payload || %s::jsonb WHERE id=%s",
                (json.dumps({"content": "changed after publication"}), successor.id))
        elif changed == "parent":
            cur.execute(f"UPDATE {h.tasks._tasks} SET parent_task_id=%s WHERE id=%s", (successor.id, successor.id))
        elif changed == "stage":
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=jsonb_set(record,'{{phase}}','\"orchestrating\"') "
                "WHERE task_id=%s", (original.id,))
        elif changed == "proof":
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=jsonb_set(record,'{{recoveries}}','[]') "
                "WHERE task_id=%s", (original.id,))
        elif changed == "proof_version":
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=jsonb_set(record,'{{schema_version}}','1') "
                "WHERE task_id=%s", (original.id,))
        elif changed == "missing_budget":
            cur.execute(f"DELETE FROM {h.calls.budgets_table} WHERE binding_id=%s", (h.binding.binding_id,))
        else:
            cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,'{{max_calls}}','99') WHERE binding_id=%s",
                (h.binding.binding_id,))
    rejected = await h.tasks.claim(successor.id, worker_id="reject-changed", expected_binding=h.binding)
    assert rejected.status == "recovery_required" and rejected.error_code == "framework_message_recovery_changed"
    assert rejected.attempt_id is None and await h.tasks.get(original.id) == terminal and h.runtime.calls == []
    assert await h.tasks.ensure_framework_message_recovery(successor.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fact", ["model", "checkpoint", "pending_write"])
async def test_submitted_stage_cannot_override_contradictory_execution_facts(admission, fact):
    from langgraph.checkpoint.base import empty_checkpoint
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from tests.test_framework_model_calls import intent

    h = admission
    original = await selected(h)
    provider = DeepAgentCheckpointProvider(h.settings)
    try:
        if fact == "model":
            await h.calls.prepare_initial(original.runtime_owner, intent(position_version=1))
        else:
            async with provider.open(owner=original.runtime_owner) as saver:
                config = {"configurable": {"thread_id": h.binding.thread_id, "checkpoint_ns": ""}}
                if fact == "checkpoint":
                    await saver.aput(config, empty_checkpoint(), {}, {})
                else:
                    config["configurable"]["checkpoint_id"] = str(uuid4())
                    await saver.aput_writes(config, [("result", "orphan write")], "orphan-task")
        assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
        await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
        before = await stage(h, original)
        assert await h.tasks.ensure_framework_message_recovery(original.id) is None
        assert await stage(h, original) == before and len(await h.tasks.list_session(h.session_id)) == 1
    finally:
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_independent_worker_kill_before_orchestration_recovers_through_official_graph_once(admission):
    from redis.asyncio import Redis
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.application.runtime.runtime_service import RuntimeService
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    h.settings.orchestration.resource_default_lease_seconds = 1
    provider = DeepAgentCheckpointProvider(h.settings)
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=40)
    process = None
    try:
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url)

            class Registry:
                def get_runtime_config(self, key):
                    assert key == config.key
                    return config

            models = ModelRuntimeService(Registry(), h.settings)
            adapter = DeepAgentRuntimeAdapter(model_resolver=models.resolve_langchain_chat_model,
                checkpointer_factory=provider.open, model_call_policy=FrameworkModelCallPolicy(
                    calls=h.calls, model_config_resolver=models.get_model_config))
            h.sessions._runtime_service = RuntimeService(graph=None, model_runtime_service=models,
                tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(),
                deep_agent_enabled=True, deep_agent_bound_runtime_adapter=adapter)
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review"})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            task = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="message-kill", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":before-stage"
            process = multiprocessing.get_context("spawn").Process(target=worker_before_message_stage,
                args=(h.settings.model_dump(mode="json"), h.prefix, gate))
            process.start()
            assert await waiting.blpop(gate, timeout=35) is not None
            original = await h.tasks.get(task.id)
            assert original.status == "running" and (await stage(h, task)).phase == "submitted"
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0
            assert process.is_alive()
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
            # Let the killed Worker's real one-second admission slot expire.
            await asyncio.sleep(1.1)
            await h.resources.reap_expired()
            h.settings.orchestration.resource_default_lease_seconds = 300
            dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources, framework_model_calls=h.calls,
                framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
            errors = []

            async def handle(item):
                try:
                    await dispatcher.handle_v2(item, worker_id="message-kill-replacement")
                except Exception as exc:
                    errors.append(exc)
                    raise

            worker = RedisTaskWorker(queue, consumer="message-kill-replacement", handler=handle, block_ms=0, reclaim_idle_ms=1)
            assert await worker.run_once() == 1
            terminal = await h.tasks.get(task.id)
            assert terminal.status == "recovery_required" and terminal.attempt_id == original.attempt_id
            successor = await h.tasks.ensure_framework_message_recovery(task.id)
            assert successor is not None and requests == []
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            processed = await worker.run_once()
            if errors:
                raise errors[0]
            assert processed == 1
            completed = await h.tasks.get(successor.id)
            assert completed.status == "completed" and completed.attempt_no == 1
            assert len(requests) == 1 and (await stage(h, task)).phase == "projected"
            budget = await h.calls.snapshot(h.binding)
            assert budget.policy == h.budget and budget.reserved_calls == 1 and budget.actual_tokens == 10
            assert budget.calls[0].owner_task_id == successor.id and budget.calls[0].owner_epoch > 1
            await dispatcher.handle_v2(QueuedTask("duplicate", outbox(h, successor.id)["payload"]), worker_id="duplicate")
            assert len(requests) == 1 and await h.tasks.get(task.id) == terminal
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_read_only_diagnosis_reports_admitted_successor_without_granting_replay(admission):
    h = admission
    original, terminal = await expired(h)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    report = await h.tasks.diagnose_framework_message(original.id, session_id=h.session_id)
    assert report.stage.execution_task_id == successor.id and report.stage.recovery_count == 1
    assert "stage_recovery_requires_admission" in report.issues
    assert not report.automatic_replay_allowed and await h.tasks.get(original.id) == terminal


@pytest.mark.asyncio
async def test_v1_stage_is_read_compatibly_and_recovery_explicitly_upgrades_its_record(admission):
    h = admission
    original, _ = await expired(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=jsonb_set(record-'recoveries','{{schema_version}}','1') "
            "WHERE task_id=%s", (original.id,))
    assert (await stage(h, original)).schema_version == 1
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    saved = await stage(h, original)
    assert saved.schema_version == 3 and saved.current_task_id == successor.id


@pytest.mark.asyncio
async def test_stage_read_requires_successor_reference_to_its_scoped_origin(admission):
    h = admission
    original, _ = await expired(h)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    assert await stage(h, successor) == await stage(h, original)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET reference_id=%s WHERE id=%s", (successor.id, successor.id))
    assert await stage(h, successor) is None
    assert (await stage(h, original)).current_task_id == successor.id


@pytest.mark.asyncio
async def test_recovery_accepts_dispatchers_trimmed_display_message_without_changing_raw_input(admission):
    h = admission
    h.payload = h.payload.model_copy(update={"content": "  v2 persistence \n"})
    original, terminal = await expired(h)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    assert successor is not None and successor.input["content"] == h.payload.content
    report = await h.tasks.diagnose_framework_message(original.id, session_id=h.session_id)
    assert report.input.user_message_matches and report.input.payload_hash_matches
    assert await h.tasks.get(original.id) == terminal

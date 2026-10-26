from __future__ import annotations

import asyncio
import json
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit


def worker_paused_in_message(settings_data, config_data, prefix, gate, window):
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.schemas.model_config import ModelConfigRecord
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.application.intent.semantic_intent_service import SemanticIntentService
    from src.registry.modes import ModeRegistry
    from tests.test_agent_task_dispatch import build_services, Runtime

    async def work():
        settings = Settings.model_validate(settings_data)
        config = ModelConfigRecord.model_validate(config_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
        await resources.connect()
        _, tasks, sessions, _ = build_services(settings, Runtime(), resources)
        calls = PostgresFrameworkModelCalls(settings)

        class Registry:
            def get_runtime_config(self, key):
                assert key == config.key
                return config

        models = ModelRuntimeService(Registry(), settings)
        models.set_framework_model_call_policy(FrameworkModelCallPolicy(calls=calls,
            model_config_resolver=models.get_model_config))
        sessions._input_orchestrator_service = InputOrchestratorService(ModeRegistry(),
            SemanticIntentService(models, deterministic_confidence_threshold=1.1))
        original_start = calls.start

        async def pause(*args, **kwargs):
            if window == "after_start":
                await original_start(*args, **kwargs)
            await redis.rpush(gate, "paused")
            await asyncio.Event().wait()

        if window in {"before_start", "after_start"}:
            calls.start = pause
        elif window == "after_orchestration":
            from src.runtime.framework_message_stages import FrameworkMessageStages

            FrameworkMessageStages.submit_runtime = pause
        else:
            calls.complete = pause
        dispatcher = AgentTaskDispatcher(settings, tasks, sessions, resources, framework_model_calls=calls,
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
            group="message-crash", client=redis)
        await queue.connect()

        async def handle(item):
            await dispatcher.handle_v2(item, worker_id="message-origin")

        await RedisTaskWorker(queue, consumer="message-origin", handler=handle, block_ms=0).run_once()

    asyncio.run(work())


async def selected(h):
    task = await submit(h)
    claimed = await h.tasks.claim(task.id, worker_id="stage-owner", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="stage-fixture", fencing_token=1)
    return claimed


async def run(h, task):
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        return await h.sessions.execute_claimed_message(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


async def stage(h, task):
    return await h.tasks.get_framework_message_stage(task.id, session_id=h.session_id)


@pytest.mark.asyncio
async def test_stage_admission_is_committed_with_original_input_and_outbox(admission):
    h = admission
    task = await submit(h)
    saved = await stage(h, task)
    assert saved.task_id == task.id and saved.binding_id == h.binding.binding_id
    assert saved.phase == "submitted" and saved.user_message_id == h.message.id
    assert saved.input_sha256 and saved.source_manifest is None and saved.execution_request is None
    assert (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
async def test_orchestration_sources_are_frozen_before_any_enrichment_and_cannot_reenter(admission, monkeypatch):
    h = admission
    task = await selected(h)
    observed = []

    async def crash(session, payload, **kwargs):
        saved = await stage(h, task)
        assert saved.phase == "orchestrating" and saved.owner_attempt_id == task.attempt_id
        assert saved.source_manifest["binding_fingerprint"] == h.binding.fingerprint
        assert saved.source_manifest["message_sources"][0]["message_id"] == h.message.id
        assert task.lease_token not in saved.model_dump_json()
        observed.append(saved)
        raise ConnectionError("injected failure after input admission")

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", crash)
    with pytest.raises(ConnectionError):
        await run(h, task)
    with pytest.raises(RuntimeError, match="stage"):
        await run(h, task)
    assert len(observed) == 1 and h.runtime.calls == []
    assert await stage(h, task) == observed[0]


@pytest.mark.asyncio
async def test_execution_request_is_frozen_before_runtime_submission(admission, monkeypatch):
    h = admission
    task = await selected(h)
    execute = h.runtime.execute_turn

    async def check(session, request, **kwargs):
        saved = await stage(h, task)
        assert saved.phase == "runtime_submitted"
        assert saved.execution_request == request.model_dump(mode="json")
        return await execute(session, request, **kwargs)

    monkeypatch.setattr(h.runtime, "execute_turn", check)
    result = await run(h, task)
    saved = await stage(h, task)
    assert saved.phase == "projected" and saved.result_reference == result.output.id
    assert saved.execution_request["runtime_binding"] == h.binding.model_dump(mode="json")
    assert len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_historical_task_without_admission_is_not_reclassified_as_unsent(admission):
    h = admission
    task = await selected(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.database.postgres_agent_task_table}_message_stage WHERE task_id=%s",
            (task.id,))
    with pytest.raises(RuntimeError, match="stage"):
        await run(h, task)
    assert h.runtime.calls == [] and await stage(h, task) is None


@pytest.mark.asyncio
async def test_stage_rejects_admitted_input_drift_before_orchestration(admission):
    h = admission
    task = await selected(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=jsonb_set(input_payload, '{{content}}', %s::jsonb) WHERE id=%s",
            (json.dumps("changed after admission"), task.id))
    with pytest.raises(RuntimeError, match="input"):
        await run(h, task)
    assert h.runtime.calls == [] and (await stage(h, task)).phase == "submitted"


@pytest.mark.asyncio
async def test_runtime_uses_frozen_request_when_original_object_changes(admission, monkeypatch):
    from src.runtime.framework_message_stages import FrameworkMessageStages

    h = admission
    task = await selected(h)
    freeze = FrameworkMessageStages.freeze_execution
    execute = h.runtime.execute_turn

    async def mutate_after_freeze(self, owner, request):
        saved = await freeze(self, owner, request)
        request.normalized_input = "changed after freeze"
        request.context["injected_after_freeze"] = True
        return saved

    async def check(session, request, **kwargs):
        saved = await stage(h, task)
        assert request.model_dump(mode="json") == saved.execution_request
        return await execute(session, request, **kwargs)

    monkeypatch.setattr(FrameworkMessageStages, "freeze_execution", mutate_after_freeze)
    monkeypatch.setattr(h.runtime, "execute_turn", check)
    await run(h, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_manifest", "execution_request"])
async def test_corrupt_frozen_stage_cannot_advance_or_appear_valid_in_diagnosis(admission, monkeypatch, field):
    from src.runtime.framework_message_stages import FrameworkMessageStages

    h = admission
    task = await selected(h)
    submit_runtime = FrameworkMessageStages.submit_runtime

    async def corrupt_before_submit(self, owner):
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {self._table} SET record=jsonb_set(record, %s, %s::jsonb) WHERE task_id=%s",
                ([field], json.dumps({"corrupt": True}), task.id))
        return await submit_runtime(self, owner)

    monkeypatch.setattr(FrameworkMessageStages, "submit_runtime", corrupt_before_submit)
    with pytest.raises(RuntimeError, match="integrity"):
        await run(h, task)
    assert h.runtime.calls == []
    report = await h.tasks.diagnose_framework_message(task.id, session_id=h.session_id)
    assert report.stage is None and "stage_admission_invalid" in report.issues
    assert not report.automatic_replay_allowed


@pytest.mark.asyncio
async def test_stage_admission_failure_rolls_back_entire_submission(admission, monkeypatch):
    from src.runtime import framework_message_stages
    from tests.test_framework_model_admission import counts

    h = admission
    admit = framework_message_stages.admit_message_stage

    def fail(*args):
        admit(*args)
        raise ConnectionError("injected stage admission failure")

    with monkeypatch.context() as patch:
        patch.setattr(framework_message_stages, "admit_message_stage", fail)
        with pytest.raises(ConnectionError, match="injected"):
            await submit(h)
    assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {framework_message_stages.message_stage_table(h.settings)}")
        assert cur.fetchone()["n"] == 0
    assert (await stage(h, await submit(h))).phase == "submitted"


@pytest.mark.asyncio
async def test_expired_owner_cannot_advance_original_stage(admission):
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
    from src.runtime.store import ContinuationLeaseLostError

    h = admission
    task = await selected(h)
    stages = FrameworkMessageStages(h.tasks)
    session = await h.store.get_session(h.session_id)
    saved = await stages.begin(task.runtime_owner, message_source_manifest(session, h.binding, h.payload))
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await stages.submit_runtime(task.runtime_owner)
    assert await stage(h, task) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("window,http_count,phase", [("before_start", 0, "ready"),
    ("after_start", 0, "invoking"), ("after_response", 1, "invoking")])
async def test_independent_worker_kill_and_redis_redelivery_keep_original_admission(admission, window, http_count, phase):
    from redis.asyncio import Redis
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.application.models.framework_model_call_policy import model_config_fingerprint
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    h.settings.orchestration.agent_task_v2_stream = h.prefix + ":message-crash"
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="message-crash", client=h.redis)
    await queue.connect()
    gate = h.prefix + ":message-crash:paused"
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=40)
    with _provider("openai_chat_completions") as (url, requests):
        config = _config("openai_chat_completions", url)
        h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
        h.command = make_command(h, h.binding)
        h.payload = h.payload.model_copy(update={"model_key": config.key})
        h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
        task = await submit(h)
        assert await h.outbox.relay_once(queue_for_stream=lambda _: queue) == 1
        process = multiprocessing.get_context("spawn").Process(target=worker_paused_in_message,
            args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate, window))
        process.start()
        try:
            assert await waiting.blpop(gate, timeout=35) is not None
            original = await h.tasks.get(task.id)
            before = await h.calls.snapshot(h.binding)
            frozen_stage = await stage(h, task)
            assert frozen_stage.phase == "orchestrating"
            semantic_input = frozen_stage.source_manifest["semantic_input"]
            assert semantic_input["request"]["system_prompt"]
            assert semantic_input["request"]["trace_context"]["logical_call_id"] == "input_orchestration:semantic_intent:v1"
            assert semantic_input["invocation_parameters"]["transport"] == "openai_chat_completions"
            assert semantic_input["transport_options"]["max_retries"] == 0
            assert before.calls[0].phase == phase and len(requests) == http_count
            assert process.is_alive()
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
            dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources, framework_model_calls=h.calls,
                framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))

            async def handle(item):
                await dispatcher.handle_v2(item, worker_id="message-replacement")

            worker = RedisTaskWorker(queue, consumer="message-replacement", handler=handle, block_ms=0, reclaim_idle_ms=1)
            assert await worker.run_once() == 1
            final = await h.tasks.get(task.id)
            assert final.status == "recovery_required" and final.attempt_id == original.attempt_id
            assert final.attempt_no == original.attempt_no and h.runtime.calls == []
            assert len(requests) == http_count and await h.calls.snapshot(h.binding) == before
            assert await stage(h, task) == frozen_stage
        finally:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 10)
            await waiting.aclose()

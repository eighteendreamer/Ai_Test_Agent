from __future__ import annotations

import asyncio
import json
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_message_stages import FrameworkMessageStages
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.infrastructure.redis_task_queue import QueuedTask
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_message_stages import selected, stage, run, worker_paused_in_message
from tests.test_framework_message_recovery import isolate_recovery_stores, outbox


async def frozen_before_runtime(h, monkeypatch):
    task = await selected(h)

    async def crash(*args):
        raise ConnectionError("injected stop after orchestration freeze")

    with monkeypatch.context() as patch:
        patch.setattr(FrameworkMessageStages, "submit_runtime", crash)
        with pytest.raises(ConnectionError, match="after orchestration"):
            await run(h, task)
    saved = await stage(h, task)
    assert saved.phase == "orchestrated" and h.runtime.calls == []
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    assert (await h.tasks.claim(task.id, worker_id="expiry", expected_binding=h.binding)).status == "recovery_required"
    return task, await h.tasks.get(task.id), saved


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_version", [1, 2])
async def test_frozen_execution_is_resumed_without_repeating_input_orchestration(admission, monkeypatch, budget_version):
    h = admission
    if budget_version == 2:
        from tests.test_framework_model_admission import snapshot_budget

        snapshot_budget(h)
        h.payload = h.payload.model_copy(update={"model_key": "auto"})
    original, terminal, frozen = await frozen_before_runtime(h, monkeypatch)
    if budget_version == 2:
        assert original.input["model_key"] == "auto" and frozen.execution_request["model_key"] == "primary"
    before = await h.calls.snapshot(h.binding)
    successors = await asyncio.gather(*(h.tasks.ensure_framework_message_recovery(original.id) for _ in range(4)))
    assert all(task is not None for task in successors)
    assert len({task.id for task in successors}) == 1
    successor = successors[0]
    link = (await stage(h, original)).recoveries[-1]
    assert link.resume_phase == "orchestrated" and link.execution_sha256 == frozen.execution_sha256

    async def never_reorchestrate(*args, **kwargs):
        raise AssertionError("frozen orchestration must not run again")

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", never_reorchestrate)
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    monkeypatch.setattr(PostgresFrameworkModelCalls, "frozen_policy", never_reorchestrate)
    execute = h.runtime.execute_turn

    async def require_frozen(session, request, **kwargs):
        assert request.model_dump(mode="json") == frozen.execution_request
        return await execute(session, request, **kwargs)

    monkeypatch.setattr(h.runtime, "execute_turn", require_frozen)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("resume-frozen", outbox(h, successor.id)["payload"]), worker_id="recovery")
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert await h.tasks.get(original.id) == terminal
    assert len(h.runtime.calls) == 1 and (await stage(h, original)).phase == "projected"
    assert await h.calls.snapshot(h.binding) == before
    await h.dispatcher.handle_v2(QueuedTask("duplicate", outbox(h, successor.id)["payload"]), worker_id="duplicate")
    assert len(h.runtime.calls) == 1 and len((await h.store.get_session(h.session_id)).messages) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["context", "message", "input", "missing_sources", "execution_identity", "ready", "unknown"])
async def test_frozen_phase_alone_does_not_override_changed_or_unknown_facts(admission, monkeypatch, changed):
    from src.schemas.framework_approval import approval_value_hash
    from tests.test_framework_model_calls import intent

    h = admission
    original, terminal, frozen = await frozen_before_runtime(h, monkeypatch)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "context":
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                "'{\"environment\":\"changed\"}'::jsonb WHERE id=%s", (h.session_id,))
        elif changed == "message":
            cur.execute(f"UPDATE {h.settings.database.postgres_message_table} SET content='changed' WHERE id=%s", (original.user_message_id,))
        elif changed == "input":
            cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=input_payload || %s::jsonb WHERE id=%s",
                (json.dumps({"content": "changed"}), original.id))
        elif changed in {"missing_sources", "execution_identity"}:
            record = frozen.model_dump(mode="json")
            if changed == "missing_sources":
                record["source_manifest"].pop("runtime_source_sha256", None)
                record["source_sha256"] = approval_value_hash(record["source_manifest"])
            else:
                record["execution_request"]["turn_id"] = "another-turn"
                record["execution_sha256"] = approval_value_hash(record["execution_request"])
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s", (json.dumps(record), original.id))
        else:
            from src.schemas.framework_model_call import FrameworkModelCallRecord
            from datetime import datetime, timezone

            record = FrameworkModelCallRecord(binding_id=h.binding.binding_id,
                intent=intent(position_version=1), owner_epoch=original.runtime_owner.owner_epoch,
                owner_task_id=original.id, owner_attempt_id=original.attempt_id,
                phase=changed, created_at=datetime.now(timezone.utc))
            h.calls._write(cur, record, insert=True)
    # Deliberate input corruption is already part of the persisted baseline;
    # recovery must not change that row or reset its terminal execution facts.
    before = await h.tasks.get(original.id)
    assert before.status == terminal.status and before.attempt_id == terminal.attempt_id
    assert await h.tasks.ensure_framework_message_recovery(original.id) is None
    assert await h.tasks.get(original.id) == before and len(await h.tasks.list_session(h.session_id)) == 1
    assert h.runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_claim", "before_submit"])
async def test_frozen_source_is_rechecked_after_publication_and_before_runtime(admission, monkeypatch, window):
    h = admission
    original, terminal, _ = await frozen_before_runtime(h, monkeypatch)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    assert successor is not None

    def change():
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                "'{\"environment\":\"changed\"}'::jsonb WHERE id=%s", (h.session_id,))

    if window == "before_claim":
        change()
        rejected = await h.tasks.claim(successor.id, worker_id="recovery", expected_binding=h.binding)
        assert rejected.status == "recovery_required" and rejected.attempt_id is None
    else:
        submit_runtime = FrameworkMessageStages.submit_runtime

        async def change_before_submit(self, owner):
            change()
            return await submit_runtime(self, owner)

        monkeypatch.setattr(FrameworkMessageStages, "submit_runtime", change_before_submit)
        current = await h.tasks.claim(successor.id, worker_id="recovery", expected_binding=h.binding)
        await h.tasks.begin(current, resource_id="recovery", fencing_token=2)
        with pytest.raises(RuntimeError, match="source"):
            await run(h, current)
    assert h.runtime.calls == [] and await h.tasks.get(original.id) == terminal


@pytest.mark.asyncio
async def test_orchestrated_diagnosis_requires_admission_without_granting_replay(admission, monkeypatch):
    h = admission
    original, terminal, _ = await frozen_before_runtime(h, monkeypatch)
    report = await h.tasks.diagnose_framework_message(original.id, session_id=h.session_id)
    assert report.stage.phase == "orchestrated"
    assert "stage_recovery_requires_admission" in report.issues
    assert not report.automatic_replay_allowed and await h.tasks.get(original.id) == terminal


@pytest.mark.asyncio
async def test_mode_registry_drift_is_rejected_before_frozen_request_is_submitted(admission, monkeypatch):
    h = admission
    original, terminal, _ = await frozen_before_runtime(h, monkeypatch)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    current = await h.tasks.claim(successor.id, worker_id="registry-drift", expected_binding=h.binding)
    await h.tasks.begin(current, resource_id="registry-drift", fencing_token=2)
    monkeypatch.setattr(h.sessions._mode_registry, "list", lambda: [])
    with pytest.raises(RuntimeError, match="mode sources"):
        await run(h, current)
    assert h.runtime.calls == [] and await h.tasks.get(original.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["binding_id", "attempt_id"])
async def test_corrupt_completed_model_identity_cannot_authorize_frozen_recovery(admission, monkeypatch, field):
    from datetime import datetime, timezone
    from src.schemas.framework_model_call import FrameworkModelCallRecord
    from tests.test_framework_model_calls import intent

    h = admission
    original, terminal, _ = await frozen_before_runtime(h, monkeypatch)
    record = FrameworkModelCallRecord(binding_id=h.binding.binding_id, intent=intent(position_version=1),
        owner_epoch=original.runtime_owner.owner_epoch, owner_task_id=original.id,
        owner_attempt_id=original.attempt_id, phase="completed", created_at=datetime.now(timezone.utc))
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        h.calls._write(cur, record, insert=True)
        path = ["binding_id"] if field == "binding_id" else ["intent", "attempt_id"]
        cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,%s,%s::jsonb) WHERE binding_id=%s",
            (path, json.dumps("foreign-identity"), h.binding.binding_id))
    assert await h.tasks.ensure_framework_message_recovery(original.id) is None
    assert await h.tasks.get(original.id) == terminal and len(await h.tasks.list_session(h.session_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["execution", "models", "proof_version"])
async def test_claim_rejects_changed_frozen_proof_without_creating_an_attempt(admission, monkeypatch, changed):
    from datetime import datetime, timezone
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_model_call import FrameworkModelCallRecord
    from tests.test_framework_model_calls import intent

    h = admission
    original, terminal, _ = await frozen_before_runtime(h, monkeypatch)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    record = (await stage(h, original)).model_dump(mode="json")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "models":
            call = FrameworkModelCallRecord(binding_id=h.binding.binding_id, intent=intent(position_version=1),
                owner_epoch=original.runtime_owner.owner_epoch, owner_task_id=original.id,
                owner_attempt_id=original.attempt_id, phase="completed", created_at=datetime.now(timezone.utc))
            h.calls._write(cur, call, insert=True)
        else:
            if changed == "execution":
                record["execution_request"]["normalized_input"] = "changed after publication"
                record["execution_sha256"] = approval_value_hash(record["execution_request"])
            else:
                record["schema_version"] = 2
            cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s",
                (json.dumps(record), original.id))
    rejected = await h.tasks.claim(successor.id, worker_id="changed-proof", expected_binding=h.binding)
    assert rejected.status == "recovery_required" and rejected.attempt_id is None
    assert rejected.error_code == "framework_message_recovery_changed" and h.runtime.calls == []
    assert await h.tasks.get(original.id) == terminal


@pytest.mark.asyncio
async def test_resumed_frozen_phase_can_lose_its_owner_again_without_reorchestration(admission, monkeypatch):
    from src.runtime.store import ContinuationLeaseLostError

    h = admission
    original, terminal, frozen = await frozen_before_runtime(h, monkeypatch)
    first = await h.tasks.ensure_framework_message_recovery(original.id)
    first = await h.tasks.claim(first.id, worker_id="first-recovery", expected_binding=h.binding)
    await h.tasks.begin(first, resource_id="first-recovery", fencing_token=2)
    stages = FrameworkMessageStages(h.tasks)
    await stages.resume_orchestrated(first.runtime_owner, modes=h.sessions._mode_registry.list())
    assert await h.store.renew_turn_execution(h.session_id, first.lease_token, 0)
    await h.tasks.claim(first.id, worker_id="expiry", expected_binding=h.binding)
    first_terminal = await h.tasks.get(first.id)
    second = await h.tasks.ensure_framework_message_recovery(first.id)
    assert second is not None and len((await stage(h, original)).recoveries) == 2
    second = await h.tasks.claim(second.id, worker_id="second-recovery", expected_binding=h.binding)
    await h.tasks.begin(second, resource_id="second-recovery", fencing_token=3)
    with pytest.raises(ContinuationLeaseLostError):
        await stages.submit_runtime(first.runtime_owner)

    async def never_reorchestrate(*args, **kwargs):
        raise AssertionError("second recovery must use the frozen request")

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", never_reorchestrate)
    await run(h, second)
    assert (await stage(h, original)).execution_request == frozen.execution_request
    assert len(h.runtime.calls) == 1 and await h.tasks.get(original.id) == terminal
    assert await h.tasks.get(first.id) == first_terminal


@pytest.mark.asyncio
async def test_killed_worker_after_semantic_completion_recovers_frozen_input_through_official_graph(admission, monkeypatch):
    from redis.asyncio import Redis
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.application.runtime.runtime_service import RuntimeService
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.redis_task_worker import RedisTaskWorker
    from tests.test_framework_chat_models import _config, _provider, _reply
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    h.settings.orchestration.resource_default_lease_seconds = 10
    provider = DeepAgentCheckpointProvider(h.settings)
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=40)
    process = None
    semantic = _reply("openai_chat_completions")
    semantic["choices"][0]["message"]["content"] = json.dumps({"target_kind": "code",
        "objectives": ["code_review"], "requested_actions": ["read"], "required_capabilities": ["code.review"],
        "candidate_mode_key": "code_review", "confidence": 0.99, "evidence": ["frozen semantic fixture"]})
    try:
        with _provider("openai_chat_completions", first_reply=semantic) as (url, requests):
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
                group="message-crash", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":after-orchestration"
            process = multiprocessing.get_context("spawn").Process(target=worker_paused_in_message,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate, "after_orchestration"))
            process.start()
            assert await waiting.blpop(gate, timeout=35) is not None
            original = await h.tasks.get(task.id)
            frozen = await stage(h, task)
            before = await h.calls.snapshot(h.binding)
            assert original.status == "running" and frozen.phase == "orchestrated" and len(requests) == 1
            assert before.reserved_calls == 1 and before.calls[0].phase == "completed" and before.actual_tokens == 10
            assert "semantic_classifier:" in json.dumps(frozen.execution_request["context"]["intent_decision"])
            assert process.is_alive()
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
            # Use the actual TTL: cold SDK setup can block this fixture's event
            # loop longer than a one-second slot, before renewals can run.
            deadline = asyncio.get_running_loop().time() + 30
            while await h.resources.list_active() and asyncio.get_running_loop().time() < deadline:
                await h.resources.reap_expired()
                if await h.resources.list_active():
                    await asyncio.sleep(.25)
            assert not await h.resources.list_active(), "Killed worker resource lease did not expire"
            h.settings.orchestration.resource_default_lease_seconds = 300

            async def never_reorchestrate(*args, **kwargs):
                raise AssertionError("completed semantic input must not be recomputed")

            monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", never_reorchestrate)
            dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources, framework_model_calls=h.calls,
                framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
            errors = []

            async def handle(item):
                try:
                    await dispatcher.handle_v2(item, worker_id="frozen-replacement")
                except Exception as exc:
                    errors.append(exc)
                    raise

            worker = RedisTaskWorker(queue, consumer="frozen-replacement", handler=handle, block_ms=0, reclaim_idle_ms=1)
            processed = await worker.run_once()
            if errors:
                raise errors[0]
            assert processed == 1
            terminal = await h.tasks.get(task.id)
            assert terminal.status == "recovery_required" and terminal.attempt_id == original.attempt_id
            successor = await h.tasks.ensure_framework_message_recovery(task.id)
            assert successor is not None and len(requests) == 1
            assert await h.calls.snapshot(h.binding) == before
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            processed = await worker.run_once()
            if errors:
                raise errors[0]
            assert processed == 1
            assert (await h.tasks.get(successor.id)).status == "completed"
            final = await h.calls.snapshot(h.binding)
            assert final.policy == h.budget and final.reserved_calls == 2 and final.actual_tokens == 20
            assert final.calls[0] == before.calls[0] and final.calls[1].owner_task_id == successor.id
            assert len(requests) == 2 and (await stage(h, task)).execution_request == frozen.execution_request
            await dispatcher.handle_v2(QueuedTask("duplicate", outbox(h, successor.id)["payload"]), worker_id="duplicate")
            assert len(requests) == 2 and await h.tasks.get(task.id) == terminal
            assert len((await h.store.get_session(h.session_id)).messages) == 2
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")

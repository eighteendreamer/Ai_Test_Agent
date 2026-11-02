"""Real process-loss windows around formal settlement, not production routing.

The execution child uses the explicit controlled service ports. There is no
production Dispatcher/resource-heartbeat migration hidden in this harness.
"""
import asyncio
from dataclasses import replace
import json
import multiprocessing
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.model_config import ModelInvocationParameters
from src.schemas.runtime_binding import RuntimeBinding
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, submit
from tests.test_controlled_case_assessment import terminal_reply
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_runtime import assembly, prepare
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_framework_chat_models import _provider, _config
from tests.test_live_postgres_concurrency import _drop_tables


def execution_worker(settings_data, config_data, raw, gate, window):
    """Only this disposable process ever holds the real live execution owner."""
    from redis import Redis as SyncRedis
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.schemas.model_config import ModelConfigRecord
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter
    from src.application.runtime.controlled_case_executor import ControlledCaseExecutor
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
    from src.runtime.controlled_case_assessment import ControlledCaseAssessments
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from tests.test_agent_task_dispatch import build_services
    from tests.test_controlled_case_graph import business_bridge

    async def work():
        settings = Settings.model_validate(settings_data)
        config = ModelConfigRecord.model_validate(config_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        store, tasks, sessions, _ = build_services(settings, None, None)
        task = await tasks.claim_controlled_case(raw, worker_id="isolated-case-kill",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True))
        models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), settings)
        provider = DeepAgentCheckpointProvider(settings)
        adapter = ControlledCaseGraphAdapter(model_resolver=models.resolve_langchain_chat_model,
            checkpointer_factory=provider.open, model_call_policy=FrameworkModelCallPolicy(
                calls=PostgresFrameworkModelCalls(settings), model_config_resolver=models.get_model_config))
        assessments, settlements = ControlledCaseAssessments(tasks), ControlledCaseSettlements(tasks)

        async def pause():
            await redis.rpush(gate, window)
            await asyncio.Event().wait()

        if window == "after_assessment":
            freeze = assessments.freeze
            async def freeze_then_pause(*args, **kwargs):
                await freeze(*args, **kwargs)
                await pause()
            assessments.freeze = freeze_then_pause
        elif window == "inside_settlement":
            append = tasks._sessions.append_event_in_transaction
            def audit_then_pause(cur, session_id, event, *args, **kwargs):
                inserted = append(cur, session_id, event, *args, **kwargs)
                if event.type == "test_run.case_settled":
                    with SyncRedis.from_url(settings.database.redis_url, decode_responses=True) as sync:
                        sync.rpush(gate, window)
                        assert sync.blpop(gate + ":release", timeout=60), "Uncommitted settlement gate timed out"
                return inserted
            tasks._sessions.append_event_in_transaction = audit_then_pause
        elif window == "after_commit_before_ack":
            material = settlements._material
            def commit_then_pause(*args):
                original = material(*args)
                # _material has exited its connection context: the actual
                # transaction is committed, but complete has not received ACK.
                with SyncRedis.from_url(settings.database.redis_url, decode_responses=True) as sync:
                    sync.rpush(gate, window)
                    assert sync.blpop(gate + ":release", timeout=60), "Committed settlement gate timed out"
                return original
            settlements._material = commit_then_pause
        else:
            raise ValueError("Unknown isolated formal kill window")
        h = SimpleNamespace(settings=settings, models=models, store=store, session_id=raw["session_id"],
            binding=task.runtime_binding, task=task, owner=task)
        async with business_bridge(h, [], approval_required=False, table_names=(
                settings.database.postgres_tool_job_table, settings.database.postgres_tool_artifact_table)) as bridge:
            await ControlledCaseRuntime(runtime_service=bridge.runtime, session_service=sessions,
                assessment_service=assessments, settlement_service=settlements).execute(task,
                    executor=ControlledCaseExecutor(task_store=tasks, graph_adapter=adapter),
                    resource_id="isolated-case-process", fencing_token=1)
        raise AssertionError("Kill window was not reached")
    asyncio.run(work())


def ack_worker(settings_data, raw, gate):
    """Fresh interpreter receives settings+delivery only: no owner/native/model."""
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.schemas.runtime_binding import RuntimeExecutionOwner

    async def work():
        settings = Settings.model_validate(settings_data)
        tasks = PostgresAgentTaskStore(settings, PostgresSessionStore(settings))
        def forbidden(*args, **kwargs):
            raise AssertionError("Read-only terminal process cannot create execution authority or mutate")
        RuntimeExecutionOwner.__init__ = forbidden
        tasks.claim_controlled_case = tasks.begin = tasks._update_status = forbidden
        service = ControlledCaseSettlements(tasks)
        try:
            receipts = [await service.acknowledge_delivery(raw, capabilities=FrameworkWorkerCapabilities("a" * 64, False))
                for _ in range(2)]
            assert receipts[0] == receipts[1]
            result = {"receipt": receipts[0].model_dump(mode="json")}
        except (RuntimeError, ValueError) as exc:
            result = {"rejected": type(exc).__name__}
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        try:
            await redis.rpush(gate, json.dumps(result))
        finally:
            await redis.aclose()
    asyncio.run(work())


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["after_assessment", "inside_settlement", "after_commit_before_ack"])
async def test_hard_kill_never_invents_settlement_and_new_reader_only_acks_committed_original(formal_case, tmp_path, window):
    from redis.asyncio import Redis
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements

    s, process, reader = formal_case, None, None
    settings = s.h.settings
    waiting = Redis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65)
    suffix = uuid4().hex
    schema = settings.deep_agents.checkpoint_schema = "case_kill_" + suffix
    tables = ["case_kill_job_" + suffix, "case_kill_artifact_" + suffix]
    settings.database.postgres_tool_job_table, settings.database.postgres_tool_artifact_table = tables
    gate = s.h.prefix + ":formal-kill"
    # Deliberately empty checks cannot turn the model's 'passed' into a passed
    # business result. No handler executes and only one local HTTP call occurs.
    receipt = {"schema_version": 1, "steps": [{"item_key": step.item.key, "status": "passed",
        "summary": "synthetic empty checks", "checks": [], "outputs": {}} for step in s.asset.context.steps]}
    reply = terminal_reply("openai_chat_completions", json.dumps(receipt))
    try:
        with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
            config = _config("openai_chat_completions", url)
            snapshot = ModelInvocationParameters.from_configuration(config)
            s.binding = RuntimeBinding.model_validate({**s.binding.model_dump(exclude={"fingerprint"}),
                "model_config_fingerprint": snapshot.fingerprint,
                "model_provider": config.provider, "model_transport": config.transport})
            s.graph_request = replace(s.graph_request, model_key=config.key)
            models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), settings)
            s.asset = await prepare(s, assembly(s, tmp_path / "assembly", models))
            s.command = s.command.model_copy(update={"runtime_binding": s.binding, "case_execution_input": s.asset})
            s.budget = s.budget.model_copy(update={"max_tokens": 500_000,
                "model_configurations": {config.key: snapshot}, "model_config_fingerprints": {config.key: snapshot.fingerprint}})
            await submit(s)
            raw = delivery(s)
            ctx = multiprocessing.get_context("spawn")
            process = ctx.Process(target=execution_worker, args=(settings.model_dump(mode="json"),
                config.model_dump(mode="json"), raw, gate, window))
            process.start()
            reached = await waiting.blpop(gate, timeout=50)
            assert reached is not None, f"execution child exitcode={process.exitcode}"
            assert reached[1] == window
            process.kill()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode not in {None, 0}
            item = await s.d.runs.get_item(s.item.id)
            task = await s.h.tasks.get(raw["task_id"])
            attempt = await asyncio.to_thread(s.d.runs._get_latest_attempt_sync, s.item.id)
            checkpoint = attempt.checkpoint_payload
            assert checkpoint["assessment"]["completion"]["status"] == "blocked"
            assert len(requests) == 1
            committed = window == "after_commit_before_ack"
            assert attempt.checkpoint_version == (6 if committed else 5)
            assert task.status == ("completed" if committed else "running")
            assert item.result_id == (checkpoint["settlement"]["result_id"] if committed else None)
            assert item.status == ("blocked" if committed else "running")
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS n FROM {s.d.runs._result_table} WHERE run_item_id=%s", (item.id,))
                assert cur.fetchone()["n"] == int(committed)
                cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_event_table} WHERE session_id=%s AND type='test_run.case_settled'", (s.session.id,))
                assert cur.fetchone()["n"] == int(committed)
                for table in tables:
                    cur.execute(f"SELECT count(*) AS n FROM {table}")
                    assert cur.fetchone()["n"] == 0
            p = SimpleNamespace(s=s)
            before = originals_digest(p)
            reader = ctx.Process(target=ack_worker, args=(settings.model_dump(mode="json"), raw, gate + ":ack"))
            reader.start()
            observed = await waiting.blpop(gate + ":ack", timeout=30)
            assert observed is not None, f"read-only child exitcode={reader.exitcode}"
            await asyncio.to_thread(reader.join, 10)
            assert not reader.is_alive() and reader.exitcode == 0
            response = json.loads(observed[1])
            if committed:
                assert response == {"receipt": checkpoint["settlement"]}
                original = await ControlledCaseSettlements(s.h.tasks).acknowledge_delivery(raw,
                    capabilities=FrameworkWorkerCapabilities("a" * 64, True))
                assert original.model_dump(mode="json") == checkpoint["settlement"]
            else:
                assert response["rejected"] in {"FrameworkMessageStageRecoveryRequired", "ValueError"}
            assert originals_digest(p) == before and len(requests) == 1
    finally:
        for child in (process, reader):
            if child is not None and child.is_alive():
                child.kill()
                await asyncio.to_thread(child.join, 10)
                assert not child.is_alive()
        await waiting.aclose()
        _drop_tables(settings, list(reversed(tables)))
        # This unique schema is owned solely by this test and was supplied to
        # the disposable child. Never delete shared/default checkpoint data.
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

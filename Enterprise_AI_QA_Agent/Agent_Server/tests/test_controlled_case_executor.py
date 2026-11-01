"""Formal PG task -> official graph; local HTTP/tools are synthetic, not verdicts."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import importlib
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter
from src.application.test_runs.controlled_case_preparation import prepare_controlled_case_input
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import current_framework_owner
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from src.schemas.model_config import ModelInvocationParameters
from src.schemas.runtime_binding import RuntimeBinding
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import (
    formal_case, pg_domain, prepare_domain, submit, database_digest, wait_for_session_sql_lock,
)
from tests.test_controlled_case_execution_lease import claim
from tests.test_controlled_case_graph import business_bridge
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply


def implementation():
    return importlib.import_module("src.application.runtime.controlled_case_executor")


@asynccontextmanager
async def execution(s, config, *, tools=(), gating=None, prepare_input=None):
    """Freeze local configuration BEFORE real admission, never edit admitted originals."""
    snapshot = ModelInvocationParameters.from_configuration(config)
    s.binding = RuntimeBinding.model_validate({**s.binding.model_dump(exclude={"fingerprint"}),
        "model_config_fingerprint": snapshot.fingerprint,
        "model_provider": config.provider, "model_transport": config.transport})
    s.graph_request = replace(s.graph_request, model_key=config.key,
        business_tools=list(tools), interrupt_on=gating or {})
    models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), s.h.settings)
    s.asset = (await prepare_input(models) if prepare_input is not None else prepare_controlled_case_input(
        detail=s.detail, item=s.item, version=s.version,
        attempt_id=s.attempt.id, binding=s.binding, graph_request=s.graph_request))
    s.command = s.command.model_copy(update={"runtime_binding": s.binding, "case_execution_input": s.asset})
    s.budget = s.budget.model_copy(update={"max_tokens": 500_000,
        "model_configurations": {config.key: snapshot},
        "model_config_fingerprints": {config.key: snapshot.fingerprint}})
    await submit(s)
    task = await claim(s)
    policy = FrameworkModelCallPolicy(calls=s.calls, model_config_resolver=models.get_model_config)
    schema = f"formal_case_graph_{uuid4().hex}"
    s.h.settings.deep_agents.checkpoint_schema = schema
    provider = DeepAgentCheckpointProvider(s.h.settings)
    adapter = ControlledCaseGraphAdapter(model_resolver=models.resolve_langchain_chat_model,
        checkpointer_factory=provider.open, model_call_policy=policy)
    try:
        yield SimpleNamespace(task=task, adapter=adapter, provider=provider, models=models,
            policy=policy, calls=s.calls, binding=s.binding, settings=s.h.settings,
            store=s.h.store, session_id=s.session.id, owner=task)
    finally:
        await provider.close()
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def executor(s, h):
    return implementation().ControlledCaseExecutor(task_store=s.h.tasks, graph_adapter=h.adapter)


async def no_tools(prepared):
    return []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_formal_task_executes_official_graph_without_finalizing_the_case(formal_case, transport):
    s = formal_case
    with _provider(transport) as (url, requests):
        async with execution(s, _config(transport, url)) as h:
            outcome = await executor(s, h).execute(h.task, build_business_tools=no_tools,
                resource_id="isolated-formal-graph", fencing_token=1)
            assert outcome.values["messages"][-1].text == "fixture answer"
            assert outcome.native_terminal is not None and outcome.native_interrupts == []
            assert outcome.native_terminal.proof.binding_fingerprint == s.binding.fingerprint
            ledger = await s.calls.snapshot(s.binding)
            assert ledger.reserved_calls == 1 and ledger.calls[0].phase == "completed"
            assert ledger.calls[0].owner_attempt_id == h.task.attempt_id != s.attempt.id
            assert len(requests) == 1 and s.graph_request.system_prompt in str(requests[0]["body"])
            assert (await s.h.tasks.get(h.task.id)).status == "running"
            assert await s.d.runs.get_item(s.item.id) == s.item
            assert current_framework_owner() is None
            with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                for table in (s.h.settings.database.postgres_message_table,
                    s.h.settings.database.postgres_snapshot_table, f"{s.h.tasks._tasks}_message_stage",
                    s.d.runs._result_table):
                    cur.execute(f"SELECT count(*) AS n FROM {table}")
                    assert cur.fetchone()["n"] == 0
            before = database_digest(s)
            with pytest.raises((ValueError, RuntimeError)):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert database_digest(s) == before and len(requests) == 1


@pytest.mark.asyncio
async def test_case_begin_rejects_durable_input_drift_after_claim(formal_case):
    s = formal_case
    await submit(s)
    task = await claim(s)
    changed = s.asset.model_dump(mode="json")
    changed["context"]["objective"] += " changed after claim"
    changed["asset_sha256"] = approval_value_hash({k: v for k, v in changed.items() if k != "asset_sha256"})
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {s.h.tasks._tasks} SET input_payload=%s::jsonb WHERE id=%s",
            (json.dumps({"case_execution_input": changed}), task.id))
    before = database_digest(s)
    with pytest.raises(ValueError, match="original|input"):
        await s.h.tasks.begin(task, resource_id="isolated-formal-graph", fencing_token=1)
    assert database_digest(s) == before


def frozen_tool_schema():
    from src.application.model_adapters.tool_adapter import LangChainToolAdapter
    from src.schemas.agent import ToolDescriptor

    async def forbidden(*args):
        pytest.fail("schema placeholder must never execute")

    return LangChainToolAdapter().to_governed_tools([ToolDescriptor(key="inspect_source", name="Inspect source",
        category="execution", description="Read only the fixture source", permission_level="safe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})],
        invoke=forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_formal_graph_tool_backedge_persists_original_job_and_langgraph_audit(formal_case, transport):
    s = formal_case
    reply = _tool_reply(transport)
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        async with execution(s, _config(transport, url), tools=frozen_tool_schema()) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                async def build(prepared):
                    assert prepared.asset_sha256 == s.asset.asset_sha256
                    tools, _ = await bridge.runtime._build_deep_agent_business_tools(
                        state=bridge.state, tool_records=bridge.records)
                    return tools
                outcome = await executor(s, h).execute(h.task, build_business_tools=build,
                    resource_id="isolated-formal-graph", fencing_token=1)
                assert outcome.native_terminal is not None and len(requests) == 2 and len(effects) == 1
                job, = await bridge.jobs.list_jobs(session_id=s.session.id)
                assert job.status.value == "completed" and job.id == effects[0][2]
                from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY

                material = FrameworkToolProjection.model_validate(outcome.values["messages"][2].artifact[TOOL_PROJECTION_KEY])
                material.require_binding(s.binding, material.source)
                assert material.record.job_id == job.id
                events = [event for event in await s.h.store.list_events(s.session.id)
                    if event.type.startswith("tool.execution_")]
                assert [event.type for event in events] == ["tool.execution_requested", "tool.execution_completed"]
                assert all(event.payload["harness"] == "langgraph"
                    and event.payload["phase"] == "langgraph.tool_governance" for event in events)
                assert [event.id for event in events] == [event["id"] for event in bridge.state["event_log"]]
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None


@pytest.mark.asyncio
async def test_concurrent_executors_commit_one_running_intent_and_invoke_once(formal_case):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            runner = executor(s, h)
            results = await asyncio.gather(*(runner.execute(h.task, build_business_tools=no_tools,
                resource_id="isolated-formal-graph", fencing_token=1) for _ in range(2)), return_exceptions=True)
            assert sum(isinstance(result, (ValueError, ContinuationLeaseLostError)) for result in results) == 1
            assert sum(getattr(result, "native_terminal", None) is not None for result in results) == 1
            events = await s.h.store.list_events(s.session.id)
            assert sum(event.type == "agent_task.running" for event in events) == 1 and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["captured_input", "captured_identity", "budget", "deadline", "run_item", "task", "interrupt"])
async def test_pre_execution_drift_or_revocation_does_not_invoke_or_change_facts(formal_case, drift):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            task = h.task.model_copy(deep=True)
            if drift == "captured_input":
                task.input["case_execution_input"]["context"]["objective"] += "changed"
            elif drift == "captured_identity":
                task = task.model_copy(update={"project_id": str(uuid4())})
            elif drift == "task":
                assert await s.h.store.renew_turn_execution(s.session.id, task.lease_token, 0)
            elif drift == "interrupt":
                assert await s.h.tasks.interrupt(s.session.id)
            else:
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    if drift == "run_item":
                        cur.execute("SELECT clock_timestamp() - interval '1 second' AS expiry")
                        s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": cur.fetchone()["expiry"]}))
                    else:
                        policy = s.budget.model_dump(mode="json")
                        if drift == "budget":
                            policy["max_calls"] += 1
                        else:
                            policy["deadline_at"] = "2000-01-01T00:00:00Z"
                        cur.execute(f"UPDATE {s.calls.budgets_table} SET policy=%s::jsonb WHERE binding_id=%s",
                            (json.dumps(policy), s.binding.binding_id))
            before = database_digest(s)
            with pytest.raises((ValueError, ContinuationLeaseLostError)):
                await executor(s, h).execute(task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert database_digest(s) == before and requests == []
            assert current_framework_owner() is None


@pytest.mark.asyncio
async def test_running_intent_is_committed_before_tool_assembly_and_model_resolution(formal_case):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            seen = []
            async def build(prepared):
                assert prepared.asset_sha256 == s.asset.asset_sha256
                assert current_framework_owner() == h.task.runtime_owner
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert any(event.type == "agent_task.running" for event in await s.h.store.list_events(s.session.id))
                # Mutating the caller's Worker copy cannot rewrite the already
                # captured task/input, or the durable original returned by begin.
                h.task.input["case_execution_input"]["graph_input"]["system_prompt"] = "caller mutation"
                seen.append("tools")
                return []

            async def resolve(key):
                assert seen == ["tools"]
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                seen.append("model")
                return await h.models.resolve_langchain_chat_model(key)

            h.adapter._model_resolver = resolve
            outcome = await executor(s, h).execute(h.task, build_business_tools=build,
                resource_id="isolated-formal-graph", fencing_token=1)
            assert seen == ["tools", "model"] and outcome.native_terminal is not None
            assert s.graph_request.system_prompt in str(requests[0]["body"]) and "caller mutation" not in str(requests)


@pytest.mark.asyncio
async def test_failure_between_running_commit_and_graph_is_not_replayable(formal_case):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            async def fail(prepared):
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                raise RuntimeError("isolated failure before graph entry")

            with pytest.raises(RuntimeError, match="before graph entry"):
                await executor(s, h).execute(h.task, build_business_tools=fail,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert (await s.h.tasks.get(h.task.id)).status == "running"
            before = database_digest(s)
            with pytest.raises(ValueError, match="claimed"):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert requests == [] and database_digest(s) == before
            assert current_framework_owner() is None


@pytest.mark.asyncio
async def test_cancelled_begin_waiter_may_commit_but_never_invokes_or_replays(formal_case, caplog):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            with postgres_connect(s.h.settings) as holder, holder.cursor() as lock:
                lock.execute(f"SELECT id FROM {s.h.tasks._session_table} WHERE id=%s FOR UPDATE", (s.session.id,))
                pending = asyncio.create_task(executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1))
                try:
                    await wait_for_session_sql_lock(s)
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                finally:
                    holder.commit()
            # Cancelling the await does not cancel the SQL worker. Observe its
            # real committed receipt before fixture teardown or a retry check.
            async with asyncio.timeout(5):
                while (await s.h.tasks.get(h.task.id)).status != "running":
                    await asyncio.sleep(.01)
            assert requests == []
            with pytest.raises(ValueError, match="claimed"):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert any(getattr(record, "recovery_reason", None) == "begin_receipt_unknown" for record in caplog.records)
            assert current_framework_owner() is None


@pytest.mark.asyncio
async def test_waiting_native_approval_is_not_a_completed_case_or_business_approval(formal_case):
    s = formal_case
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with execution(s, _config("openai_chat_completions", url), tools=frozen_tool_schema(),
                gating={"inspect_source": {"allowed_decisions": ["approve", "reject"]}}) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=True) as bridge:
                async def build(prepared):
                    tools, _ = await bridge.runtime._build_deep_agent_business_tools(state=bridge.state, tool_records=bridge.records)
                    return tools
                outcome = await executor(s, h).execute(h.task, build_business_tools=build,
                    resource_id="isolated-formal-graph", fencing_token=1)
                assert len(outcome.native_interrupts) == 1 and outcome.native_terminal is None
                assert effects == [] and len(requests) == 1 and await bridge.jobs.list_jobs(session_id=s.session.id) == []
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None


@pytest.mark.asyncio
async def test_unknown_model_effect_preserves_ledger_and_prevents_automatic_replay(formal_case):
    from langchain_openai.chat_models.base import OpenAIAPIError

    s = formal_case
    with _provider("openai_chat_completions", status=503) as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            with pytest.raises(OpenAIAPIError, match="controlled fixture failure"):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            ledger = await s.calls.snapshot(s.binding)
            assert ledger.unknown_calls == ledger.reserved_calls == len(requests) == 1
            assert (await s.h.tasks.get(h.task.id)).status == "running"
            before = database_digest(s)
            with pytest.raises(ValueError, match="claimed"):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert len(requests) == 1 and await s.calls.snapshot(s.binding) == ledger
            assert database_digest(s) == before and current_framework_owner() is None


@pytest.mark.asyncio
async def test_mismatched_tools_fail_before_model_without_erasing_running_intent(formal_case):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url), tools=frozen_tool_schema()) as h:
            with pytest.raises(ValueError, match="frozen schemas"):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert requests == [] and (await s.calls.snapshot(s.binding)).reserved_calls == 0
            assert (await s.h.tasks.get(h.task.id)).status == "running"


@pytest.mark.asyncio
async def test_audit_ack_failure_prevents_tool_effect_and_second_model_call(formal_case, monkeypatch):
    s = formal_case
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with execution(s, _config("openai_chat_completions", url), tools=frozen_tool_schema()) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                original = s.h.store.append_event
                async def append(session_id, event, **kwargs):
                    if event.type == "tool.execution_requested":
                        raise RuntimeError("isolated audit ACK failure")
                    return await original(session_id, event, **kwargs)
                monkeypatch.setattr(s.h.store, "append_event", append)

                async def build(prepared):
                    tools, _ = await bridge.runtime._build_deep_agent_business_tools(state=bridge.state, tool_records=bridge.records)
                    return tools
                with pytest.raises(RuntimeError, match="audit ACK failure"):
                    await executor(s, h).execute(h.task, build_business_tools=build,
                        resource_id="isolated-formal-graph", fencing_token=1)
                assert len(requests) == 1 and effects == []
                assert await bridge.jobs.list_jobs(session_id=s.session.id) == []
                assert (await s.h.tasks.get(h.task.id)).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["run_item", "agent_task"])
async def test_begin_rechecks_both_leases_after_waiting_for_budget_lock(formal_case, lost):
    s = formal_case
    await submit(s)
    task = await claim(s)
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() + interval '2 seconds' AS expiry")
        expiry = cur.fetchone()["expiry"]
        if lost == "run_item":
            s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": expiry}))
        else:
            cur.execute(f"UPDATE {s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{turn_lease_expires_at}',to_jsonb(%s::timestamptz)) WHERE id=%s", (expiry, s.session.id))
            cur.execute(f"UPDATE {s.h.tasks._tasks}_runtime SET expires_at=%s WHERE binding_id=%s", (expiry, s.binding.binding_id))
    before = database_digest(s)
    with postgres_connect(s.h.settings) as holder, holder.cursor() as lock:
        lock.execute(f"SELECT binding_id FROM {s.calls.budgets_table} WHERE binding_id=%s FOR UPDATE", (s.binding.binding_id,))
        pending = asyncio.create_task(s.h.tasks.begin(task, resource_id="isolated-formal-graph", fencing_token=1))
        try:
            await wait_for_session_sql_lock(s, s.calls.budgets_table)
            lock.execute("SELECT clock_timestamp() < %s AS active", (expiry,))
            assert lock.fetchone()["active"]
            deadline = time.monotonic() + 4
            while True:
                lock.execute("SELECT clock_timestamp() >= %s AS expired", (expiry,))
                if lock.fetchone()["expired"]:
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            holder.commit()
            with pytest.raises(ContinuationLeaseLostError):
                await pending
        finally:
            holder.rollback()
            if not pending.done():
                await pending
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_formal_native_original_survives_lease_loss_without_granting_reexecution(formal_case):
    from src.application.deep_agents.native_terminal import collect_native_terminal
    from src.schemas.runtime_binding import RuntimeCheckpointSource

    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            outcome = await executor(s, h).execute(h.task, build_business_tools=no_tools,
                resource_id="isolated-formal-graph", fencing_token=1)
            assert await s.h.store.renew_turn_execution(s.session.id, h.task.lease_token, 0)
            source = RuntimeCheckpointSource.model_validate(h.task.runtime_owner.model_dump(exclude={"owner_token"}))
            before = database_digest(s)
            async with h.adapter.open_native_reader(s.asset.graph_input, source=source) as (graph, saver):
                original = await collect_native_terminal(graph, saver, {"configurable": {"thread_id": s.turn}})
            assert original.proof == outcome.native_terminal.proof
            assert original.checkpoint_timestamp == outcome.native_terminal.checkpoint_timestamp
            with pytest.raises(ContinuationLeaseLostError):
                await executor(s, h).execute(h.task, build_business_tools=no_tools,
                    resource_id="isolated-formal-graph", fencing_token=1)
            assert database_digest(s) == before and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "run_item_lease"])
async def test_stop_during_real_model_setup_never_sends_or_finishes(formal_case, stop):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async with execution(s, _config("openai_chat_completions", url)) as h:
            entered, release = asyncio.Event(), asyncio.Event()
            async def resolve(key):
                entered.set()
                await release.wait()
                return await h.models.resolve_langchain_chat_model(key)
            h.adapter._model_resolver = resolve
            pending = asyncio.create_task(executor(s, h).execute(h.task, build_business_tools=no_tools,
                resource_id="isolated-formal-graph", fencing_token=1))
            try:
                async with asyncio.timeout(5):
                    await entered.wait()
                if stop == "cancel":
                    pending.cancel()
                else:
                    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                        cur.execute("SELECT clock_timestamp() - interval '1 second' AS expiry")
                        s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": cur.fetchone()["expiry"]}))
                    release.set()
                with pytest.raises(asyncio.CancelledError if stop == "cancel" else ContinuationLeaseLostError):
                    await pending
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            assert requests == [] and (await s.calls.snapshot(s.binding)).reserved_calls == 0
            assert (await s.h.tasks.get(h.task.id)).status == "running"
            assert (await s.d.runs.get_item(s.item.id)).result_id is None and current_framework_owner() is None


@pytest.mark.asyncio
async def test_late_tool_output_after_run_item_lease_loss_cannot_checkpoint_or_call_model_again(formal_case):
    s = formal_case
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with execution(s, _config("openai_chat_completions", url), tools=frozen_tool_schema()) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                entered, release = asyncio.Event(), asyncio.Event()
                async def handle(arguments, context):
                    effects.append(context.tool_job_id)
                    entered.set()
                    await release.wait()
                    return {"status": "completed", "summary": "late fixture-only effect"}
                bridge.runtime._tool_runtime_service._handlers["inspect_source"] = handle

                async def build(prepared):
                    tools, _ = await bridge.runtime._build_deep_agent_business_tools(state=bridge.state, tool_records=bridge.records)
                    return tools
                pending = asyncio.create_task(executor(s, h).execute(h.task, build_business_tools=build,
                    resource_id="isolated-formal-graph", fencing_token=1))
                try:
                    async with asyncio.timeout(5):
                        await entered.wait()
                    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                        cur.execute("SELECT clock_timestamp() - interval '1 second' AS expiry")
                        s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": cur.fetchone()["expiry"]}))
                    release.set()
                    with pytest.raises(ContinuationLeaseLostError):
                        await pending
                finally:
                    release.set()
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                assert len(effects) == len(requests) == 1
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
                events = await s.h.store.list_events(s.session.id)
                assert not any(event.type == "tool.execution_completed" for event in events)
                assert (await s.calls.snapshot(s.binding)).reserved_calls == 1

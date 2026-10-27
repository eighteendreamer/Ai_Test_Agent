from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import asyncio
import json
from uuid import uuid4

import pytest

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeError, DeepAgentRuntimeRequest
from src.application.models.model_runtime_service import ModelRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_model_call import FrameworkModelBudget
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


@asynccontextmanager
async def governed(h, config, *, begin_execution=True, **limits):
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint

    h.calls = PostgresFrameworkModelCalls(h.settings)
    await h.calls.initialize()
    fingerprint = model_config_fingerprint(config)
    h.binding = make_binding(h.session_id, f"turn-{uuid4().hex}", model_config_fingerprint=fingerprint)
    task = await h.tasks.submit_v2(make_command(h, h.binding), make_payload(), make_message(h, h.binding),
        idempotency_key=uuid4().hex)
    h.owner = await h.tasks.claim(task.id, worker_id="governed-model", expected_binding=h.binding)
    if begin_execution:
        await h.tasks.begin(h.owner, resource_id="model-policy-test", fencing_token=1)
        await h.tasks.renew_framework_execution(h.owner, 120)
    await h.calls.freeze(h.owner.runtime_owner, FrameworkModelBudget(
        max_calls=limits.get("max_calls", 8), max_tokens=limits.get("max_tokens", 500_000),
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model_config_fingerprints={config.key: fingerprint}))

    class Registry:
        current = config

        def get_runtime_config(self, key):
            assert key == config.key
            return self.current

    h.registry = Registry()
    h.models = ModelRuntimeService(h.registry, h.settings)
    h.policy = FrameworkModelCallPolicy(calls=h.calls, model_config_resolver=h.models.get_model_config)
    h.settings.deep_agents.checkpoint_schema = f"model_policy_{uuid4().hex}"
    h.provider = DeepAgentCheckpointProvider(h.settings)
    h.adapter = DeepAgentRuntimeAdapter(model_resolver=h.models.resolve_langchain_chat_model,
        checkpointer_factory=h.provider.open, model_call_policy=h.policy)
    h.request = DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
        trace_id="policy-trace", model_key=config.key, system_prompt="Review the supplied fixture.",
        messages=[{"role": "user", "content": "fixture request"}],
        checkpoint_namespace=h.binding.checkpoint_namespace)
    try:
        yield h
    finally:
        await h.provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")
            cur.execute(f"DROP TABLE {h.calls.calls_table}")
            cur.execute(f"DROP TABLE {h.calls.budgets_table}")


async def execute(h, request=None):
    with framework_owner_scope(h.owner.runtime_owner):
        return await h.adapter.execute(request or h.request)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_actual_official_deep_agent_request_reserves_and_settles(harness, transport, streaming, tmp_path):
    with _provider(transport) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            request = replace(h.request, read_only_filesystem_enabled=streaming,
                cognitive_subagents_enabled=streaming, context={"project_root": str(tmp_path)})
            result = await execute(h, request)
            assert result.output_text == "fixture answer" and len(requests) == 1
            snapshot = await h.calls.snapshot(h.binding)
            assert snapshot.reserved_calls == 1 and snapshot.actual_tokens == snapshot.charged_tokens == 10
            call, = snapshot.calls
            assert call.phase == "completed" and call.intent.transport == transport
            assert call.intent.purpose == "agent" and call.intent.branch_id == "root"
            assert call.intent.reserved_output_tokens == 512 and call.intent.reserved_input_tokens > 0
            assert "fixture request" not in call.model_dump_json()
            if transport == "google_gemini_generate_content":
                assert (":streamGenerateContent" in requests[0]["path"]) is streaming
            else:
                assert bool(requests[0]["body"].get("stream")) is streaming


@pytest.mark.asyncio
async def test_budget_rejection_happens_before_sdk_http(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), max_tokens=1) as h:
            with pytest.raises(DeepAgentRuntimeError, match="budget"):
                await execute(h)
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_unknown_request_is_charged_once_and_checkpoint_replay_cannot_resend(harness, transport):
    with _provider(transport, status=503) as (url, requests):
        async with governed(harness, _config(transport, url)) as h:
            with pytest.raises(DeepAgentRuntimeError):
                await execute(h)
            snapshot = await h.calls.snapshot(h.binding)
            assert snapshot.reserved_calls == snapshot.unknown_calls == 1 and len(requests) == 1
            assert snapshot.charged_tokens == snapshot.calls[0].intent.reserved_tokens
            with pytest.raises(DeepAgentRuntimeError, match="retry"):
                await execute(h, replace(h.request, continue_from_checkpoint=True))
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot


def force_summary(monkeypatch):
    from src.application.deep_agents import model_call_middleware

    original = model_call_middleware.compute_summarization_defaults

    def small_context(model):
        return {**original(model), "trigger": ("messages", 2), "keep": ("messages", 1),
            "truncate_args_settings": None}

    monkeypatch.setattr(model_call_middleware, "compute_summarization_defaults", small_context)


def history(request):
    return replace(request, messages=[{"role": "user", "content": "old fixture question"},
        {"role": "assistant", "content": "old fixture answer"},
        {"role": "user", "content": "review the latest fixture"}])


@pytest.mark.asyncio
async def test_automatic_summary_and_main_request_share_root_ledger(harness, monkeypatch):
    force_summary(monkeypatch)
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            result = await execute(h, history(h.request))
            assert result.output_text == "fixture answer" and len(requests) == 2
            snapshot = await h.calls.snapshot(h.binding)
            assert [call.intent.purpose for call in snapshot.calls] == ["summary", "agent"]
            assert snapshot.actual_tokens == 20 and {call.intent.branch_id for call in snapshot.calls} == {"root"}
            assert all(call.phase == "completed" for call in snapshot.calls)


@pytest.mark.asyncio
async def test_failed_summary_has_no_implicit_middleware_retry(harness, monkeypatch):
    force_summary(monkeypatch)
    with _provider("openai_chat_completions", status=503) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            with pytest.raises(DeepAgentRuntimeError):
                await execute(h, history(h.request))
            snapshot = await h.calls.snapshot(h.binding)
            assert len(requests) == snapshot.unknown_calls == snapshot.reserved_calls == 1
            assert snapshot.calls[0].intent.purpose == "summary"


@pytest.mark.asyncio
async def test_named_subagent_uses_explicit_root_policy(harness, tmp_path):
    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0]["message"]["tool_calls"][0]["function"] = {"name": "task", "arguments": json.dumps({
        "description": "Inspect only the fixture.", "subagent_type": "code-review-researcher"})}
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            # A prior Turn using the same provider must not permanently remove
            # tools from later Turns that explicitly enable a bounded child.
            model = await h.models.resolve_langchain_chat_model(h.request.model_key)
            h.adapter._configure_harness(model, read_only_filesystem_enabled=False,
                read_only_max_file_size_mb=1, context={})
            result = await execute(h, replace(h.request, read_only_filesystem_enabled=True, cognitive_subagents_enabled=True,
                context={"project_root": str(tmp_path)}))
            snapshot = await h.calls.snapshot(h.binding)
            assert len(requests) == snapshot.reserved_calls == 3 and snapshot.actual_tokens == 30, {
                "streaming": [request["body"].get("stream") for request in requests],
                "calls": [(call.intent.purpose, call.phase) for call in snapshot.calls],
                "tool_results": [message for message in result.messages if message.get("role") == "tool"]}
            child, = [call for call in snapshot.calls if call.intent.purpose == "subagent"]
            assert child.intent.parent_branch_id == "root" and child.intent.branch_id != "root"
            assert all(call.phase == "completed" for call in snapshot.calls)


@pytest.mark.asyncio
async def test_official_structured_output_still_uses_same_policy(harness):
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ProviderStrategy
    from pydantic import BaseModel
    from src.application.deep_agents.model_call_middleware import FrameworkModelCallMiddleware

    class Review(BaseModel):
        summary: str

    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0].update(finish_reason="stop", message={"role": "assistant",
        "content": '{"summary":"fixture"}'})
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            model = await h.models.resolve_langchain_chat_model(h.request.model_key)
            async with h.provider.open(owner=h.owner.runtime_owner) as saver:
                agent = create_agent(model=model, response_format=ProviderStrategy(Review), checkpointer=saver,
                    middleware=[FrameworkModelCallMiddleware(policy=h.policy, owner=h.owner.runtime_owner,
                        model_key=h.request.model_key)])
                result = await agent.ainvoke({"messages": h.request.messages},
                    {"configurable": {"thread_id": h.binding.turn_id}}, durability="sync")
            assert result["structured_response"].summary == "fixture" and len(requests) == 1
            assert requests[0]["body"]["response_format"]["type"] == "json_schema"
            assert (await h.calls.snapshot(h.binding)).actual_tokens == 10


@pytest.mark.asyncio
async def test_governed_request_bypasses_global_model_cache(harness):
    from langchain.agents import create_agent
    from langchain_core.caches import InMemoryCache
    from langchain_core.globals import get_llm_cache, set_llm_cache
    from src.application.deep_agents.model_call_middleware import FrameworkModelCallMiddleware

    previous = get_llm_cache()
    set_llm_cache(InMemoryCache())
    try:
        with _provider("openai_chat_completions") as (url, requests):
            async with governed(harness, _config("openai_chat_completions", url)) as h:
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                await create_agent(model=model).ainvoke({"messages": h.request.messages})
                assert len(requests) == 1
                async with h.provider.open(owner=h.owner.runtime_owner) as saver:
                    agent = create_agent(model=model, checkpointer=saver,
                        middleware=[FrameworkModelCallMiddleware(policy=h.policy, owner=h.owner.runtime_owner,
                            model_key=h.request.model_key)])
                    await agent.ainvoke({"messages": h.request.messages},
                        {"configurable": {"thread_id": h.binding.turn_id}}, durability="sync")
                assert len(requests) == 2
                assert (await h.calls.snapshot(h.binding)).reserved_calls == 1
    finally:
        set_llm_cache(previous)


@pytest.mark.asyncio
async def test_model_configuration_change_after_resolution_blocks_before_http(harness, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        config = _config("openai_chat_completions", url)
        async with governed(harness, config) as h:
            resolve = h.models.resolve_langchain_chat_model

            async def changed(key):
                model = await resolve(key)
                h.registry.current = config.model_copy(update={"temperature": 0.7})
                return model

            monkeypatch.setattr(h.adapter, "_model_resolver", changed)
            with pytest.raises(DeepAgentRuntimeError, match="configuration"):
                await execute(h)
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_response_settlement_failure_never_reissues_provider_request(harness, monkeypatch, caplog, committed):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            complete = h.calls.complete

            async def interrupted_commit(*args, **kwargs):
                if committed:
                    await complete(*args, **kwargs)
                raise ConnectionError("injected completion acknowledgement failure")

            with monkeypatch.context() as patch:
                patch.setattr(h.calls, "complete", interrupted_commit)
                with pytest.raises(DeepAgentRuntimeError, match="acknowledgement"):
                    await execute(h)
            snapshot = await h.calls.snapshot(h.binding)
            call, = snapshot.calls
            assert len(requests) == 1 and call.phase == ("completed" if committed else "invoking")
            assert snapshot.charged_tokens == (10 if committed else call.intent.reserved_tokens)
            assert "framework_model_response_settlement_failed" in caplog.messages
            with pytest.raises(DeepAgentRuntimeError, match="retry"):
                await execute(h, replace(h.request, continue_from_checkpoint=True))
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancelled", "owner_lost"])
async def test_response_visibility_window_preserves_uncertainty(harness, monkeypatch, failure):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            invoke = h.policy.invoke
            received, release = asyncio.Event(), asyncio.Event()

            async def response_window(**kwargs):
                send = kwargs["send"]

                async def delayed_visibility():
                    response = await send()
                    received.set()
                    await release.wait()
                    return response

                return await invoke(**{**kwargs, "send": delayed_visibility})

            monkeypatch.setattr(h.policy, "invoke", response_window)
            task = asyncio.create_task(execute(h))
            try:
                await asyncio.wait_for(received.wait(), 10)
                assert len(requests) == 1
                if failure == "cancelled":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
                    release.set()
                    with pytest.raises(ContinuationLeaseLostError, match="owner is no longer active"):
                        await task
                snapshot = await h.calls.snapshot(h.binding)
                call, = snapshot.calls
                assert call.phase == ("unknown" if failure == "cancelled" else "invoking")
                assert call.usage is None and snapshot.charged_tokens == call.intent.reserved_tokens
                assert len(requests) == 1
                if failure == "owner_lost":
                    with pytest.raises(ContinuationLeaseLostError):
                        await execute(h, replace(h.request, continue_from_checkpoint=True))
                    assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

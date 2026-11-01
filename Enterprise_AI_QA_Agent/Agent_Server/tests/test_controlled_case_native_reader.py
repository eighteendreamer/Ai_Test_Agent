"""Source-only schema inspection, not formal RunItem recovery permission."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import messages_to_dict

from src.application.deep_agents.native_terminal import collect_native_terminal, require_native_terminal_original
from src.application.deep_agents.result_projection import DeepAgentRuntimeError
from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter, ControlledCaseGraphRequest
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.runtime_binding import RuntimeCheckpointSource
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_adapter import adapter_request
from tests.test_controlled_case_graph import business_bridge, config, wrapped_tools
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply
from tests.test_framework_graph_input_asset import prepared_graph_input
from tests.test_framework_harness_settings import original_asset, rehash
from tests.test_framework_model_policy import governed
from tests.test_runtime_binding_persistence import make_binding


def source_and_request():
    source = RuntimeCheckpointSource(binding=make_binding("session", "turn", runtime_kind="langgraph"),
        owner_epoch=1, task_id="task", attempt_id="attempt", worker_id="worker")
    request = ControlledCaseGraphRequest(session_id="session", turn_id="turn", trace_id="trace",
        model_key="fixture", checkpoint_namespace=source.binding.checkpoint_namespace,
        system_prompt="fixed", framework_messages=[])
    return source, request


def forbidden(*args, **kwargs):
    pytest.fail("Source-only schema inspection reached a model, policy, tool or execution path")


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_controlled_source_reader_reopens_exact_terminal_after_expiry_without_execution(harness, transport, monkeypatch):
    reply = _tool_reply(transport)
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        async with governed(harness, _config(transport, url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            effects = []
            adapter, request = adapter_request(h, business_tools=wrapped_tools(h, effects))
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute(request)
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            budget = await h.calls.snapshot(h.binding)
            assert len(requests) == 2 and len(effects) == 1
            await h.provider.close()
            assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
            adapter = ControlledCaseGraphAdapter(model_resolver=forbidden,
                checkpointer_factory=h.provider.open, model_call_policy=SimpleNamespace(execution_scope=forbidden))
            # The source schema must not consult any original tool wrapper.
            request = replace(request, business_tools=[SimpleNamespace(name="inspect_source", ainvoke=forbidden)])
            async with adapter.open_native_reader(request, source=source) as (graph, reader):
                assert not hasattr(reader, "runtime_owner") and reader.runtime_source == source
                assert set(graph.get_graph().nodes) == {"__start__", "model", "tools", "__end__"}
                monkeypatch.setattr(graph, "ainvoke", forbidden)
                monkeypatch.setattr(graph, "astream_events", forbidden)
                before = await asyncio.to_thread(list, reader.list(config(h)))
                restored = await collect_native_terminal(graph, reader, config(h))
                assert restored == outcome.native_terminal
                assert messages_to_dict(restored.values["messages"]) == messages_to_dict(outcome.values["messages"])
                with pytest.raises(PermissionError, match="source-only"):
                    await reader.aput_writes(config(h), [("messages", [])], "cannot-write")
                assert await asyncio.to_thread(list, reader.list(config(h))) == before
            # SAME application connection, no new owner or transaction commit.
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                cur.execute("SELECT current_setting('search_path') AS path")
                path = cur.fetchone()["path"]
                require_native_terminal_original(cur, h.settings, source, restored, request)
                cur.execute("SELECT current_setting('search_path') AS path")
                assert cur.fetchone()["path"] == path
            assert await h.calls.snapshot(h.binding) == budget
            assert len(requests) == 2 and len(effects) == 1


@pytest.mark.asyncio
async def test_source_reader_preserves_pending_interrupts_but_cannot_deliver_or_call_them(harness):
    from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts
    from langgraph.types import Command

    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph"}) as h:
            effects = []
            adapter, request = adapter_request(h, business_tools=wrapped_tools(h, effects),
                interrupt_on={"inspect_source": {"allowed_decisions": ["approve", "reject"]}})
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute(request)
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            budget = await h.calls.snapshot(h.binding)
            adapter = ControlledCaseGraphAdapter(model_resolver=forbidden,
                checkpointer_factory=h.provider.open, model_call_policy=None)
            async with adapter.open_native_reader(request, source=source) as (graph, reader):
                before = await asyncio.to_thread(list, reader.list(config(h)))
                snapshot = await graph.aget_state(config(h), subgraphs=True)
                pending, = outcome.native_interrupts
                task, = snapshot.tasks
                interrupt, = snapshot.interrupts
                assert task.id == pending.graph_task_id and task.interrupts == (interrupt,)
                assert interrupt.id == pending.interrupt_id and snapshot.next == ("tools",)
                assert interrupt.value["tool_call_id"] == pending.tool_call_id
                assert interrupt.value["name"] == pending.tool_name and interrupt.value["args"] == pending.arguments
                assert messages_to_dict(snapshot.values["messages"]) == messages_to_dict(outcome.values["messages"])
                # This existing collector is an owned approval-material port,
                # not the public SDK snapshot read. Do not broaden its rights.
                with pytest.raises(PermissionError, match="source-only"):
                    await collect_pending_tool_interrupts(graph, reader, config(h))
                with pytest.raises(FrameworkMessageStageRecoveryRequired, match="pending tasks"):
                    await collect_native_terminal(graph, reader, config(h))
                # Resume can reach the inert node before SDK background
                # writes fail; either independent refusal preserves the scope.
                with pytest.raises(PermissionError, match="schema cannot execute|source-only"):
                    await graph.ainvoke(Command(resume={pending.interrupt_id: {"type": "approve"}}),
                        config(h), durability="sync")
                assert await graph.aget_state(config(h), subgraphs=True) == snapshot
                assert await asyncio.to_thread(list, reader.list(config(h))) == before
            assert effects == [] and len(requests) == 1 and await h.calls.snapshot(h.binding) == budget


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["messages", "timestamp", "checkpoint", "serializer", "channels", "fingerprint", "thread", "namespace"])
async def test_atomic_controlled_native_recheck_rejects_changed_originals(harness, changed):
    from src.application.deep_agents.native_terminal import _native_hash

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute(request)
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            native = deepcopy(outcome.native_terminal)
            if changed == "messages":
                native.values["messages"][-1].content = "not the persisted answer"
                from src.application.deep_agents.checkpoint_provider import checkpoint_serializer

                native = replace(native, proof=native.proof.model_copy(update={
                    "state_sha256": _native_hash(checkpoint_serializer(), native.values)}))
            elif changed == "timestamp":
                native = replace(native, checkpoint_timestamp="2000-01-01T00:00:00+00:00")
            else:
                field, value = {"checkpoint": ("checkpoint_sha256", "0" * 64),
                    "serializer": ("serializer", "foreign.codec"),
                    "channels": ("uncheckpointed_channels", ("messages",)),
                    "fingerprint": ("binding_fingerprint", "foreign-binding"),
                    "thread": ("thread_id", "foreign-thread"),
                    "namespace": ("checkpoint_namespace", "foreign-namespace")}[changed]
                native = replace(native, proof=native.proof.model_copy(update={field: value}))
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                with pytest.raises(FrameworkMessageStageRecoveryRequired, match="native|source"):
                    require_native_terminal_original(cur, h.settings, source, native, request)
            assert len(requests) == 1 and (await h.calls.snapshot(h.binding)).reserved_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_source_only_terminal_after_native_resume_preserves_original_budget_and_messages(harness, decision):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph"}) as h:
            effects = []
            adapter, request = adapter_request(h, business_tools=wrapped_tools(h, effects),
                interrupt_on={"inspect_source": {"allowed_decisions": ["approve", "reject"]}})
            with framework_owner_scope(h.owner.runtime_owner):
                waiting = await adapter.execute(request)
                interrupt, = waiting.native_interrupts
                outcome = await adapter.execute(replace(request, resume={interrupt.interrupt_id: {"type": decision}}))
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            budget = await h.calls.snapshot(h.binding)
            assert len(effects) == (1 if decision == "approve" else 0) and len(requests) == 2
            assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
            adapter = ControlledCaseGraphAdapter(model_resolver=forbidden,
                checkpointer_factory=h.provider.open, model_call_policy=None)
            async with adapter.open_native_reader(request, source=source) as (graph, reader):
                assert await collect_native_terminal(graph, reader, config(h)) == outcome.native_terminal
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                require_native_terminal_original(cur, h.settings, source, outcome.native_terminal, request)
            assert await h.calls.snapshot(h.binding) == budget
            assert len(effects) == (1 if decision == "approve" else 0) and len(requests) == 2


@pytest.mark.asyncio
async def test_source_only_original_retains_real_business_tooljob_and_projection_material(harness):
    from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY

    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph"}) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                with framework_owner_scope(h.owner.runtime_owner):
                    tools, gating = await bridge.runtime._build_deep_agent_business_tools(
                        state=bridge.state, tool_records=bridge.records)
                    adapter, request = adapter_request(h, business_tools=tools, interrupt_on=gating)
                    outcome = await adapter.execute(request)
                jobs = await bridge.jobs.list_jobs(session_id=h.session_id)
                source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
                async with adapter.open_native_reader(request, source=source) as (graph, reader):
                    native = await collect_native_terminal(graph, reader, config(h))
                    assert native == outcome.native_terminal
                    message = native.values["messages"][2]
                    material = FrameworkToolProjection.model_validate(message.artifact[TOOL_PROJECTION_KEY])
                    material.require_binding(h.binding, material.source)
                    assert bridge.records == [material.record.model_dump(mode="python")]
                assert await bridge.jobs.list_jobs(session_id=h.session_id) == jobs
                assert len(effects) == 1 and len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["session", "turn", "namespace", "kind", "input_type"])
async def test_source_schema_rejects_mismatched_input_before_opening_checkpoint_provider(changed):
    source, request = source_and_request()
    if changed in {"session", "turn", "namespace"}:
        request = replace(request, **{{"session": "session_id", "turn": "turn_id",
            "namespace": "checkpoint_namespace"}[changed]: "foreign"})
    elif changed == "kind":
        source = source.model_copy(update={"binding": source.binding.model_copy(update={"runtime_kind": "deepagents"})})
    else:
        request = prepared_graph_input(source.binding, "trace")

    @asynccontextmanager
    async def no_open(**kwargs):
        forbidden()
        yield None

    adapter = ControlledCaseGraphAdapter(model_resolver=forbidden, checkpointer_factory=no_open, model_call_policy=None)
    with pytest.raises(ValueError, match="Controlled.*source|Controlled.*request"):
        async with adapter.open_native_reader(request, source=source):
            pytest.fail("Mismatched source opened")


@pytest.mark.parametrize("changed", ["source", "writer"])
def test_controlled_native_schema_cannot_borrow_foreign_source_or_writer(changed):
    source, request = source_and_request()
    saver = SimpleNamespace(runtime_source=source)
    if changed == "source":
        saver.runtime_source = source.model_copy(update={"task_id": "foreign"})
    else:
        saver.runtime_owner = object()
    adapter = ControlledCaseGraphAdapter(model_resolver=forbidden, checkpointer_factory=None, model_call_policy=None)
    with pytest.raises(ValueError, match="source-only"):
        adapter.native_schema(request, source=source, saver=saver)


@pytest.mark.asyncio
async def test_controlled_schema_actions_are_inert_even_without_saver_write_protection():
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import HumanMessage

    source, request = source_and_request()
    saver = InMemorySaver()
    saver.runtime_source = source
    adapter = ControlledCaseGraphAdapter(model_resolver=forbidden, checkpointer_factory=None, model_call_policy=None)
    graph = adapter.native_schema(request, source=source, saver=saver)
    with pytest.raises(PermissionError, match="schema.*execute"):
        await graph.ainvoke({"messages": [HumanMessage(content="not allowed")]},
            {"configurable": {"thread_id": source.binding.thread_id}})
    # Both public node actions fail closed, not just the saver or model factory.
    for node in ("model", "tools"):
        with pytest.raises(PermissionError, match="schema.*execute"):
            await graph.nodes[node].node.ainvoke({"messages": []})


def test_deep_agents_source_schema_rejects_consistently_rehashed_langgraph_binding():
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.schemas.framework_graph_input import parse_graph_input_asset

    raw = original_asset()
    binding = make_binding("session", "turn", runtime_kind="langgraph")
    raw["runtime_preparation"]["identity"]["binding"] = binding.model_dump(mode="json")
    raw["runtime_preparation"]["initial_state"]["context_bundle"]["runtime_binding"] = binding.model_dump(mode="json")
    original = parse_graph_input_asset(rehash(raw))
    source = RuntimeCheckpointSource(binding=binding, owner_epoch=1, task_id="task", attempt_id="attempt", worker_id="worker")
    with pytest.raises(DeepAgentRuntimeError, match="runtime kind|Deep Agents.*source"):
        DeepAgentRuntimeAdapter(model_resolver=forbidden).native_schema(original, source=source, saver=None)


def test_shared_schema_dispatch_cannot_interpret_a_controlled_request_as_deep_agents():
    from src.application.runtime.framework_native_schema import native_graph_schema

    source, request = source_and_request()
    source = source.model_copy(update={"binding": source.binding.model_copy(update={"runtime_kind": "deepagents"})})
    with pytest.raises(ValueError):
        native_graph_schema(request, source=source, saver=SimpleNamespace(runtime_source=source))

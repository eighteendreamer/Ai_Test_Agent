"""Audited graph boundary, not RunItem admission or application completion."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import messages_to_dict

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.deep_agents.result_projection import DeepAgentRuntimeError
from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.models.framework_model_call_policy import FrameworkExecutionDeadlineExceeded
from src.runtime.execution_context import framework_owner_scope
from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_graph import business_bridge, initial, wrapped_tools
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply
from tests.test_framework_execution_deadline import set_deadline
from tests.test_framework_model_policy import governed


def adapter_request(h, **changes):
    from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter, ControlledCaseGraphRequest

    adapter = ControlledCaseGraphAdapter(model_resolver=h.models.resolve_langchain_chat_model,
        checkpointer_factory=h.provider.open, model_call_policy=h.policy)
    request = ControlledCaseGraphRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
        trace_id="controlled-adapter", model_key=h.request.model_key, checkpoint_namespace=h.binding.checkpoint_namespace,
        system_prompt="FIXED_CASE_CONTEXT: use only the frozen steps.",
        framework_messages=serialize_messages(initial()["messages"]))
    return adapter, replace(request, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_controlled_adapter_captures_original_terminal_budget_and_business_tool_material(harness, transport):
    reply = _tool_reply(transport)
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        async with governed(harness, _config(transport, url), binding_overrides={
                "runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                with framework_owner_scope(h.owner.runtime_owner):
                    tools, gating = await bridge.runtime._build_deep_agent_business_tools(
                        state=bridge.state, tool_records=bridge.records)
                    adapter, request = adapter_request(h, business_tools=tools, interrupt_on=gating)
                    outcome = await adapter.execute(request)
                    # Reading/continuing a terminal graph cannot issue a new model/tool call.
                    original = await adapter.execute(replace(request, continue_from_checkpoint=True))
                assert len(requests) == 2 and len(effects) == 1
                assert outcome.values["messages"][-1].text == "fixture answer"
                assert outcome.native_interrupts == [] and outcome.subagent_events == []
                assert outcome.native_terminal is not None
                assert outcome.native_terminal.proof == original.native_terminal.proof
                assert outcome.native_terminal.checkpoint_timestamp == original.native_terminal.checkpoint_timestamp
                assert outcome.native_terminal.proof.binding_fingerprint == h.binding.fingerprint
                assert messages_to_dict(outcome.values["messages"]) == messages_to_dict(outcome.native_terminal.values["messages"])
                assert messages_to_dict(outcome.values["messages"]) == messages_to_dict(original.values["messages"])
                job, = await bridge.jobs.list_jobs(session_id=h.session_id)
                assert job.status.value == "completed" and effects[0][2] == job.id
                from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY

                material = FrameworkToolProjection.model_validate(outcome.values["messages"][2].artifact[TOOL_PROJECTION_KEY])
                material.require_binding(h.binding, material.source)
                assert bridge.records == [material.record.model_dump(mode="python")]
                ledger = await h.calls.snapshot(h.binding)
                assert read_model_budget_material(h.binding, outcome.root_model_budget) == ledger
                assert outcome.root_model_budget == original.root_model_budget
                assert ledger.reserved_calls == 2 and ledger.actual_tokens == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_controlled_adapter_rebuilds_native_approval_without_changing_root_or_source(harness, decision):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={
                "runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            with framework_owner_scope(h.owner.runtime_owner):
                adapter, request = adapter_request(h, business_tools=wrapped_tools(h, effects),
                    interrupt_on={"inspect_source": {"allowed_decisions": ["approve", "reject"]}})
                waiting = await adapter.execute(request)
                pending, = waiting.native_interrupts
                assert len(requests) == 1 and effects == [] and waiting.native_terminal is None
                assert read_model_budget_material(h.binding, waiting.root_model_budget).reserved_calls == 1
                adapter, request = adapter_request(h, business_tools=wrapped_tools(h, effects),
                    interrupt_on=request.interrupt_on, resume={pending.interrupt_id: {"type": decision}})
                completed = await adapter.execute(request)
            assert completed.native_interrupts == [] and completed.native_terminal is not None
            assert len(requests) == 2 and len(effects) == (1 if decision == "approve" else 0)
            if effects:
                assert effects[0].source_message_id == pending.source_message_id
            assert read_model_budget_material(h.binding, completed.root_model_budget).reserved_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["session_id", "turn_id", "checkpoint_namespace", "kind"])
async def test_controlled_adapter_rejects_mismatched_request_before_model_resolution(harness, monkeypatch, mismatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={
                "runtime_kind": "deepagents" if mismatch == "kind" else "langgraph"}) as h:
            async def forbidden(key):
                pytest.fail("mismatched graph input resolved a model")

            monkeypatch.setattr(h.models, "resolve_langchain_chat_model", forbidden)
            adapter, request = adapter_request(h, **({} if mismatch == "kind" else {mismatch: "other"}))
            with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ValueError, match="captured LangGraph owner"):
                await adapter.execute(request)
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
async def test_controlled_adapter_unknown_request_is_not_retried(harness):
    with _provider("openai_chat_completions", status=503) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={
                "runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            with framework_owner_scope(h.owner.runtime_owner):
                with pytest.raises(Exception, match="controlled fixture failure"):
                    await adapter.execute(request)
                before = await h.calls.snapshot(h.binding)
                with pytest.raises(FrameworkModelRecoveryRequired):
                    await adapter.execute(replace(request, continue_from_checkpoint=True))
            assert len(requests) == 1 and before.unknown_calls == 1
            assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_controlled_adapter_uses_original_root_deadline_and_does_not_reenter_after_stop(harness, stop):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={
                "runtime_kind": "langgraph"}) as h:
            if stop == "deadline":
                set_deadline(h, 3)
            effects, entered, block = [], asyncio.Event(), asyncio.Event()
            adapter, request = adapter_request(h, business_tools=wrapped_tools(h, effects, entered=entered, block=block))
            with framework_owner_scope(h.owner.runtime_owner):
                task = asyncio.create_task(adapter.execute(request))
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    if stop == "cancel":
                        task.cancel()
                    with pytest.raises(asyncio.CancelledError if stop == "cancel" else FrameworkExecutionDeadlineExceeded):
                        await asyncio.wait_for(task, 10)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert len(requests) == len(effects) == 1
            assert (await h.calls.snapshot(h.binding)).reserved_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("expired", [False, True])
async def test_shared_graph_invocation_preserves_sync_durability_and_deadline_disarm(persistent, expired):
    from src.application.runtime.framework_graph_invocation import invoke_and_audit_framework_graph

    observed, disarmed = [], []
    async def invoke(value, config, **options):
        observed.append((value, config, options))
        return {"messages": []}

    timer = SimpleNamespace(expired=lambda: expired, reschedule=disarmed.append)
    outcome = await invoke_and_audit_framework_graph(agent=SimpleNamespace(ainvoke=invoke),
        invoke_input={"messages": []}, invoke_config={"configurable": {"thread_id": "test"}},
        checkpointer=object() if persistent else None, execution_owner=None,
        model_call_policy=None, execution_deadline=timer)
    assert observed[0][2] == ({"durability": "sync"} if persistent else {})
    assert disarmed == ([] if expired else [None])
    assert outcome.native_terminal is None and outcome.root_model_budget is None


@pytest.mark.asyncio
async def test_shared_graph_invocation_rejects_returned_interrupt_disagreement(monkeypatch):
    from src.application.runtime.framework_graph_invocation import invoke_and_audit_framework_graph
    import src.application.deep_agents.native_interrupts as native

    async def invoke(*args, **kwargs):
        return {"__interrupt__": []}
    async def pending(*args):
        return [SimpleNamespace(interrupt_id="actually-pending")]

    monkeypatch.setattr(native, "collect_pending_tool_interrupts", pending)
    with pytest.raises(DeepAgentRuntimeError, match="disagree with pending native tasks"):
        await invoke_and_audit_framework_graph(agent=SimpleNamespace(ainvoke=invoke), invoke_input=None,
            invoke_config={}, checkpointer=object(), execution_owner=object(), model_call_policy=None,
            execution_deadline=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change,match", [({"session_id": ""}, "requires its request"),
    ({"resume": {}, "continue_from_checkpoint": True}, "cannot also submit"),
    ({"framework_messages": []}, "original native input"),
    ({"framework_messages": [{"role": "user", "content": "lossy"}]}, "LangChain message dictionaries")])
async def test_controlled_adapter_invalid_input_cannot_reach_a_model_or_policy(change, match):
    from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter, ControlledCaseGraphRequest

    @asynccontextmanager
    async def captured():
        yield SimpleNamespace(runtime_owner=SimpleNamespace(binding=SimpleNamespace(runtime_kind="langgraph",
            session_id="session", turn_id="turn", checkpoint_namespace="qa:v2")))

    async def forbidden(key):
        pytest.fail("invalid input resolved a model")

    request = ControlledCaseGraphRequest(session_id="session", turn_id="turn", trace_id="trace", model_key="test",
        checkpoint_namespace="qa:v2", system_prompt="frozen", framework_messages=[])
    adapter = ControlledCaseGraphAdapter(model_resolver=forbidden, checkpointer_factory=captured, model_call_policy=object())
    with pytest.raises(ValueError, match=match):
        await adapter.execute(replace(request, **change))


@pytest.mark.asyncio
async def test_controlled_adapter_cannot_replace_strict_native_messages_with_a_display_answer(harness, monkeypatch):
    import src.application.runtime.controlled_case_adapter as module
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    build = module.build_controlled_case_graph
    def altered_output(**kwargs):
        graph = build(**kwargs)
        invoke = graph.ainvoke
        async def altered(*args, **kwargs):
            result = deepcopy(await invoke(*args, **kwargs))
            result["messages"][-1].content = "not the persisted source"
            return result
        monkeypatch.setattr(graph, "ainvoke", altered)
        return graph

    monkeypatch.setattr(module, "build_controlled_case_graph", altered_output)
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={
                "runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            with framework_owner_scope(h.owner.runtime_owner), pytest.raises(FrameworkMessageStageRecoveryRequired,
                    match="Returned graph output differs.*messages"):
                await adapter.execute(request)
            assert len(requests) == 1 and (await h.calls.snapshot(h.binding)).reserved_calls == 1

"""One result projection for invocation output and official native-state reads.

This contract is not RuntimeTurnResult recovery admission. Callback facts and
the producer's stage are explicit inputs, not guessed from display messages.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langgraph.types import Interrupt

from src.application.deep_agents.runtime_adapter import (
    DeepAgentRuntimeAdapter, DeepAgentRuntimeError, DeepAgentRuntimeRequest, DeepAgentRuntimeResult,
)
from src.application.deep_agents.native_terminal import collect_native_terminal
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config, start
from tests.test_framework_native_terminal import Model


@pytest.mark.asyncio
@pytest.mark.parametrize("planning", [False, True])
async def test_same_projection_consumes_official_terminal_without_running_graph(checkpoint_harness, monkeypatch, planning):
    h = checkpoint_harness
    await start(h)
    graph_holder = []
    answer = AIMessage(id="signed-answer", content=[{"type": "text", "text": " original answer "}],
        additional_kwargs={"signature": "opaque-fixture-signature"},
        response_metadata={"provider_fixture": "original"},
        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
    responses = [answer]
    if planning:
        responses.insert(0, AIMessage(id="planning-source", content="", tool_calls=[{
            "id": "planning-call", "name": "write_todos", "args": {"todos": [
                {"content": "inspect", "status": "completed"}]}, "type": "tool_call"}]))
    model = Model(responses=responses)
    async def resolve(key):
        return model
    def factory(**kwargs):
        graph = create_deep_agent(**kwargs)
        graph_holder.append(graph)
        return graph

    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve,
        checkpointer_factory=lambda: h.provider.open(owner=h.owner.runtime_owner))
    monkeypatch.setattr(adapter, "_resolve_factory", lambda: factory)
    result = await adapter.execute(DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
        trace_id="projection", model_key="fixture", system_prompt="inspect",
        checkpoint_namespace=h.binding.checkpoint_namespace,
        cognitive_planning_enabled=planning, messages=[{"role": "user", "content": "inspect"}]))
    graph, = graph_holder
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only projection cannot invoke a model, graph or tool")
    monkeypatch.setattr(graph, "ainvoke", forbidden)
    monkeypatch.setattr(graph, "astream_events", forbidden)
    monkeypatch.setattr(model, "_generate", forbidden)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        terminal = await collect_native_terminal(graph, saver, config(h))
        original_values = deepcopy(terminal.values)
        original_checkpoint = await saver.aget_tuple(config(h))
        original_events = await h.store.list_events(h.session_id)
        from src.application.deep_agents.result_projection import project_deep_agent_result

        projected = project_deep_agent_result(terminal.values, stage=result.metadata["stage"],
            native_terminal=terminal.proof.model_dump(mode="json"), subagent_events=[])
        assert projected == result
        assert projected.framework_messages == messages_to_dict(terminal.values["messages"])
        assert projected.output_text == "original answer" and bool(projected.todos) == planning
        assert await saver.aget_tuple(config(h)) == original_checkpoint
        assert await h.store.list_events(h.session_id) == original_events
        assert terminal.values == original_values
        assert project_deep_agent_result(terminal.values, stage=result.metadata["stage"],
            native_terminal=terminal.proof.model_dump(mode="json"), subagent_events=[]) == projected


def test_projection_preserves_native_tool_artifact_metadata_and_explicit_callback_facts():
    from src.application.deep_agents.result_projection import project_deep_agent_result

    values = {"messages": [HumanMessage(id="input", content="inspect"),
        AIMessage(id="source", content="", tool_calls=[
            {"id": "A", "name": "inspect", "args": {"nested": [1]}, "type": "tool_call"}]),
        ToolMessage(id="tool", content=[{"type": "text", "text": "evidence"}], tool_call_id="A", name="inspect",
            artifact={"original": {"identity": "tool-result"}}),
        AIMessage(id="answer", content=" done ", additional_kwargs={"signature": "original"})],
        "todos": [{"content": " inspect ", "status": "completed"}, {"content": "", "status": "pending"}],
        "run_tool_call_count": 2}
    callbacks = [{"type": "started", "name": "reader", "run_id": "child", "parent_run_id": "root"}]
    original = deepcopy(values)
    result = project_deep_agent_result(values, stage="DA-E4", subagent_events=callbacks)
    assert isinstance(result, DeepAgentRuntimeResult)
    assert result.metadata == {"harness": "deepagents", "stage": "DA-E4", "message_count": 4}
    assert result.framework_messages == messages_to_dict(values["messages"])
    assert result.todos == [{"content": "inspect", "status": "completed"}]
    assert result.output_text == "done" and result.subagent_events == callbacks
    # Returned projections must not mutate the original native/callback inputs.
    result.messages[1]["tool_calls"][0]["args"]["nested"].append(2)
    result.framework_messages[2]["data"]["artifact"]["original"]["identity"] = "changed"
    result.subagent_events[0]["name"] = "changed"
    assert values == original and callbacks[0]["name"] == "reader"


def test_waiting_projection_keeps_interrupts_and_never_reuses_previous_assistant_text():
    from src.application.deep_agents.result_projection import project_deep_agent_result

    interruption = Interrupt(id="pending", value={"action_requests": [{"name": "inspect", "args": {}}]})
    values = {"messages": [AIMessage(content="earlier answer")], "__interrupt__": [interruption]}
    result = project_deep_agent_result(values, stage="DA-E4", subagent_events=[])
    assert result.output_text == "" and result.interrupts == [{"id": "pending", "value": interruption.value}]
    assert "native_terminal" not in result.metadata
    result.interrupts[0]["value"]["action_requests"][0]["name"] = "changed"
    assert interruption.value["action_requests"][0]["name"] == "inspect"


def test_waiting_projection_cannot_be_labeled_native_terminal():
    from src.application.deep_agents.result_projection import project_deep_agent_result

    with pytest.raises(DeepAgentRuntimeError, match="interrupted result"):
        project_deep_agent_result({"messages": [AIMessage(content="old")],
            "__interrupt__": [Interrupt(id="pending", value={})]}, stage="DA-E4",
            subagent_events=[], native_terminal={"scope": "native_graph_terminal_only"})


def test_native_interrupt_identity_and_terminal_metadata_are_detached():
    from src.application.deep_agents.result_projection import project_deep_agent_result
    from src.schemas.framework_approval import NativeToolInterrupt

    native = NativeToolInterrupt(checkpoint_namespace="", source_message_id="source", tool_call_id="A",
        tool_name="inspect", arguments={"nested": [1]}, checkpoint_id="checkpoint", graph_task_id="task",
        task_path="tools", interrupt_id="pending", resume_index=0)
    pending = project_deep_agent_result({"messages": [], "__interrupt__": [Interrupt(id="pending", value={})]},
        stage="DA-E4", subagent_events=[], native_interrupts=[native])
    assert pending.native_interrupts == [native]
    pending.native_interrupts[0].arguments["nested"].append(2)
    assert native.arguments == {"nested": [1]}
    terminal = {"scope": "native_graph_terminal_only", "uncheckpointed_channels": ["run_tool_call_count"]}
    completed = project_deep_agent_result({"messages": [AIMessage(content="done")]}, stage="DA-E4",
        subagent_events=[], native_terminal=terminal)
    completed.metadata["native_terminal"]["uncheckpointed_channels"].append("changed")
    assert terminal["uncheckpointed_channels"] == ["run_tool_call_count"]


@pytest.mark.parametrize("values", [{}, {"messages": []}, {"messages": [HumanMessage(content="inspect")]}])
def test_no_completion_evidence_does_not_gain_a_result(values):
    from src.application.deep_agents.result_projection import project_deep_agent_result

    with pytest.raises(DeepAgentRuntimeError, match="no assistant message"):
        project_deep_agent_result(values, stage="DA-E1", subagent_events=[])


def test_legacy_role_dicts_remain_display_only_and_are_detached():
    from src.application.deep_agents.result_projection import project_deep_agent_result

    values = {"messages": [{"role": "assistant", "content": [{"type": "text", "text": "legacy"}]}]}
    result = project_deep_agent_result(values, stage="DA-E1", subagent_events=[])
    assert result.output_text == "legacy" and result.framework_messages == []
    result.messages[0]["content"][0]["text"] = "changed"
    assert values["messages"][0]["content"][0]["text"] == "legacy"

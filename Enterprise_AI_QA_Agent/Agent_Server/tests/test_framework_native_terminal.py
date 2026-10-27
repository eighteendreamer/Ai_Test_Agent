"""Native graph completion evidence is not application recovery permission."""
from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config, start


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def finished(h, saver):
    model = Model(responses=[AIMessage(id="terminal-answer", content="original answer")])
    agent = create_deep_agent(model=model, tools=[], checkpointer=saver)
    result = await agent.ainvoke({"messages": [HumanMessage(id="terminal-input", content="inspect")]},
        config(h), durability="sync")
    return agent, result


@pytest.mark.asyncio
async def test_reads_exact_native_terminal_twice_without_invoking_graph(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent, result = await finished(h, saver)
        from src.application.deep_agents.native_terminal import collect_native_terminal

        def forbidden(*args, **kwargs):
            raise AssertionError("Reading a terminal checkpoint must not invoke a graph/model")
        monkeypatch.setattr(agent, "ainvoke", forbidden)
        first = await collect_native_terminal(agent, saver, config(h), returned_values=result)
        second = await collect_native_terminal(agent, saver, config(h))
        assert first == second
        assert first.proof.scope == "native_graph_terminal_only"
        assert first.proof.binding_fingerprint == h.binding.fingerprint
        assert first.proof.thread_id == h.binding.thread_id and first.proof.checkpoint_namespace == ""
        assert first.proof.checkpoint_id == (await agent.aget_state(config(h))).config["configurable"]["checkpoint_id"]
        assert first.values["messages"][-1].content == "original answer"
        assert h.owner.runtime_owner.owner_token not in first.proof.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["next", "interrupts", "tasks", "missing_checkpoint", "wrong_thread", "edited_state", "returned_output", "unknown_output", "pending_writes"])
async def test_rejects_nonterminal_or_mismatched_native_facts(checkpoint_harness, monkeypatch, changed):
    from langgraph.types import Interrupt, PregelTask

    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent, result = await finished(h, saver)
        snapshot = await agent.aget_state(config(h))
        from src.application.deep_agents.native_terminal import collect_native_terminal
        from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

        selected = config(h)
        if changed == "wrong_thread":
            selected = {"configurable": {"thread_id": "foreign-thread"}}
        elif changed == "edited_state":
            await agent.aupdate_state(config(h), {"messages": [AIMessage(id="injected", content="edited")]})
        elif changed == "returned_output":
            result = {**result, "messages": [AIMessage(id="different", content="not the persisted answer")]}
        elif changed == "unknown_output":
            result = {**result, "not_a_native_channel": "unsupported"}
        elif changed == "pending_writes":
            await saver.aput_writes(snapshot.config, [("__error__", "unsettled")], "unfinished-task")
        else:
            if changed == "next":
                snapshot = snapshot._replace(next=("model",))
            elif changed == "interrupts":
                snapshot = snapshot._replace(interrupts=(Interrupt(value="approval", id="pending"),))
            elif changed == "tasks":
                snapshot = snapshot._replace(tasks=(PregelTask("pending", "model", (), "unfinished", (), None, None),))
            else:
                snapshot = snapshot._replace(config={"configurable": {"thread_id": h.binding.thread_id}})
            async def changed_state(*args, **kwargs):
                return snapshot
            monkeypatch.setattr(agent, "aget_state", changed_state)
        with pytest.raises(FrameworkMessageStageRecoveryRequired):
            await collect_native_terminal(agent, saver, selected, returned_values=result)


@pytest.mark.parametrize("changed", ["pending", "duplicate_result", "orphan_result", "invalid_call", "missing_call_id"])
def test_terminal_requires_closed_native_tool_protocol(changed):
    from src.application.deep_agents.native_terminal import require_closed_native_messages
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    call = AIMessage(id="source", content="", tool_calls=[
        {"id": "A", "name": "inspect", "args": {}, "type": "tool_call"}])
    result = ToolMessage(content="result", tool_call_id="A")
    messages = [HumanMessage(content="inspect"), call, result, AIMessage(id="answer", content="done")]
    if changed == "pending":
        messages.remove(result)
    elif changed == "duplicate_result":
        messages.insert(3, result.model_copy())
    elif changed == "orphan_result":
        messages = [HumanMessage(content="inspect"), result, AIMessage(content="done")]
    elif changed == "invalid_call":
        messages[-1] = AIMessage(content="", invalid_tool_calls=[
            {"name": "inspect", "args": "broken", "id": "B", "error": "bad args"}])
    else:
        call.tool_calls[0]["id"] = None
    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        require_closed_native_messages(messages)


def test_closed_protocol_allows_reused_call_ids_in_distinct_completed_batches():
    from src.application.deep_agents.native_terminal import require_closed_native_messages

    messages = [HumanMessage(content="inspect")]
    for source in ("first-source", "second-source"):
        messages.extend([AIMessage(id=source, content="", tool_calls=[
            {"id": "A", "name": "inspect", "args": {}, "type": "tool_call"}]),
            ToolMessage(content=source, tool_call_id="A")])
    messages.append(AIMessage(content="done"))
    require_closed_native_messages(messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("planning", [False, True])
async def test_bound_adapter_attaches_native_terminal_reference(checkpoint_harness, planning):
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest

    h = checkpoint_harness
    await start(h)
    responses = [AIMessage(id="adapter-answer", content="adapter answer")]
    if planning:
        responses.insert(0, AIMessage(id="planning-source", content="", tool_calls=[{
            "id": "planning-call", "name": "write_todos", "args": {"todos": [
                {"content": "inspect", "status": "completed"}]}, "type": "tool_call"}]))
    model = Model(responses=responses)
    async def resolve(key):
        return model
    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve, checkpointer_factory=lambda: h.provider.open(owner=h.owner.runtime_owner))
    result = await adapter.execute(DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
        trace_id="terminal-trace", model_key="fixture", system_prompt="inspect", messages=[{"role": "user", "content": "inspect"}],
        checkpoint_namespace=h.binding.checkpoint_namespace, cognitive_planning_enabled=planning))
    assert result.metadata["native_terminal"]["scope"] == "native_graph_terminal_only"
    assert result.metadata["native_terminal"]["binding_fingerprint"] == h.binding.fingerprint
    assert result.output_text == "adapter answer"
    assert bool(result.todos) == planning


@pytest.mark.asyncio
async def test_untracked_run_limits_are_explicitly_outside_terminal_state(checkpoint_harness):
    from langchain.agents.middleware import ToolCallLimitMiddleware
    from src.application.deep_agents.native_terminal import collect_native_terminal

    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent = create_deep_agent(model=Model(responses=[AIMessage(content="done")]), tools=[], checkpointer=saver,
            middleware=[ToolCallLimitMiddleware(run_limit=3)])
        result = await agent.ainvoke({"messages": [HumanMessage(content="inspect")]}, config(h), durability="sync")
        first = await collect_native_terminal(agent, saver, config(h), returned_values=result)
        second = await collect_native_terminal(agent, saver, config(h))
        assert first == second
        assert "run_tool_call_count" in first.proof.uncheckpointed_channels
        assert "run_tool_call_count" not in first.values


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_order", [("A", "B"), ("B", "A")])
async def test_real_per_call_interrupt_is_not_native_completion(checkpoint_harness, monkeypatch, decision_order):
    from langgraph.types import Command
    from tests.test_framework_approval_receipts import native_approvals
    from src.application.deep_agents.native_terminal import collect_native_terminal
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="pending tasks"):
            await collect_native_terminal(native.agent, saver, config(h))
        assert native.calls == []
        first, second = decision_order
        decisions = {"A": {"type": "reject"}, "B": {"type": "approve"}}
        await native.agent.ainvoke(Command(resume={native.tasks[first].interrupt.id: decisions[first]}), config(h), durability="sync")
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="pending tasks"):
            await collect_native_terminal(native.agent, saver, config(h))
        final = await native.agent.ainvoke(Command(resume={native.tasks[second].interrupt.id: decisions[second]}),
            config(h), durability="sync")
        proof = await collect_native_terminal(native.agent, saver, config(h), returned_values=final)
        assert proof.values["messages"][-1].content == "finished"
        assert native.calls == ["B"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["id", "signature", "metadata", "usage", "content"])
async def test_terminal_comparison_preserves_native_identity_blocks_and_metadata(checkpoint_harness, changed):
    from copy import deepcopy
    from langchain_core.messages import messages_to_dict
    from src.application.deep_agents.native_terminal import collect_native_terminal
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    h = checkpoint_harness
    await start(h)
    original = AIMessage(id="signed-answer", content=[{"type": "text", "text": "answer"}],
        additional_kwargs={"signature": "opaque-fixture-signature"},
        response_metadata={"provider_fixture": "original"},
        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent = create_deep_agent(model=Model(responses=[original]), tools=[], checkpointer=saver)
        result = await agent.ainvoke({"messages": [HumanMessage(id="input", content="inspect")]}, config(h), durability="sync")
        terminal = await collect_native_terminal(agent, saver, config(h), returned_values=result)
        assert messages_to_dict(terminal.values["messages"]) == messages_to_dict(result["messages"])
        altered = deepcopy(result)
        answer = altered["messages"][-1]
        if changed == "id":
            answer.id = "different-identity"
        elif changed == "signature":
            answer.additional_kwargs["signature"] = "different-signature"
        elif changed == "metadata":
            answer.response_metadata["provider_fixture"] = "different-metadata"
        elif changed == "usage":
            answer.usage_metadata = {"input_tokens": 1, "output_tokens": 3, "total_tokens": 4}
        else:
            answer.content[0]["text"] = "different-answer"
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="messages"):
            await collect_native_terminal(agent, saver, config(h), returned_values=altered)


@pytest.mark.asyncio
async def test_adapter_does_not_disguise_native_reconciliation_as_provider_error(checkpoint_harness):
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    h = checkpoint_harness
    await start(h)
    async def resolve(key):
        return Model(responses=[AIMessage(content="done")])
    def factory(**kwargs):
        graph = create_deep_agent(**kwargs)
        get_state = graph.aget_state
        async def unfinished(*args, **kwargs):
            return (await get_state(*args, **kwargs))._replace(next=("model",))
        graph.aget_state = unfinished
        return graph
    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve, agent_factory=factory,
        checkpointer_factory=lambda: h.provider.open(owner=h.owner.runtime_owner))
    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        await adapter.execute(DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
            trace_id="unfinished", model_key="fixture", system_prompt="inspect", messages=[{"role": "user", "content": "inspect"}],
            checkpoint_namespace=h.binding.checkpoint_namespace))

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import Command

from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config
from tests.test_framework_approval_receipts import native_approvals
from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired


@pytest.mark.asyncio
async def test_extracts_native_task_source_and_resume_identity(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        identities = await collect_pending_tool_interrupts(native.agent, saver, config(h))
    assert len(identities) == 2
    for identity in identities:
        item = native.tasks[identity.tool_call_id]
        assert identity.source_message_id == "receipt-source"
        assert identity.graph_task_id == item.task_id
        assert identity.task_path == item.task_path
        assert identity.checkpoint_id == item.config["configurable"]["checkpoint_id"]
        assert identity.checkpoint_namespace == ""
        assert identity.resume_index == 0
        assert identity.interrupt_id == item.interrupt.id
        assert identity.tool_name == "inspect_value"
        assert identity.arguments == {"value": identity.tool_call_id}


@pytest.mark.asyncio
async def test_partial_resume_preserves_remaining_identity_and_completed_task_is_absent(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        first = await collect_pending_tool_interrupts(native.agent, saver, config(h))
        a, b = (next(item for item in first if item.tool_call_id == key) for key in ("A", "B"))
        await native.agent.ainvoke(Command(resume={b.interrupt_id: {"type": "approve"}}),
            config(h), durability="sync")
        assert native.calls == ["B"]
        assert await collect_pending_tool_interrupts(native.agent, saver, config(h)) == [a]
        await native.agent.ainvoke(Command(resume={a.interrupt_id: {"type": "reject"}}),
            config(h), durability="sync")
        assert await collect_pending_tool_interrupts(native.agent, saver, config(h)) == []
        assert native.calls == ["B"]


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing_id", "wrong_call_id", "ambiguous_call", "wrong_args", "wrong_name", "older_source_only"])
async def test_corrupt_source_is_rejected_without_name_argument_fallback(checkpoint_harness, monkeypatch, corruption):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    snapshot = await native.agent.aget_state(config(h), subgraphs=True)
    messages = list(snapshot.values["messages"])
    source = messages[-1].model_copy(deep=True)
    if corruption == "missing_id":
        source.id = None
    elif corruption == "wrong_call_id":
        source.tool_calls[0]["id"] = "different"
    elif corruption == "ambiguous_call":
        source.tool_calls.append(dict(source.tool_calls[0]))
    elif corruption == "wrong_args":
        source.tool_calls[0]["args"] = {"value": "different"}
    elif corruption == "wrong_name":
        source.tool_calls[0]["name"] = "different"
    else:
        messages.append(source)
        source = AIMessage(id="later-model", content="no calls")
    messages[-1] = source

    async def corrupt_state(*args, **kwargs):
        return snapshot._replace(values={**snapshot.values, "messages": messages})

    monkeypatch.setattr(native.agent, "aget_state", corrupt_state)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="source AI"):
            await collect_pending_tool_interrupts(native.agent, saver, config(h))


async def scripted_agent(h, responses, *, nested=False):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langgraph.graph import StateGraph, MessagesState, START, END
    from tests.test_framework_checkpoint_fencing import start

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def inspect_value(value: str) -> str:
        """Return the inspected value."""
        return value

    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent = create_deep_agent(model=Model(responses=responses), tools=[inspect_value], checkpointer=saver,
            middleware=[HumanInTheLoopMiddleware(interrupt_on={"inspect_value": {
                "allowed_decisions": ["approve", "reject"]}}, interrupt_mode="per_call")])
        if nested:
            graph = StateGraph(MessagesState)
            graph.add_node("review", agent)
            graph.add_edge(START, "review")
            graph.add_edge("review", END)
            agent = graph.compile(checkpointer=saver)
        await agent.ainvoke({"messages": [{"role": "user", "content": "inspect"}]}, config(h), durability="sync")
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_same_arguments_and_reused_provider_call_ids_keep_distinct_source_identity(checkpoint_harness, nested):
    h = checkpoint_harness
    calls = [{"name": "inspect_value", "args": {"value": "same"}, "id": key, "type": "tool_call"}
        for key in ("call-1", "call-2")]
    agent = await scripted_agent(h, [AIMessage(id="round-1", content="", tool_calls=calls),
        AIMessage(id="round-2", content="", tool_calls=[calls[0]]), AIMessage(content="done")], nested=nested)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        first = await collect_pending_tool_interrupts(agent, saver, config(h))
        assert len(first) == 2 and {item.source_message_id for item in first} == {"round-1"}
        assert len({item.graph_task_id for item in first}) == 2
        assert all(bool(item.checkpoint_namespace) == nested for item in first)
        await agent.ainvoke(Command(resume={item.interrupt_id: {"type": "approve"} for item in first}),
            config(h), durability="sync")
        second = await collect_pending_tool_interrupts(agent, saver, config(h))
        assert len(second) == 1
        assert second[0].tool_call_id == "call-1" and second[0].source_message_id == "round-2"
        assert second[0].interrupt_id not in {item.interrupt_id for item in first}
        assert second[0].checkpoint_id not in {item.checkpoint_id for item in first}


@pytest.mark.asyncio
async def test_expired_owner_cannot_extract_authoritative_identity(checkpoint_harness, monkeypatch):
    from src.runtime.store import ContinuationLeaseLostError

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with pytest.raises(ContinuationLeaseLostError):
            await collect_pending_tool_interrupts(native.agent, saver, config(h))


@pytest.mark.asyncio
async def test_consumed_decision_without_task_result_requires_reconciliation(checkpoint_harness, monkeypatch):
    from tests.test_framework_approval_receipts import write_resume

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    # A durable native resume is not proof of a settled tool. Do not turn this
    # execution gap into a new approval at the next resume list position.
    await write_resume(h, native)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcil"):
            await collect_pending_tool_interrupts(native.agent, saver, config(h))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_events", [False, True])
async def test_bound_adapter_returns_native_identity_and_continues_without_new_decision(checkpoint_harness, stream_events):
    from dataclasses import replace
    from deepagents import create_deep_agent
    from src.application.deep_agents import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest
    from tests.test_framework_checkpoint_fencing import start

    h = checkpoint_harness
    await start(h)
    effects = []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def inspect_value(value: str) -> str:
        """Return a controlled adapter test value."""
        effects.append(value)
        return value

    model = Model(responses=[AIMessage(id="adapter-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": key}, "id": key, "type": "tool_call"}
        for key in ("A", "B")]), AIMessage(content="done")])

    async def resolve(key):
        return model

    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve,
        agent_factory=(create_deep_agent if stream_events else None),
        checkpointer_factory=lambda: h.provider.open(owner=h.owner.runtime_owner))
    request = DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="trace",
        model_key="model", system_prompt="test", messages=[{"role": "user", "content": "inspect"}],
        checkpoint_namespace=h.binding.checkpoint_namespace, business_tools=[inspect_value],
        interrupt_on={"inspect_value": {"allowed_decisions": ["approve", "reject"]}},
        cognitive_subagents_enabled=stream_events)
    first = await adapter.execute(request)
    assert len(first.native_interrupts) == 2 and effects == []
    a, b = (next(item for item in first.native_interrupts if item.tool_call_id == key) for key in ("A", "B"))
    second = await adapter.execute(replace(request, resume={b.interrupt_id: {"type": "approve"}}))
    assert second.native_interrupts == [a] and effects == ["B"]
    third = await adapter.execute(replace(request, continue_from_checkpoint=True))
    assert third.native_interrupts == [a] and effects == ["B"]
    final = await adapter.execute(replace(request, resume={a.interrupt_id: {"type": "reject"}}))
    assert not final.native_interrupts and not final.interrupts
    assert final.output_text == "done" and effects == ["B"]
    assert sum(item["type"] == "human" for item in final.framework_messages) == 1

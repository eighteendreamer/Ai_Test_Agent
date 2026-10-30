from __future__ import annotations

import json
from dataclasses import replace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
from tests.test_agent_task_dispatch import harness
from tests import test_framework_chat_models as providers
from tests.test_framework_model_policy import execute, governed


def _call(name, arguments, call_id):
    return {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_task_allowance_survives_rebuilt_adapter_and_two_approval_resumes(
    harness, tmp_path, monkeypatch, limit, decision,
):
    """Actual SDK/HTTP + PG ledger/checkpoints, not a paid provider or Runner."""
    original_reply = providers._reply

    def reply(transport):
        response = original_reply(transport)
        message = requests[-1]["body"]["messages"][-1]
        previous = message.get("tool_call_id")
        if message["role"] == "user" and message["content"] == "fixture request":
            call = _call("task", {"description": "first bounded inspection",
                "subagent_type": "code-review-researcher"}, "task-1")
        elif previous in {"task-1", "task-2"}:
            call = _call("inspect_value", {"value": previous}, f"approval-{previous}")
        elif previous in {"approval-task-1", "approval-task-2"}:
            number = int(previous.rsplit("-", 1)[1]) + 1
            call = _call("task", {"description": f"bounded inspection {number}",
                "subagent_type": "code-review-researcher"}, f"task-{number}")
        else:
            return response
        response["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant",
            "content": "", "tool_calls": [{"id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": json.dumps(call["args"])}}]})
        return response

    monkeypatch.setattr(providers, "_reply", reply)
    effects = []

    @tool
    def inspect_value(value: str) -> str:
        """Return a controlled approval fixture value."""
        effects.append(value)
        return value

    with providers._provider("openai_chat_completions") as (url, requests):
        async with governed(harness, providers._config("openai_chat_completions", url), max_calls=12) as h:
            request = replace(h.request, read_only_filesystem_enabled=True, cognitive_subagents_enabled=True,
                max_subagent_calls_per_turn=limit, context={"project_root": str(tmp_path)},
                business_tools=[inspect_value], interrupt_on={"inspect_value": {
                    "allowed_decisions": ["approve", "reject"]}})
            result = await execute(h, request)
            assert len(result.native_interrupts) == 1 and effects == []
            for index in range(2):
                # Reopen the actual PG checkpoint with a new provider/adapter;
                # no counter survives by virtue of a Python graph instance.
                await h.provider.close()
                h.provider = DeepAgentCheckpointProvider(h.settings)
                h.adapter = DeepAgentRuntimeAdapter(model_resolver=h.models.resolve_langchain_chat_model,
                    checkpointer_factory=h.provider.open, model_call_policy=h.policy)
                pending, = result.native_interrupts
                before = await h.calls.snapshot(h.binding)
                waiting = await execute(h, replace(request, continue_from_checkpoint=True))
                assert waiting.native_interrupts == result.native_interrupts
                assert await h.calls.snapshot(h.binding) == before
                result = await execute(h, replace(request, resume={pending.interrupt_id: {"type": decision}}))
                snapshot = await h.calls.snapshot(h.binding)
                children = [item for item in snapshot.calls if item.intent.purpose == "subagent"]
                assert len(children) == min(limit, index + 2), "Resume replenished the original Turn's task allowance"
                assert len(result.native_interrupts) == (1 if index == 0 else 0)
            assert effects == (["task-1", "task-2"] if decision == "approve" else [])
            assert result.output_text == "fixture answer"
            assert len(requests) == snapshot.reserved_calls == 6 + limit
            assert snapshot.actual_tokens == 10 * len(requests)
            assert all(item.phase == "completed" for item in snapshot.calls)
            assert len({item.intent.branch_id for item in children}) == limit


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", ["model", "tool"])
@pytest.mark.parametrize("continuation", ["approval", "checkpoint"])
async def test_named_branch_limits_survive_official_checkpoint_resume(tmp_path, limited, continuation):
    """The exact configured child middleware, with a controlled model and memory saver."""
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(responses=[AIMessage(content="", tool_calls=[_call("ls", {"path": "/"}, "ls-1")]),
        AIMessage(content="", tool_calls=[_call("ls", {"path": "/"}, "ls-2")]),
        AIMessage(content="done"), AIMessage(content="unused")])
    saver = InMemorySaver()

    def rebuild():
        options, cleanup = DeepAgentRuntimeAdapter(model_resolver=None)._configure_harness(model,
            read_only_filesystem_enabled=True, read_only_max_file_size_mb=1,
            cognitive_subagents_enabled=True, subagent_model_call_limit=(1 if limited == "model" else 6),
            subagent_tool_call_limit=(1 if limited == "tool" else 6), context={"project_root": str(tmp_path)})
        assert cleanup is None
        child = options["subagents"][0]
        extra = ([HumanInTheLoopMiddleware(interrupt_on={"ls": {
            "allowed_decisions": ["approve", "reject"]}}, interrupt_mode="per_call")]
            if continuation == "approval" else [])
        return create_agent(model=model, tools=child["tools"], checkpointer=saver,
            middleware=[*child["middleware"], *extra],
            interrupt_after=(["tools"] if continuation == "checkpoint" else None))

    config = {"configurable": {"thread_id": "original-bounded-branch"}}
    agent = rebuild()
    first = await agent.ainvoke({"messages": [HumanMessage(content="inspect")]}, config, durability="sync")
    state = await agent.aget_state(config)
    assert state.next
    assert "run_model_call_count" not in state.values and "run_tool_call_count" not in state.values
    assert state.values["thread_model_call_count"] == 1
    assert state.values["thread_tool_call_count"] == {"__all__": 1}
    resume = (Command(resume={first["__interrupt__"][0].id: {"type": "approve"}})
        if continuation == "approval" else None)
    resumed = await rebuild().ainvoke(resume, config, durability="sync")
    assert not resumed.get("__interrupt__"), "Resume granted a fresh named-branch allowance"
    assert not (await agent.aget_state(config)).next
    assert "limit" in resumed["messages"][-1].text.lower()
    assert model.i == (1 if limited == "model" else 2)
    # A distinct Turn/branch must not inherit the exhausted thread's counters.
    model.i = 0
    fresh_config = {"configurable": {"thread_id": "new-bounded-branch"}}
    fresh = rebuild()
    await fresh.ainvoke({"messages": [HumanMessage(content="inspect")]}, fresh_config, durability="sync")
    fresh_state = await fresh.aget_state(fresh_config)
    assert fresh_state.next and model.i == 1
    assert fresh_state.values["thread_model_call_count"] == 1
    assert fresh_state.values["thread_tool_call_count"] == {"__all__": 1}

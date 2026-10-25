"""Offline evidence for the framework integration plan; no project state writes.

Run with the root Python environment. Candidate mode requires the documented
temporary dependency overlay; it does not install or upgrade dependencies.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from importlib.metadata import version
from pathlib import Path


def current_contracts() -> dict:
    from langchain_core.messages import AIMessage, messages_from_dict, messages_to_dict
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Server"))
    from src.application.deep_agents.runtime_adapter import _normalize_messages

    message = AIMessage(
        id="evidence-message",
        content=[{"type": "text", "text": "synthetic fixture"}],
        additional_kwargs={"fixture_native_metadata": "preserve-me"},
        response_metadata={"fixture_provider": "offline"},
        usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    )
    restored = messages_from_dict(messages_to_dict([message]))[0]
    assert restored.model_dump() == message.model_dump()
    projected = _normalize_messages([message])[0]
    dropped = sorted(
        key for key in ("id", "additional_kwargs", "response_metadata", "usage_metadata")
        if key not in projected
    )
    assert len(dropped) == 4
    try:
        JsonPlusSerializer().dumps_typed({"_event_queue": asyncio.Queue()})
    except (TypeError, ValueError) as exc:
        queue_result = type(exc).__name__
    else:
        raise AssertionError("Queue serialization unexpectedly succeeded; revisit finding")

    async def cancellation_probe() -> bool:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def work() -> None:
            started.set()
            if release.wait(timeout=3):
                finished.set()

        task = asyncio.create_task(asyncio.to_thread(work))
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        release.set()
        for _ in range(1000):
            if finished.is_set():
                return True
            await asyncio.sleep(0.001)
        raise AssertionError("Synchronous worker did not finish within probe deadline")

    return {
        "native_roundtrip": "passed",
        "current_projection_drops": dropped,
        "queue_serialization_rejected": queue_result,
        "thread_finishes_after_await_cancelled": asyncio.run(cancellation_probe()),
    }


def candidate_contracts() -> dict:
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain_anthropic import ChatAnthropic
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.types import Command
    from psycopg_pool import ConnectionPool

    assert all(item is not None for item in (
        ChatOpenAI, ChatAnthropic, ChatGoogleGenerativeAI, PostgresSaver, ConnectionPool,
    ))

    class ToolModel(FakeMessagesListChatModel):
        calls: int = 0

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            self.calls += 1
            return super()._generate(*args, **kwargs)

    outcomes = []
    for remaining_decision, same_args in (
        ("approve", False), ("reject", False),
        ("approve", True), ("reject", True),
    ):
        executed: list[str] = []
        label_a, label_b = ("same", "same") if same_args else ("a", "b")

        @tool
        def reviewed_action(label: str) -> str:
            """Record one synthetic action in an in-memory list."""
            executed.append(label)
            return f"completed:{label}"

        saver = InMemorySaver()
        config = {"configurable": {"thread_id": f"fixture-{remaining_decision}-{same_args}"}}
        middleware = HumanInTheLoopMiddleware(
            interrupt_on={"reviewed_action": {"allowed_decisions": ["approve", "reject"]}},
            interrupt_mode="per_call",
        )

        def build(model):
            return create_deep_agent(
                model=model, tools=[reviewed_action], checkpointer=saver,
                middleware=[middleware], interrupt_on=None,
            )

        first_model = ToolModel(responses=[AIMessage(content="", tool_calls=[
            {"id": "call-a", "name": "reviewed_action", "args": {"label": label_a}},
            {"id": "call-b", "name": "reviewed_action", "args": {"label": label_b}},
        ])])
        graph = build(first_model)
        first = graph.invoke({"messages": [HumanMessage(content="offline fixture")]},
                             config, durability="sync")
        interrupts = {item.value["tool_call_id"]: item for item in first["__interrupt__"]}
        assert len(interrupts) == 2 and first_model.calls == 1 and not executed
        assert all(item.value["type"] == "tool_approval" for item in interrupts.values())
        second = graph.invoke(
            Command(resume={interrupts["call-b"].id: {"type": "approve"}}),
            config, durability="sync",
        )
        assert executed == [label_b] and first_model.calls == 1
        assert [item.id for item in second["__interrupt__"]] == [interrupts["call-a"].id]
        pending = saver.get_tuple(config).pending_writes
        # The candidate runtime persists accepted resume values as task writes.
        assert any(channel == "__resume__" for _, channel, _ in pending)
        continued = graph.invoke(None, config, durability="sync")
        assert executed == [label_b] and first_model.calls == 1
        assert [item.id for item in continued["__interrupt__"]] == [interrupts["call-a"].id]

        final_model = ToolModel(responses=[AIMessage(content="finished")])
        # Rebuild the official Harness with the same in-memory saver. This is
        # deliberately not labelled process restart or PostgreSQL recovery.
        rebuilt = build(final_model)
        decision = {"type": remaining_decision}
        if remaining_decision == "reject":
            decision["message"] = "fixture rejection"
        final = rebuilt.invoke(
            Command(resume={interrupts["call-a"].id: decision}),
            config, durability="sync",
        )
        assert executed == ([label_b, label_a] if remaining_decision == "approve" else [label_b])
        assert final_model.calls == 1 and not final.get("__interrupt__")
        results = [item for item in final["messages"] if isinstance(item, ToolMessage)]
        assert sorted(item.tool_call_id for item in results) == ["call-a", "call-b"]
        outcomes.append({"remaining_decision": remaining_decision,
                         "identical_tool_arguments": same_args, "executed": executed,
                         "model_calls": first_model.calls + final_model.calls,
                         "tool_result_ids": sorted(item.tool_call_id for item in results)})
    return {
        "versions": {name: version(name) for name in (
            "deepagents", "langchain", "langchain-core", "langgraph",
            "langchain-openai", "langchain-anthropic", "langchain-google-genai",
            "langgraph-checkpoint-postgres", "psycopg-pool",
        )},
        "official_per_call": outcomes,
        "scope": "offline fake model; in-memory saver; same-process Harness reconstruction",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--dependency-dir", type=Path)
    args = parser.parse_args()
    if args.candidate and not args.dependency_dir:
        parser.error("--candidate requires --dependency-dir to keep environments explicit")
    if args.dependency_dir:
        if not args.dependency_dir.is_dir():
            parser.error("--dependency-dir must refer to an installed dependency directory")
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    result = candidate_contracts() if args.candidate else current_contracts()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

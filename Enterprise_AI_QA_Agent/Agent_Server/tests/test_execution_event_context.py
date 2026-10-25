from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.schemas.session import ExecutionEvent
from src.runtime.streaming import format_sse
from src.runtime.execution_context import graph_event_scope
from src.runtime.execution_logging import append_graph_event
from src.runtime.control import RuntimeControlRegistry
from src.application.runtime.runtime_service import RuntimeService
from src.graph.state import AgentGraphState


def test_execution_event_serializes_harness_context_for_sse():
    event = ExecutionEvent(
        type="run_item.started",
        session_id="session-1",
        timestamp=datetime.now(timezone.utc),
        request_id="req-1",
        trace_id="trace-1",
        turn_id="turn-1",
        run_id="run-1",
        run_item_id="item-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        resource_id="browser-1",
    )
    rendered = format_sse(event)
    assert '"request_id": "req-1"' in rendered
    assert '"resource_id": "browser-1"' in rendered


@pytest.mark.asyncio
async def test_live_event_sink_stays_outside_persisted_graph_state():
    sink = asyncio.Queue()

    async def node(state):
        append_graph_event(state, "test.node", "test", "checkpoint-safe event")
        return state

    builder = StateGraph(AgentGraphState)
    builder.add_node("emit", node)
    builder.add_edge(START, "emit")
    builder.add_edge("emit", END)
    saver = InMemorySaver()
    graph = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "serializable-events"}}
    with graph_event_scope(sink):
        await graph.ainvoke({"session_id": "s", "turn_id": "t", "event_log": []}, config)
    checkpoint = await graph.aget_state(config)
    assert "_event_queue" not in checkpoint.values
    assert "_event_queue" not in AgentGraphState.__annotations__
    assert checkpoint.values["event_log"][0]["id"] == sink.get_nowait().id


@pytest.mark.asyncio
async def test_parallel_event_scopes_and_exception_cleanup_do_not_leak():
    outer, first, second = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()

    async def emit(sink, identity):
        with graph_event_scope(sink):
            await asyncio.sleep(0)
            append_graph_event({"session_id": identity}, "test.event", "test", identity)
            raise ValueError(identity)

    with graph_event_scope(outer):
        results = await asyncio.gather(emit(first, "a"), emit(second, "b"), return_exceptions=True)
        assert all(isinstance(result, ValueError) for result in results)
        append_graph_event({"session_id": "outer"}, "test.event", "test", "outer")
    append_graph_event({"session_id": "detached"}, "test.event", "test", "detached")
    assert [queue.get_nowait().session_id for queue in (first, second, outer)] == ["a", "b", "outer"]
    assert all(queue.empty() for queue in (outer, first, second))


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,private", [
    ("execute_turn", "_execute_turn"),
    ("resume_turn", "_resume_turn"),
    ("resume_after_approval", "_resume_after_approval"),
])
async def test_runtime_entry_installs_event_scope_for_execution_and_resume(entry, private):
    runtime = object.__new__(RuntimeService)
    runtime._runtime_control = RuntimeControlRegistry()
    sink = asyncio.Queue()

    async def operation(*args, **kwargs):
        state = {"session_id": "s"}
        append_graph_event(state, "test.entry", "test", entry)
        return state

    setattr(runtime, private, operation)
    args = (SimpleNamespace(id="s"), object())
    if entry == "resume_turn":
        args += ("test resume",)
    state = await getattr(runtime, entry)(*args, event_queue=sink)
    assert sink.get_nowait().id == state["event_log"][0]["id"]
    append_graph_event({"session_id": "outside"}, "test.event", "test", "outside")
    assert sink.empty()

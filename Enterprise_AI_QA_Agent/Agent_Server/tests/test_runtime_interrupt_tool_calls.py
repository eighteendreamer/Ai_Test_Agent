import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from langgraph.graph import END, START, StateGraph

from src.application.permissions.permission_service import PermissionService
from src.application.runtime.runtime_service import RuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.finalizer import build_finalizer_node
from src.graph.nodes.responder import responder
from src.graph.nodes.tool_executor import build_tool_executor_node
from src.graph.state import AgentGraphState
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.session import (
    ExecutionRequest,
    RuntimeMode,
    SessionMode,
    SessionStatus,
)
from src.schemas.tool_runtime import ToolExecutionRecord


class _ModelRuntimeStub:
    def get_model_config(self, model_key):
        return None

    def get_default_model_config(self):
        return None

    @asynccontextmanager
    async def stream_handler(self, handler):
        yield


class _BlockingFirstToolRuntime:
    def __init__(self):
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.call_ids = []

    async def execute(self, tool, call, context):
        self.call_ids.append(call.id)
        if call.id == "call-1":
            self.first_started.set()
            await self.release_first.wait()
        return ToolExecutionRecord(
            call_id=call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="completed",
            summary=f"Completed {call.id}.",
            input=call.arguments,
            output={"ok": True},
        )


def _emit_two_tool_calls(state: AgentGraphState) -> AgentGraphState:
    calls = [
        {"id": "call-1", "name": "workflow-router", "arguments": {"query": "first"}},
        {"id": "call-2", "name": "workflow-router", "arguments": {"query": "second"}},
    ]
    state["available_tool_keys"] = ["workflow-router"]
    state["model_visible_tool_keys"] = ["workflow-router"]
    state["allowed_tool_keys"] = ["workflow-router"]
    state["model_tool_calls"] = calls
    state["assistant_tool_call_message"] = {
        "role": "assistant",
        "content": "I will route both requests.",
        "tool_calls": calls,
    }
    return state


def _graph(tool_executor):
    graph = StateGraph(AgentGraphState)
    graph.add_node("model_output_stub", _emit_two_tool_calls)
    graph.add_node("tool_executor", tool_executor)
    graph.add_node("finalizer", build_finalizer_node(model_runtime_service=None))
    graph.add_node("responder", responder)
    graph.add_edge(START, "model_output_stub")
    graph.add_edge("model_output_stub", "tool_executor")
    graph.add_edge("tool_executor", "finalizer")
    graph.add_edge("finalizer", "responder")
    graph.add_edge("responder", END)
    return graph.compile()


def _session(session_id: str) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        title="Interrupt tool call regression",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=now,
        updated_at=now,
    )


def _request(session_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        turn_id="turn-p0-23",
        session_id=session_id,
        user_message="Route two requests.",
        normalized_input="Route two requests.",
        mode_key="default",
        model_key="test-model",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["operator requested stop", ""])
async def test_interrupt_between_tool_calls_skips_later_call_and_closes_model_protocol(reason):
    session_id = "session-p0-23"
    registry = ToolRegistry()
    tool_runtime = _BlockingFirstToolRuntime()
    control = RuntimeControlRegistry()
    tool_executor = build_tool_executor_node(
        registry,
        PermissionService(),
        tool_runtime,
        runtime_control=control,
    )
    runtime = RuntimeService(
        graph=_graph(tool_executor),
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=tool_runtime,
        tool_registry=registry,
        runtime_control=control,
    )

    execution = asyncio.create_task(runtime.execute_turn(_session(session_id), _request(session_id)))
    await asyncio.wait_for(tool_runtime.first_started.wait(), timeout=1)
    runtime.request_interrupt(session_id, reason)
    tool_runtime.release_first.set()
    result = await asyncio.wait_for(execution, timeout=1)

    assert tool_runtime.call_ids == ["call-1"]
    assert result.state["termination_reason"] == "interrupted"
    assert result.snapshot.stage == "interrupted"
    assert not any(event.type == "graph.execution_completed" for event in result.events)

    assistant_call_ids = [
        call["id"] for call in result.state["assistant_tool_call_message"]["tool_calls"]
    ]
    tool_message_ids = [message["tool_call_id"] for message in result.state["tool_messages"]]
    assert tool_message_ids == assistant_call_ids == ["call-1", "call-2"]
    assert [
        message["tool_call_id"]
        for message in result.snapshot.graph_state["runtime_messages"]
        if message.get("role") == "tool"
    ] == assistant_call_ids
    assert [
        message["tool_call_id"] for message in result.snapshot.graph_state["tool_messages"]
    ] == assistant_call_ids
    assert [
        message["tool_call_id"] for message in result.pending_turn["tool_messages"]
    ] == assistant_call_ids
    assert [
        message["tool_call_id"]
        for message in result.pending_turn["runtime_messages"]
        if message.get("role") == "tool"
    ] == assistant_call_ids

    skipped = next(item for item in result.state["tool_results"] if item["call_id"] == "call-2")
    assert skipped["status"] == "failed"
    assert skipped["output"]["error"] == "tool_call_interrupted"
    assert any(
        event.type == "tool.execution_skipped"
        and event.payload["call_id"] == "call-2"
        for event in result.events
    )
    # The durable snapshot retains the reason; process control only owns the
    # invocation that has now returned, not the session's historical state.
    assert result.state["interrupt_reason"] == reason
    assert control.is_interrupt_requested(session_id) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["execute_turn", "resume_turn", "resume_after_approval"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
async def test_runtime_control_scope_releases_all_entry_points(monkeypatch, entry, outcome):
    """Controlled entry-point contract; not a model/provider failure reproduction."""
    control = RuntimeControlRegistry()
    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(),
                             tool_runtime_service=object(), tool_registry=ToolRegistry(),
                             runtime_control=control)
    session = _session("scope-entry")
    sentinel = object()
    handler, events = object(), object()

    async def execute(*args, **kwargs):
        assert kwargs == {"on_model_chunk": handler, "event_queue": events}
        runtime.request_interrupt(session.id, "scope interrupt")
        await asyncio.sleep(0)
        assert control.get_interrupt_reason(session.id) == "scope interrupt"
        if outcome == "error":
            raise RuntimeError("controlled lifecycle fixture")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return sentinel

    monkeypatch.setattr(runtime, "_" + entry, execute)
    arguments = [session, _request(session.id)]
    if entry == "resume_turn":
        arguments = [session, object(), "contract resume"]
    elif entry == "resume_after_approval":
        arguments = [session, {}]
    invocation = getattr(runtime, entry)(*arguments, on_model_chunk=handler, event_queue=events)
    if outcome == "success":
        assert await invocation is sentinel
    else:
        error = RuntimeError if outcome == "error" else asyncio.CancelledError
        with pytest.raises(error):
            await invocation
    assert control._execution_owners == {}
    assert control._interrupt_reasons == {}
    # A delayed stop after the invocation returned cannot retain historical
    # state or become a stop request for the next execution.
    runtime.request_interrupt(session.id, "delayed request")
    assert not control.is_interrupt_requested(session.id)


def test_overlapping_control_owner_cannot_clear_running_execution():
    control = RuntimeControlRegistry()
    with control.execution_scope("session"):
        control.request_interrupt("session", "first owner's request")
        with pytest.raises(RuntimeError, match="active runtime execution"), control.execution_scope("session"):
            pass
        assert control.get_interrupt_reason("session") == "first owner's request"
    with control.execution_scope("session"):
        assert not control.is_interrupt_requested("session")
    assert control._execution_owners == {}

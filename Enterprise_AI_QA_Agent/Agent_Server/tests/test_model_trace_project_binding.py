from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.application.runtime.runtime_service import RuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.model_config import ModelInvocationResult
from src.schemas.session import (
    ExecutionRequest,
    RuntimeMode,
    SessionMode,
    SessionStatus,
)
from src.schemas.tool_runtime import ModelToolCall


class _ModelRuntime:
    def __init__(self) -> None:
        self.request = None
        self.requests = []

    def get_model_config(self, model_key):
        return None

    def get_default_model_config(self):
        return None

    async def invoke(self, model_key, request):
        self.request = request
        self.requests.append(request)
        call_id = f"trace-tool-call-{len(self.requests)}"
        return ModelInvocationResult(
            text="ok",
            tool_calls=[ModelToolCall(id=call_id, name="fixture_tool", arguments={})],
            response_summary={
                "logical_call_id": request.trace_context["logical_call_id"],
                "http_request_attempt_count": 1,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_project_id", ["project-bound", None])
@pytest.mark.parametrize("restored", [False, True])
async def test_model_trace_uses_session_project_binding(
    bound_project_id: str | None, restored: bool
) -> None:
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-project-trace",
        title="Project trace",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        project_id=bound_project_id,
        created_at=now,
        updated_at=now,
    )
    request = ExecutionRequest(
        turn_id="turn-project-trace",
        trace_id="trace-project-trace",
        session_id=session.id,
        user_message="test",
        normalized_input="test",
        mode_key="default",
        model_key="fake-model",
        context={"project_id": "project-from-request"},
    )
    model_runtime = _ModelRuntime()
    tool_registry = ToolRegistry()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=model_runtime,
        tool_runtime_service=None,
        tool_registry=tool_registry,
        runtime_control=RuntimeControlRegistry(),
    )

    if restored:
        state = runtime._state_from_pending_turn(
            session,
            {
                "turn_id": request.turn_id,
                "trace_id": request.trace_id,
                "selected_model_key": request.model_key,
                "context_bundle": dict(request.context),
            },
        )
    else:
        state = runtime._build_initial_state(session, request)
    state["system_prompt"] = "Follow the session's project binding."
    model_invoker = build_model_invoker_node(model_runtime, tool_registry)
    await model_invoker(state)
    first_call_id = model_runtime.requests[0].trace_context["logical_call_id"]
    state["runtime_messages"].extend([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "trace-tool-call-1", "type": "function", "function": {
                "name": "fixture_tool", "arguments": "{}"
            }}
        ]},
        {"role": "tool", "tool_call_id": "trace-tool-call-1", "content": "fixture tool result"},
    ])
    state["loop_iteration"] += 1
    await model_invoker(state)

    assert state["context_bundle"]["project_id"] == bound_project_id
    assert state["trace_id"] == request.trace_id
    assert len(model_runtime.requests) == 2
    assert all(item.trace_context["project_id"] == (bound_project_id or "")
        for item in model_runtime.requests)
    assert all(item.trace_context["turn_id"] == request.turn_id
        for item in model_runtime.requests)
    assert all(item.trace_context["trace_id"] == request.trace_id
        for item in model_runtime.requests)
    assert model_runtime.requests[0].trace_context["logical_call_id"] != model_runtime.requests[1].trace_context["logical_call_id"]
    assert model_runtime.requests[1].messages[-1]["tool_call_id"] == "trace-tool-call-1"
    response_events = [event for event in state["event_log"] if event["type"] == "model.response_received"]
    assert len(response_events) == 2
    assert response_events[0]["payload"]["tool_call_ids"] == ["trace-tool-call-1"]
    assert response_events[1]["payload"]["tool_call_ids"] == ["trace-tool-call-2"]
    assert response_events[0]["payload"]["logical_call_id"] == first_call_id
    assert all(event["payload"]["trace_id"] == request.trace_id for event in response_events)
    assert all(event["payload"]["turn_id"] == request.turn_id for event in response_events)
    assert request.context["project_id"] == "project-from-request"

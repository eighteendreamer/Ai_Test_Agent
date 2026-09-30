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


class _ModelRuntime:
    def __init__(self) -> None:
        self.request = None

    def get_model_config(self, model_key):
        return None

    def get_default_model_config(self):
        return None

    async def invoke(self, model_key, request):
        self.request = request
        return ModelInvocationResult(text="ok", response_summary={})


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
                "selected_model_key": request.model_key,
                "context_bundle": dict(request.context),
            },
        )
    else:
        state = runtime._build_initial_state(session, request)
    state["system_prompt"] = "Follow the session's project binding."
    await build_model_invoker_node(model_runtime, tool_registry)(state)

    assert state["context_bundle"]["project_id"] == bound_project_id
    assert model_runtime.request.trace_context["project_id"] == (bound_project_id or "")
    assert request.context["project_id"] == "project-from-request"

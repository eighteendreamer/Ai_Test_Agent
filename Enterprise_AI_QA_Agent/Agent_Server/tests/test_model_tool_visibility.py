from __future__ import annotations

import pytest

from src.application.permissions.permission_service import PermissionService
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.graph.nodes.permission_gate import build_permission_gate
from src.registry.tools import ToolRegistry
from src.schemas.model_config import ModelInvocationResult


class RecordingModelRuntime:
    def __init__(self):
        self.request = None

    async def invoke(self, model_key, request):
        self.request = request
        return ModelInvocationResult(text="ok", response_summary={})


def _state(available_tool_keys: list[str]) -> dict:
    return {
        "session_id": "visibility-session",
        "turn_id": "visibility-turn",
        "trace_id": "visibility-trace",
        "mode_key": "api_testing",
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "selected_agent_key": "api-testing-agent",
        "selected_agent_name": "API Testing Agent",
        "selected_model_key": "fake-model",
        "selected_model_name": "Fake Model",
        "selected_model_provider": "fake",
        "available_tool_keys": available_tool_keys,
        "context_bundle": {},
        "event_log": [],
        "loop_iteration": 0,
        "system_prompt": "Follow the tool permissions.",
        "runtime_messages": [{"role": "user", "content": "Run API tests."}],
    }


@pytest.mark.asyncio
async def test_permission_gate_hidden_tools_do_not_reappear_in_model_request():
    registry = ToolRegistry()
    state = build_permission_gate(PermissionService(), registry)(
        _state(["perf-container-manager"])
    )
    assert state["denied_tool_keys"] == ["perf-container-manager"]
    assert state["model_visible_tool_keys"] == []

    model_runtime = RecordingModelRuntime()
    await build_model_invoker_node(model_runtime, registry)(state)

    assert model_runtime.request.tools == []
    assert state["model_request_payload"]["tools"] == []


@pytest.mark.asyncio
async def test_permission_gate_only_sends_visible_tools_to_model():
    registry = ToolRegistry()
    state = build_permission_gate(PermissionService(), registry)(
        _state(["perf-container-manager", "api-docs-library"])
    )
    assert state["model_visible_tool_keys"] == ["api-docs-library"]

    model_runtime = RecordingModelRuntime()
    await build_model_invoker_node(model_runtime, registry)(state)

    assert [tool["name"] for tool in model_runtime.request.tools] == ["api-docs-library"]

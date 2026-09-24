import asyncio

from src.application.permissions.permission_service import PermissionService
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.core.safety_gate import SafetyGate, SafetyVerdict
from src.graph.nodes.tool_executor import _resolve_tool_call, build_tool_executor_node
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


class _RecordingRuntime:
    def __init__(self):
        self.calls = []

    async def execute(self, tool, tool_call, context):
        self.calls.append(tool_call)
        return ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="completed",
            summary="executed",
            input=tool_call.arguments,
            output={"ok": True},
        )


class _FixedGate:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    async def evaluate(self, tool_key, tool_args, ctx):
        self.calls.append((tool_key, tool_args, ctx))
        return self.verdict


def _state(tool_key, arguments, mode_key="default"):
    return {
        "session_id": "safety-session",
        "turn_id": "safety-turn",
        "trace_id": "safety-trace",
        "user_message": "请执行工具",
        "normalized_input": "请执行工具",
        "mode_key": mode_key,
        "session_mode": "normal",
        "selected_agent_key": "general-agent",
        "selected_model_key": "test-model",
        "context_bundle": {},
        "available_tool_keys": [tool_key],
        "approval_required_tool_keys": [],
        "denied_tool_keys": [],
        "permission_decisions": [],
        "model_tool_calls": [{"id": "call-1", "name": tool_key, "arguments": arguments}],
        "tool_messages": [],
        "tool_results": [],
        "runtime_messages": [],
        "assistant_tool_call_message": {"role": "assistant", "tool_calls": []},
        "event_log": [],
        "loop_iteration": 0,
    }


def _resolve(state, gate, runtime):
    raw_call = state["model_tool_calls"][0]
    context = ToolExecutionContext(
        session_id=state["session_id"],
        turn_id=state["turn_id"],
        trace_id=state["trace_id"],
        user_message=state["user_message"],
        normalized_input=state["normalized_input"],
        context_bundle=state["context_bundle"],
        selected_agent_key=state["selected_agent_key"],
        selected_model_key=state["selected_model_key"],
    )
    return asyncio.run(
        _resolve_tool_call(
            state=state,
            tool_call=ModelToolCall.model_validate(raw_call),
            tool_registry=ToolRegistry(),
            permission_service=PermissionService(),
            tool_runtime_service=runtime,
            tool_job_service=None,
            tool_context=context,
            safety_gate=gate,
        )
    )


def test_safety_gate_denial_returns_tool_result_and_never_executes():
    state = _state("cli-executor", {"command": "rm -rf /"})
    runtime = _RecordingRuntime()
    node = build_tool_executor_node(
        ToolRegistry(), PermissionService(), runtime, safety_gate=SafetyGate()
    )

    result = asyncio.run(node(state))

    assert result["tool_results"][0]["status"] == "denied"
    assert result["tool_results"][0]["output"]["error"] == "safety_gate_denied"
    assert runtime.calls == []
    assert any(event["type"] == "safety.tool_denied" for event in result["event_log"])
    assert result["runtime_messages"][-1]["tool_call_id"] == "call-1"


def test_safety_gate_allow_keeps_safe_tool_execution():
    state = _state("perf-engine-select", {"scenario": "smoke"}, mode_key="performance_testing")
    runtime = _RecordingRuntime()
    gate = _FixedGate(SafetyVerdict.allow())
    result = _resolve(state, gate, runtime)

    assert result["tool_result"]["status"] == "completed"
    assert len(runtime.calls) == 1
    assert gate.calls[0][0] == "perf-engine-select"


def test_safety_gate_approval_uses_existing_approval_flow():
    state = _state("mock-target-runner", {"action": "start"}, mode_key="performance_testing")
    runtime = _RecordingRuntime()
    gate = _FixedGate(SafetyVerdict.require_approval("Needs operator review"))
    result = _resolve(state, gate, runtime)

    assert result["tool_result"]["status"] == "waiting_approval"
    assert result["approval"]["metadata"]["permission_source"] == "safety_gate"
    assert runtime.calls == []


def test_read_only_document_search_does_not_treat_search_text_as_a_command():
    arguments = {"action": "search", "query": "DROP TABLE in an API example"}
    state = _state("api-docs-library", arguments)
    runtime = _RecordingRuntime()
    result = _resolve(state, SafetyGate(), runtime)

    assert result["tool_result"]["status"] == "completed"
    assert len(runtime.calls) == 1


def test_mock_response_text_is_not_treated_as_executable_command():
    arguments = {"action": "start", "response_text": "DROP TABLE and execute(input) are example strings"}
    state = _state("mock-target-runner", arguments, mode_key="performance_testing")
    runtime = _RecordingRuntime()
    result = _resolve(state, SafetyGate(), runtime)

    assert result["tool_result"]["status"] == "completed"
    assert len(runtime.calls) == 1

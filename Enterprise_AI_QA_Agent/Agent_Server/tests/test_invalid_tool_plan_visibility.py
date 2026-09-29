from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.runtime.tool_input_validation import validate_tool_input
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.application.security.execution_environment_service import (
    SecurityCommandExecutionResult,
)
from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.core.config import Settings
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="invalid-plan-session",
        turn_id="invalid-plan-turn",
        trace_id="invalid-plan-trace",
        user_message="Run the requested test",
        normalized_input="Run the requested test",
        context_bundle={"environment": "testing", "risk_tolerance": "medium"},
    )


@pytest.mark.asyncio
async def test_invalid_performance_plan_does_not_fall_back_to_successful_engine_selection() -> None:
    runtime = ToolRuntimeService(settings=Settings())
    tool = ToolRegistry().get("performance-engine-runner")
    arguments = {"engine": "k6", "plan": {"engine": "not-an-engine"}}
    assert validate_tool_input(tool.input_schema, arguments) == []

    result = await runtime.execute(
        tool,
        ModelToolCall(
            id="invalid-perf-plan",
            name=tool.key,
            arguments=arguments,
        ),
        _context(),
    )

    assert result.status == "failed"
    assert result.output["error"] == "invalid_perf_plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "expected_engine"),
    [
        ({"engine": "k6"}, "k6"),
        ({"engine": "jmeter", "plan": {}}, "jmeter"),
        ({"engine": "k6", "plan": {"engine": "jmeter"}}, "jmeter"),
    ],
)
async def test_performance_engine_selection_keeps_valid_inputs(arguments, expected_engine) -> None:
    runtime = ToolRuntimeService(settings=Settings())
    tool = ToolRegistry().get("performance-engine-runner")

    result = await runtime.execute(
        tool,
        ModelToolCall(id="valid-perf-plan", name=tool.key, arguments=arguments),
        _context(),
    )

    assert result.status == "completed"
    assert result.output["engine"] == expected_engine


@pytest.mark.asyncio
async def test_invalid_security_task_cannot_fall_back_to_top_level_target(monkeypatch, tmp_path) -> None:
    runtime = ToolRuntimeService(settings=Settings(security_runner_tool_bootstrap=False))
    calls = []

    async def capture_execution(**kwargs):
        calls.append(kwargs)
        now = datetime.now(UTC)
        return SecurityCommandExecutionResult(
            backend="captured", command=kwargs["command"], argv=kwargs["command_args"],
            cwd=str(tmp_path), stdout="", stderr="", exit_code=0, timed_out=False,
            started_at=now, completed_at=now,
        )

    monkeypatch.setattr(runtime._security_execution_environment, "execute", capture_execution)
    monkeypatch.setattr(runtime, "_prepare_local_artifact_dir", lambda *_: tmp_path)
    tool = ToolRegistry().get("web-scan-runner")
    arguments = {
        "worker_action": "execute_security_task",
        "task": {"command_profile": "http_headers_probe", "target": "https://assigned.example.test"},
        "command_profile": "http_headers_probe",
        "target": "https://other.example.test",
    }
    assert validate_tool_input(tool.input_schema, arguments) == []
    assert ExecutionSafetyPolicy().evaluate_tool_call(
        tool=tool,
        arguments=arguments,
        active_mode_key="security_testing",
        context={"environment": "testing", "safety_assessment": {"decision": "allow"}},
    ).behavior != "deny"

    result = await runtime.execute(
        tool,
        ModelToolCall(
            id="invalid-security-task",
            name=tool.key,
            arguments=arguments,
        ),
        _context(),
    )

    assert result.status == "failed"
    assert calls == []
    assert result.output["error"] == "invalid_security_task"

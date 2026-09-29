from __future__ import annotations

import pytest
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_route"),
    [
        ("build test plan", "qa-planner"),
        ("clinical report", "report-analyst"),
        ("capital report", "report-analyst"),
        ("Open the UI page", "ui-executor"),
        ("run CLI command", "ops-executor"),
        ("verify API response", "api-verifier"),
        ("查看页面测试", "ui-executor"),
        ("生成测试用例", "qa-planner"),
    ],
)
async def test_workflow_router_matches_keywords_as_words(query: str, expected_route: str) -> None:
    runtime = ToolRuntimeService()
    context = ToolExecutionContext(
        session_id="session-route", turn_id="turn-route", trace_id="trace-route",
        user_message=query, normalized_input=query, context_bundle={},
    )

    result = await runtime.execute(
        ToolRegistry().get("workflow-router"),
        ModelToolCall(id="call-route", name="workflow-router", arguments={"query": query}),
        context,
    )

    assert result.status == "completed"
    assert result.output["route"] == expected_route

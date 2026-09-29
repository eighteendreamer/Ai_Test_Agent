from __future__ import annotations

from copy import deepcopy

import pytest

from src.application.runtime.tool_input_validation import validate_tool_input
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.core.config import Settings
from src.modes.smoke_testing_mode.contracts import SmokeCaseResult
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall


def _context(*, approved: bool = False, case_run_item_id: str = "") -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="smoke-plan-session",
        turn_id="smoke-plan-turn",
        trace_id="smoke-plan-trace",
        user_message="Run the reviewed smoke plan",
        normalized_input="Run the reviewed smoke plan",
        context_bundle={"run_item_id": case_run_item_id} if case_run_item_id else {},
        server_approval_granted=approved,
        trusted_case_run_item_id=case_run_item_id,
    )


async def _run(runtime, arguments, context):
    tool = ToolRegistry().get("smoke-suite-runner")
    assert validate_tool_input(tool.input_schema, arguments) == []
    return await runtime.execute(
        tool,
        ModelToolCall(id="smoke-plan-call", name=tool.key, arguments=arguments),
        context,
    )


@pytest.fixture
def smoke_runtime(monkeypatch):
    runtime = ToolRuntimeService(settings=Settings())
    smoke = runtime._smoke_testing_mode_runtime
    saved_plans = {}
    executed_urls = []

    async def initialize():
        return None

    async def resolve_project(**kwargs):
        return "reviewed-project", [], []

    async def resolve_sources(**kwargs):
        return {"warnings": [], "sources": [], "project_cases": [], "api_documents": []}

    async def save_plan_version(plan, **kwargs):
        saved_plans[plan.plan_id] = plan.model_copy(deep=True)
        return plan, []

    async def load_plan(plan_id):
        plan = saved_plans.get(plan_id)
        return (plan.model_copy(deep=True) if plan else None), []

    async def save_approved_plan(plan, **kwargs):
        return plan, []

    async def save_run_result(*, result, **kwargs):
        return result, []

    async def execute_case(case, plan, **kwargs):
        executed_urls.append(case.steps[0].api.url)
        return SmokeCaseResult(
            case_id=case.case_id,
            title=case.title,
            case_type=case.case_type,
            status="passed",
            summary="Captured at the external HTTP boundary.",
        )

    monkeypatch.setattr(smoke._plan_store, "initialize", initialize)
    monkeypatch.setattr(smoke._project_resolver, "resolve", resolve_project)
    monkeypatch.setattr(smoke._source_resolver, "resolve", resolve_sources)
    monkeypatch.setattr(smoke._plan_store, "save_plan_version", save_plan_version)
    monkeypatch.setattr(smoke._plan_store, "load_plan", load_plan)
    monkeypatch.setattr(smoke._plan_store, "save_approved_plan", save_approved_plan)
    monkeypatch.setattr(smoke._plan_store, "save_run_result", save_run_result)
    monkeypatch.setattr(smoke, "_execute_case", execute_case)
    return runtime, saved_plans, executed_urls


@pytest.mark.asyncio
async def test_model_supplied_inline_plan_cannot_replace_reviewed_smoke_plan(smoke_runtime):
    runtime, saved_plans, executed_urls = smoke_runtime
    draft = await _run(
        runtime,
        {"action": "draft_plan", "target_url": "http://127.0.0.1:8801"},
        _context(),
    )
    assert draft.output["phase"] == "awaiting_user_confirmation"
    plan = deepcopy(draft.output["plan"])
    plan_id = plan["plan_id"]
    assert saved_plans[plan_id].cases[0].steps[0].api.url == "http://127.0.0.1:8801"
    plan["target_url"] = "http://127.0.0.1:8802"
    plan["cases"][0]["steps"][0]["api"]["url"] = "http://127.0.0.1:8802"

    result = await _run(
        runtime,
        {
            "action": "execute_approved_plan",
            "plan_id": plan_id,
            "plan_version": draft.output["plan_version"],
            "plan": plan,
            "selected_case_ids": [plan["cases"][0]["case_id"]],
        },
        _context(approved=True),
    )

    assert result.status == "failed"
    assert result.output["error"] == "inline_plan_not_allowed"
    assert executed_urls == []


@pytest.mark.asyncio
async def test_reviewed_smoke_plan_executes_only_at_approved_version(smoke_runtime):
    runtime, _saved_plans, executed_urls = smoke_runtime
    draft = await _run(
        runtime,
        {"action": "draft_plan", "target_url": "http://127.0.0.1:8801"},
        _context(),
    )
    plan_id = draft.output["plan_id"]
    case_id = draft.output["plan"]["cases"][0]["case_id"]
    arguments = {
        "action": "execute_approved_plan",
        "plan_id": plan_id,
        "plan_version": draft.output["plan_version"],
        "selected_case_ids": [case_id],
    }

    without_approval = await _run(runtime, arguments, _context())
    wrong_version = await _run(
        runtime,
        {**arguments, "plan_version": arguments["plan_version"] + 1},
        _context(approved=True),
    )
    valid = await _run(runtime, arguments, _context(approved=True))

    assert without_approval.output["error"] == "approval_required"
    assert wrong_version.output["error"] == "plan_version_mismatch"
    assert valid.output["plan_id"] == plan_id
    assert executed_urls == ["http://127.0.0.1:8801"]


@pytest.mark.asyncio
async def test_smoke_run_waits_when_approved_plan_cannot_be_frozen(smoke_runtime, monkeypatch):
    runtime, _saved_plans, executed_urls = smoke_runtime
    draft = await _run(
        runtime,
        {"action": "draft_plan", "target_url": "http://127.0.0.1:8801"},
        _context(),
    )
    smoke = runtime._smoke_testing_mode_runtime

    async def fail_freeze(plan, **kwargs):
        return plan, ["fixture storage unavailable"]

    monkeypatch.setattr(smoke._plan_store, "save_approved_plan", fail_freeze)
    result = await _run(
        runtime,
        {
            "action": "execute_approved_plan",
            "plan_id": draft.output["plan_id"],
            "plan_version": draft.output["plan_version"],
        },
        _context(approved=True),
    )

    assert result.status == "failed"
    assert result.output["error"] == "plan_freeze_failed"
    assert executed_urls == []


@pytest.mark.asyncio
async def test_trusted_case_run_keeps_versioned_inline_plan_path(smoke_runtime):
    runtime, _saved_plans, executed_urls = smoke_runtime
    draft = await _run(
        runtime,
        {"action": "draft_plan", "target_url": "http://127.0.0.1:8801"},
        _context(),
    )
    plan = deepcopy(draft.output["plan"])
    result = await _run(
        runtime,
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": [plan["cases"][0]["case_id"]],
        },
        _context(case_run_item_id="frozen-item-1"),
    )

    assert result.status == "completed", result.output
    assert result.output["plan_id"] == plan["plan_id"]
    assert executed_urls == ["http://127.0.0.1:8801"]

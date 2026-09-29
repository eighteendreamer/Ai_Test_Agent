from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.modes.smoke_testing_mode.contracts import SmokeExecutionPlan
from src.modes.smoke_testing_mode.runtime import SmokeTestingModeRuntime


@pytest.mark.asyncio
async def test_smoke_runtime_retries_failed_catalog_init_without_repeating_old_warning():
    runtime = SmokeTestingModeRuntime()
    attempts = 0

    async def initialize():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("catalog unavailable")

    async def resolve_project(**kwargs):
        return "fixture-project", [], []

    async def resolve_sources(**kwargs):
        return {"warnings": []}

    async def save_plan_version(plan):
        return plan, []

    runtime._plan_store.initialize = initialize
    runtime._project_resolver.resolve = resolve_project
    runtime._source_resolver.resolve = resolve_sources
    runtime._plan_store.save_plan_version = save_plan_version
    runtime._build_plan = lambda **kwargs: SmokeExecutionPlan(project_scope="fixture-project")
    first_context = SimpleNamespace(session_id="session-a", trace_id="trace-a", context_bundle={})
    second_context = SimpleNamespace(session_id="session-b", trace_id="trace-b", context_bundle={})

    first = await runtime.handle({"action": "draft_plan"}, first_context)
    second = await runtime.handle({"action": "draft_plan"}, second_context)

    assert first["status"] == "partial"
    assert any("catalog unavailable" in item for item in first["storage_warnings"])
    assert attempts == 2
    assert second["storage_warnings"] == []

from __future__ import annotations

import pytest

from src.modes.performance_testing_mode.coordinator import PerfSubagentCoordinator
from src.modes.performance_testing_mode.contracts import TASK_COMPLETED, TASK_FAILED
from src.modes.performance_testing_mode.task_pool import PerfTask, PerfTaskPool


class _FakeCoordinatorRuntimeService:
    def __init__(self, results: dict[str, dict]) -> None:
        self.results = results
        self.calls: list[dict] = []
        self.events: list[dict] = []

    async def dispatch_worker(self, agent_key: str, prompt: str, description: str, context: dict):
        self.calls.append({
            "agent_key": agent_key,
            "prompt": prompt,
            "description": description,
            "context": context,
        })
        return self.results.get(context["task_id"], {"ok": True, "summary": "ok"})

    async def emit_event(self, event_type: str, session_id: str, data: dict):
        self.events.append({"event_type": event_type, "session_id": session_id, "data": data})


@pytest.mark.asyncio
async def test_perf_subagent_coordinator_runs_dependency_order():
    pool = PerfTaskPool()
    pool.add_task(PerfTask(task_id="script", task_type="script_generation", agent_key="perf-script-builder"))
    pool.add_task(PerfTask(
        task_id="analysis",
        task_type="analysis",
        agent_key="perf-analyst",
        depends_on=["script"],
    ))
    service = _FakeCoordinatorRuntimeService({
        "script": {"ok": True, "summary": "script ok"},
        "analysis": {"ok": True, "summary": "analysis ok"},
    })
    coordinator = PerfSubagentCoordinator(pool, service, session_id="sess-1", max_workers=2)

    await coordinator.run_all()

    assert [call["context"]["task_id"] for call in service.calls] == ["script", "analysis"]
    assert pool.get("script").status == TASK_COMPLETED
    assert pool.get("analysis").status == TASK_COMPLETED
    assert service.events


@pytest.mark.asyncio
async def test_perf_subagent_coordinator_retries_failed_task_once():
    pool = PerfTaskPool()
    pool.add_task(PerfTask(
        task_id="run",
        task_type="execution",
        agent_key="perf-runner",
        max_retries=1,
    ))
    service = _FakeCoordinatorRuntimeService({"run": {"ok": False, "summary": "timeout"}})
    coordinator = PerfSubagentCoordinator(pool, service, session_id="sess-1", max_workers=1)

    await coordinator.run_all()

    assert len(service.calls) == 2
    assert pool.get("run").status == TASK_FAILED
    assert pool.get("run").retries == 1

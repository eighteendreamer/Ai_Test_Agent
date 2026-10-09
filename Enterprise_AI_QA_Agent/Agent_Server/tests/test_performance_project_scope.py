"""Scope restoration from the actual cross-project reproduction."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.core.request_context import bind_request_context
from src.modes.performance_testing_mode.contracts import PHASE_RESULT_COLLECTED, STATE_METADATA_KEY
from src.modes.performance_testing_mode.plan_state import PerformanceTestingState
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime

DOCS = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def context(project=None):
    captured = json.loads((DOCS / "p5-jmeter-report-pg-before/pg-rejected-context.json").read_text(encoding="utf-8"))
    captured[STATE_METADATA_KEY]["phase"] = PHASE_RESULT_COLLECTED
    captured["project_id"] = project
    return SimpleNamespace(context_bundle=captured, session_id="scope", trace_id="scope", turn_id="scope")


class Store:
    def __init__(self):
        self.reads, self.writes = [], []

    async def get_baseline(self, target_url, *, project_id=None):
        self.reads.append((target_url, project_id))
        return None

    async def save_run(self, **kwargs):
        self.writes.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_project_is_captured_and_given_to_both_store_operations():
    captured = json.loads((DOCS / "p5-perf-project-before/facts.json").read_text(encoding="utf-8"))
    project = captured["project_b"]
    runtime, store = PerformanceTestingModeRuntime(), Store()
    runtime._metrics_store = store
    ctx = context(project)
    result = await runtime.handle({}, ctx)
    assert result["status"] == "completed" and result["baseline_comparison"] is None
    assert [p for _, p in store.reads] == [project]
    assert [w["project_id"] for w in store.writes] == [project]
    assert result["project_id"] == project
    assert ctx.context_bundle[STATE_METADATA_KEY]["project_id"] == project
    payload = json.loads(next(a["content"] for a in result["artifacts"] if a["type"] == "performance_metrics_json"))
    assert payload["project_id"] == project


@pytest.mark.asyncio
async def test_restore_rejects_different_project_without_changing_checkpoint():
    captured = json.loads((DOCS / "p5-perf-project-before/facts.json").read_text(encoding="utf-8"))
    ctx = context(captured["project_b"])
    ctx.context_bundle[STATE_METADATA_KEY]["project_id"] = captured["project_a"]
    before = copy.deepcopy(ctx.context_bundle[STATE_METADATA_KEY])
    runtime, store = PerformanceTestingModeRuntime(), Store()
    runtime._metrics_store = store
    result = await runtime.handle({}, ctx)
    assert result["status"] == "denied" and result["phase"] == PHASE_RESULT_COLLECTED
    assert not store.reads and not store.writes
    assert ctx.context_bundle[STATE_METADATA_KEY] == before


@pytest.mark.asyncio
async def test_unscoped_state_does_not_borrow_ambient_project():
    runtime, store = PerformanceTestingModeRuntime(), Store()
    runtime._metrics_store = store
    ctx = context()
    with bind_request_context(project_id="unrelated-parent-project"):
        result = await runtime.handle({}, ctx)
    assert result["status"] == "completed" and result["project_id"] is None
    assert all(project is None for _, project in store.reads)
    assert store.writes[0]["project_id"] is None
    assert "project_id" not in ctx.context_bundle[STATE_METADATA_KEY]


def test_legacy_state_serialization_omits_new_unset_project():
    raw = context().context_bundle[STATE_METADATA_KEY]
    assert PerformanceTestingState.model_validate(raw).model_dump() == raw


@pytest.mark.asyncio
async def test_unscoped_reads_never_initialize_or_query_global_store():
    settings = SimpleNamespace(database=SimpleNamespace(postgres_perf_runs_table="unused"))
    store = PerfMetricsStore(settings)

    async def unexpected():
        raise AssertionError("Unscoped read must not inspect the database")

    store.initialize = unexpected
    assert await store.get_baseline("http://owned") is None
    assert await store.list_runs() == []

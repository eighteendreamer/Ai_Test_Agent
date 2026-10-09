"""Report-only recovery from the actual PostgreSQL rejection snapshot."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.modes.performance_testing_mode.contracts import PHASE_ANALYZED, STATE_METADATA_KEY
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime


def captured_context():
    source = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-jmeter-report-pg-before/pg-rejected-context.json"
    bundle = json.loads(source.read_text(encoding="utf-8"))
    # The captured production bug marked an unwritten report ready. Recovery
    # must start from the existing analyzed stage, not repeat load execution.
    bundle[STATE_METADATA_KEY]["phase"] = PHASE_ANALYZED
    return SimpleNamespace(context_bundle=bundle, session_id="report-recovery", turn_id="retry", trace_id="report-recovery")


class Store:
    def __init__(self, outcome):
        self.outcome, self.saved = outcome, []

    async def save_run(self, **kwargs):
        self.saved.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [False, None, RuntimeError("owned write rejection")])
async def test_rejected_report_keeps_measurement_and_resumes_without_engine(outcome):
    context = captured_context()
    original = context.context_bundle[STATE_METADATA_KEY]
    runtime = PerformanceTestingModeRuntime()
    store = Store(outcome)
    runtime._metrics_store = store
    result = await runtime.handle({}, context)
    assert result["status"] == "partial" and result["ok"] is False
    assert result["phase"] == PHASE_ANALYZED
    assert result["metrics_persistence"] == {"status": "failed", "retryable": True}
    assert result["verdict"] == "pass"  # Storage failure is not an SLA failure.
    assert result["run"] == original["run"]
    assert result["request_validation"]["status"] == "passed"
    metric_artifact = next(a for a in result["artifacts"] if a["type"] == "performance_metrics_json")
    assert json.loads(metric_artifact["content"])["metrics_persistence"] == result["metrics_persistence"]
    assert context.context_bundle[STATE_METADATA_KEY]["phase"] == PHASE_ANALYZED
    assert len(store.saved) == 1
    # A newly created runtime has no runner at all. Only the stored report can
    # complete here, so re-running smoke/load would fail this recovery test.
    restored = PerformanceTestingModeRuntime()
    restored._metrics_store = Store(True)
    resumed = await restored.handle({}, context)
    assert resumed["status"] == "completed" and resumed["phase"] == "report_ready"
    assert resumed["metrics_persistence"] == {"status": "saved", "retryable": False}
    assert resumed["run_id"] == result["run_id"] and resumed["run"] == result["run"]
    assert resumed["report_markdown"] == result["report_markdown"]
    assert len(restored._metrics_store.saved) == 1


@pytest.mark.asyncio
async def test_report_without_configured_store_does_not_claim_saved():
    result = await PerformanceTestingModeRuntime().handle({}, captured_context())
    assert result["status"] == "completed"
    assert result["metrics_persistence"] == {"status": "not_configured", "retryable": False}

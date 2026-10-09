from __future__ import annotations

from src.modes.performance_testing_mode.plan_state import (
    PerfMetrics,
    PerfPlan,
    PerfRun,
    SLAConfig,
)
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import ParsedMetrics


def test_regression_report_fails_on_sla_violation_and_compares_baseline():
    parsed = ParsedMetrics(
        metrics=PerfMetrics(
            samples=100,
            throughput_tps=80,
            p95_ms=250,
            p99_ms=500,
            error_rate=0.02,
        )
    )
    plan = PerfPlan(
        plan_id="plan-1",
        run_intent="regression",
        sla=SLAConfig(p95_ms=200, p99_ms=400, error_rate=0.01, min_tps=100),
    )
    run = PerfRun(run_id="run-1", plan_id="plan-1", status="completed")
    baseline = PerfMetrics(p95_ms=200)

    report = PerfReportBuilder().build(parsed, plan, run, baseline)

    assert report.verdict == "fail"
    assert not report.sla_result.passed
    assert {v.metric for v in report.sla_result.violations} == {
        "p95_ms",
        "p99_ms",
        "error_rate",
        "throughput_tps",
    }
    assert report.baseline_comparison is not None
    assert report.baseline_comparison.p95_delta_pct == 25.0
    assert report.baseline_comparison.regressed is True


def test_probe_report_is_baseline_even_without_sla_pass_fail():
    parsed = ParsedMetrics(metrics=PerfMetrics(samples=1, p95_ms=999, error_rate=1.0))
    plan = PerfPlan(plan_id="plan-1", run_intent="probe", sla=SLAConfig(p95_ms=1))
    run = PerfRun(run_id="run-1", plan_id="plan-1", status="completed")

    report = PerfReportBuilder().build(parsed, plan, run)

    assert report.verdict == "baseline"
    assert report.sla_result.passed is True
    assert report.sla_result.violations == []

from src.modes.performance_testing_mode.plan_state import (
    BaselineComparison,
    PerfMetrics,
    PerfPlan,
    PerfReport,
    PerfRun,
    PerformanceTestingState,
)
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime


def test_performance_report_projects_report_and_metrics_as_inline_artifacts():
    runtime = PerformanceTestingModeRuntime()
    state = PerformanceTestingState(
        session_id="session-1",
        run=PerfRun(run_id="perf-run-1", plan_id="plan-1", status="completed"),
        plan=PerfPlan(plan_id="plan-1", run_intent="regression"),
        report=PerfReport(
            report_id="report-1",
            run_id="perf-run-1",
            run_intent="regression",
            verdict="pass",
            metrics=PerfMetrics(samples=10, p95_ms=120, p99_ms=180),
            baseline_comparison=BaselineComparison(p95_delta_pct=-5, regressed=False),
            report_markdown="# Performance report",
            report_html="<h1>Performance report</h1>",
        ),
    )

    result = __import__("asyncio").run(runtime._handle_report(state))

    assert result["baseline_comparison"] == {
        "p95_delta_pct": -5.0,
        "regressed": False,
    }
    artifacts = result["artifacts"]
    assert [item["type"] for item in artifacts] == [
        "performance_report_markdown",
        "performance_report_html",
        "performance_metrics_json",
    ]
    assert artifacts[0]["content"] == "# Performance report"
    assert artifacts[1]["content"] == "<h1>Performance report</h1>"
    assert '"samples": 10' in artifacts[2]["content"]
    assert '"sla_result"' in artifacts[2]["content"]

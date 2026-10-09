"""Regressions based on the actual cached-image summary captured before repair."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from src.application.performance.engine_adapter import RawMetrics
from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.modes.performance_testing_mode.plan_state import PerfMetrics, PerfPlan, PerfRun, PerformanceTestingState, SLAConfig
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from test_case_execution_verification import _performance_context, _verify

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-perf-measurement-before"


def actual_summary(name="default"):
    return (EVIDENCE / f"{name}.js-summary.json").read_text(encoding="utf-8")


def report_for(raw, sla=None, baseline=None, intent="regression"):
    plan = PerfPlan(run_intent=intent, sla=sla or SLAConfig(p99_ms=1000))
    run = PerfRun(run_id="measurement-regression", status="completed", engine_thresholds=raw.thresholds)
    return PerfReportBuilder().build(PerfResultParser().parse(raw), plan, run, baseline)


def test_actual_default_summary_missing_p99_cannot_pass_or_render_zero():
    raw = K6EngineAdapter().parse_results(actual_summary())
    report = report_for(raw)
    assert raw.samples == 40 and raw.p99_ms is None
    assert raw.thresholds == {"http_req_duration:p(99)<1000": {"ok": True}}
    assert report.verdict == "inconclusive" and report.sla_result.passed is None
    assert report.sla_result.missing_metrics == ["p99_ms"]
    assert report.sla_result.violations == []
    assert "| P99 | 未采集 |" in report.report_markdown
    assert "<td>P99</td><td>未采集</td>" in report.report_html
    assert json.loads(report.model_dump_json())["metrics"]["p99_ms"] is None


def test_actual_configured_summary_retains_measured_zero_and_threshold_success():
    raw = K6EngineAdapter().parse_results(actual_summary("configured"))
    report = report_for(raw)
    assert raw.p99_ms == 0.7414812 and raw.error_rate == 0 and raw.error_count == 0
    assert report.verdict == "pass" and report.sla_result.passed is True
    assert report.engine_threshold_crosscheck.agree
    assert "| 错误率 | 0.00% |" in report.report_markdown


def test_values_wrapped_thresholds_keep_ok_semantics():
    legacy = json.loads(actual_summary("configured"))
    metrics = legacy["metrics"]
    wrapped = {}
    for name, value in metrics.items():
        thresholds = value.pop("thresholds", {})
        wrapped[name] = {"values": value, "thresholds": {k: {"ok": not v} for k, v in thresholds.items()}}
    raw = K6EngineAdapter().parse_results(json.dumps({"metrics": wrapped}))
    assert raw.thresholds == {"http_req_duration:p(99)<1000": {"ok": True}}
    assert raw.p99_ms == 0.7414812


@pytest.mark.parametrize("adapter, payload", [
    (K6EngineAdapter(), "not JSON"), (JMeterEngineAdapter(), "not JSON"),
    (K6EngineAdapter(), "[]"), (JMeterEngineAdapter(), "{}"),
    (K6EngineAdapter(), '{"metrics":{"http_reqs":{"count":true}}}'),
    (JMeterEngineAdapter(), '{"Total":{"throughput":NaN}}'),
])
def test_invalid_summaries_are_explicit_parse_errors(adapter, payload):
    with pytest.raises((ValueError, TypeError)):
        adapter.parse_results(payload)


def test_jmeter_missing_measurement_differs_from_measured_zero():
    raw = JMeterEngineAdapter().parse_results('{"Total":{"sampleCount":3,"errorPct":0,"minResTime":0}}')
    assert raw.error_rate == 0 and raw.min_ms == 0 and raw.p99_ms is None
    assert report_for(raw).verdict == "inconclusive"


@pytest.mark.parametrize("samples", [None, 0])
@pytest.mark.parametrize("intent", ["probe", "regression"])
def test_no_samples_cannot_be_successful_baseline_or_pass(samples, intent):
    report = report_for(RawMetrics(samples=samples, p99_ms=0), intent=intent)
    assert report.verdict == "inconclusive" and report.sla_result.passed is None
    assert report.sla_result.missing_metrics == ["samples"]


def test_known_violation_wins_over_missing_measurement_and_tps_sign_is_correct():
    report = report_for(RawMetrics(samples=1, throughput_tps=2), SLAConfig(min_tps=5, p99_ms=1000))
    assert report.verdict == "fail" and report.sla_result.passed is False
    assert report.sla_result.missing_metrics == ["p99_ms"]
    assert "实际 2.00 < 阈值 5.00" in report.report_markdown


def test_unavailable_baseline_is_not_displayed_as_zero_change_or_success():
    raw = K6EngineAdapter().parse_results(actual_summary("configured"))
    report = report_for(raw, baseline=PerfMetrics(p95_ms=None))
    assert report.verdict == "inconclusive"
    assert "无法计算" in report.report_markdown
    assert "变化: ↓ 0.0%" not in report.report_markdown


def test_engine_failure_cannot_be_ignored_when_independent_sla_passes():
    raw = K6EngineAdapter().parse_results(actual_summary("configured"))
    raw.thresholds = {"duration": {"ok": False}}
    report = report_for(raw)
    assert report.verdict == "inconclusive" and not report.engine_threshold_crosscheck.agree


def test_generated_threshold_retains_the_actual_decimal_sla():
    script = K6EngineAdapter().build_script(PerfPlan(sla=SLAConfig(p95_ms=0.001, p99_ms=0.001)))
    assert 'threshold: "p(95)<0.001"' in script.script_content
    assert 'threshold: "p(99)<0.001"' in script.script_content
    script = K6EngineAdapter().build_script(PerfPlan(sla=SLAConfig(p99_ms=123.456789)))
    assert 'threshold: "p(99)<123.456789"' in script.script_content


@pytest.mark.asyncio
async def test_mode_artifact_and_case_verifier_preserve_null_and_partial():
    raw = K6EngineAdapter().parse_results(actual_summary())
    plan = PerfPlan(run_intent="regression", sla=SLAConfig(p99_ms=1000))
    run = PerfRun(run_id="measurement-regression", status="completed", raw_metrics=asdict(raw), engine_thresholds=raw.thresholds)
    state = PerformanceTestingState(plan=plan, run=run, report=report_for(raw))
    output = await PerformanceTestingModeRuntime()._handle_report(state)
    result = _verify("performance-test-runner", output, _performance_context({"kind": "p99_ms", "threshold": 1000}))
    assert result.status.value == "partial"
    assert result.passed_count == 0 and result.failed_count == 0
    artifact = json.loads(output["artifacts"][2]["content"])
    assert artifact["metrics"]["p99_ms"] is None and artifact["sla_result"]["passed"] is None

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.performance.engine_adapter import RunOptions
from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.modes.performance_testing_mode.plan_state import (
    DataParam,
    PerfPlan,
    PerfTarget,
    PerfWorkloadConfig,
    SLAConfig,
    Correlation,
)
from src.application.performance.request_validation import request_validation
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.plan_state import PerfRun
from dataclasses import asdict


def test_jmeter_build_script_contains_core_jmx_elements():
    plan = PerfPlan(
        plan_id="plan-1",
        title="Load <Plan>",
        engine="jmeter",
        targets=[
            PerfTarget(
                name="primary",
                url="http://localhost:8080/api/v1/users?q=1",
                method="GET",
                headers={"Authorization": "Bearer token"},
            )
        ],
        workload=PerfWorkloadConfig(
            model="open",
            mode="constant_arrival_rate",
            target_rate_rps=20,
            virtual_users=40,
            hold_seconds=90,
        ),
        data_params=[DataParam(name="users", ref="users.csv")],
        sla=SLAConfig(p95_ms=500, error_rate=0.01),
    )

    artifact = JMeterEngineAdapter().build_script(plan)

    assert artifact.engine == "jmeter"
    assert artifact.filename == "test.jmx"
    assert "<ThreadGroup" in artifact.script_content
    assert "<HTTPSamplerProxy" in artifact.script_content
    assert "host.docker.internal" in artifact.script_content
    assert "HTTP Header Manager" in artifact.script_content
    assert "CSVDataSet" in artifact.script_content
    assert "ConstantThroughputTimer" in artifact.script_content
    assert "<stringProp name=\"throughput\">72000</stringProp>" in artifact.script_content
    assert artifact.data_files == {"data/users.csv": "users.csv"}


def test_jmeter_run_command_uses_non_gui_report_generation():
    adapter = JMeterEngineAdapter(image="custom/jmeter:latest")
    artifact = adapter.build_script(PerfPlan(engine="jmeter", targets=[]))

    cmd = adapter.run_command(artifact, RunOptions(is_smoke=True, environment={"foo": "bar"}))

    assert cmd.image == "custom/jmeter:latest"
    assert cmd.command[:4] == ["jmeter", "-n", "-t", "/work/test.jmx"]
    assert "-e" in cmd.command
    assert "/work/report" in cmd.command
    assert "-Jqa_perf.smoke=true" in cmd.command
    assert "-Jfoo=bar" in cmd.command


def test_jmeter_parse_statistics_json_maps_total_metrics():
    stats = {
        "Total": {
            "sampleCount": 33224,
            "errorCount": 2,
            "errorPct": 0.5,
            "meanResTime": 376.5,
            "medianResTime": 6.0,
            "minResTime": 0.0,
            "maxResTime": 8231.0,
            "pct1ResTime": 435.0,
            "pct2ResTime": 491.95,
            "pct3ResTime": 7830.0,
            "throughput": 262.59,
        },
        "Login": {"sampleCount": 10},
    }

    metrics = JMeterEngineAdapter().parse_results(json.dumps(stats))

    assert metrics.samples == 33224
    assert metrics.error_count == 2
    assert metrics.error_rate == 0.005
    assert metrics.throughput_tps == 262.59
    assert metrics.avg_ms == 376.5
    assert metrics.p50_ms == 6.0
    assert metrics.p90_ms == 435.0
    assert metrics.p95_ms == 491.95
    assert metrics.p99_ms == 7830.0
    assert metrics.raw_data["Login"]["sampleCount"] == 10


def test_ordered_journey_keeps_credentials_out_of_sample_variables():
    plan = PerfPlan(engine="jmeter", targets=[PerfTarget(name="login", url="http://localhost/login"),
        PerfTarget(name="profile", url="http://localhost/profile")],
        correlations=[Correlation(extract="ticket", from_path="session.credential", into_header="Authorization")])
    adapter = JMeterEngineAdapter()
    script = adapter.build_script(plan)
    assert script.script_content.count("<ThreadGroup ") == 1
    assert script.script_content.index('testname="login"') < script.script_content.index('testname="profile"')
    assert "${qa_corr_0}" in script.script_content and "JMESPathExtractor" in script.script_content
    cmd = adapter.run_command(script, RunOptions(is_smoke=True, smoke_iterations=3, smoke_vus=1))
    assert "-Jsample_variables=qa_extract_0,qa_dependency_0" in cmd.command
    assert "-Jqa_perf.scheduler=false" in cmd.command
    assert "-Jqa_perf.smoke_vus=1" in cmd.command


@pytest.mark.parametrize("variant,verdict", [("normal", "pass"), ("wrong-status", "fail"),
    ("negative", "pass"), ("chain", "pass")])
def test_actual_jmeter_statuses_and_boolean_evidence_drive_report(variant, verdict):
    directory = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-jmeter-response-final"
    plan = PerfPlan.model_validate_json((directory / f"{variant}-plan.json").read_text(encoding="utf-8"))
    plan.run_intent = "regression"
    plan.sla = SLAConfig(p99_ms=10000, error_rate=0.01)
    summary = (directory / f"{variant}-summary.json").read_text(encoding="utf-8")
    raw = JMeterEngineAdapter().parse_results(summary)
    validation = request_validation(json.loads(summary), plan)
    assert validation["status"] == ("failed" if verdict == "fail" else "passed")
    report = PerfReportBuilder().build(PerfResultParser().parse(raw), plan,
        PerfRun(status="completed", exit_code=0, raw_metrics=asdict(raw), evidence={"status": "saved"}))
    assert report.verdict == verdict
    if variant == "chain":
        assert validation["correlations"][0]["extraction"]["passes"] == 3
        assert validation["correlations"][0]["binding"]["passes"] == 3
        assert "qa_corr_0" not in json.loads(summary)["__jtl"].splitlines()[0]

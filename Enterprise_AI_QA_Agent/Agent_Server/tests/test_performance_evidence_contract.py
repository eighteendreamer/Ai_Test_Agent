"""Regressions from actual missing, malformed and exit107 k6 evidence."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from src.application.performance.engine_adapter import RawMetrics
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.perf_runner_service import ExecutionResult, PerfRunnerService
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfRun, SLAConfig
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from test_performance_runner import _Settings

ROOT = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-perf-evidence-reproduction"


@pytest.mark.parametrize("name", ["missing", "malformed", "teardown"])
def test_actual_invalid_smoke_is_not_passed(name):
    raw = json.loads((ROOT / f"{name}.js-execution.json").read_bytes())
    result = PerfRunnerService(_Settings())._validate_smoke(ExecutionResult(**raw), PerfPlan())
    assert result.passed is False


def test_actual_normal_smoke_still_validates_engine_data():
    raw = json.loads((ROOT / "normal.js-execution.json").read_bytes())
    result = PerfRunnerService(_Settings())._validate_smoke(ExecutionResult(**raw), PerfPlan())
    assert result.passed is True


def test_actual_exit107_load_cannot_pass_despite_valid_samples_and_sla():
    run = PerfRun.model_validate(json.loads((ROOT / "teardown-run.json").read_bytes()))
    parsed = PerfResultParser().parse(RawMetrics(**run.raw_metrics))
    report = PerfReportBuilder().build(parsed, PerfPlan(run_intent="regression", sla=SLAConfig(p99_ms=1000)), run)
    assert parsed.metrics.samples == 40 and report.sla_result.passed is True
    assert report.execution_status == "failed" and report.engine_exit_code == 107
    assert report.verdict == "inconclusive"


@pytest.mark.parametrize("summary", [
    '{"metrics":{"http_reqs":{"count":3}}}',
    '{"metrics":{"http_reqs":{"count":true},"http_req_failed":{"value":0}}}',
    '{"metrics":{"http_reqs":{"count":3},"http_req_failed":{"value":NaN}}}',
    '{"Total":{"sampleCount":3}}', '[]',
])
def test_smoke_does_not_invent_missing_or_invalid_failure_rate(summary):
    result = PerfRunnerService(_Settings())._validate_smoke(ExecutionResult(0, "", "", summary_json=summary), PerfPlan())
    assert result.passed is False


def test_measured_sla_violation_remains_failure_even_when_process_or_evidence_failed():
    run = PerfRun.model_validate(json.loads((ROOT / "teardown-run.json").read_bytes()))
    report = PerfReportBuilder().build(PerfResultParser().parse(RawMetrics(**run.raw_metrics)),
        PerfPlan(run_intent="regression", sla=SLAConfig(p99_ms=0.001)), run)
    assert report.verdict == "fail" and report.sla_result.passed is False


def test_new_partial_evidence_cannot_pass_with_healthy_process():
    data = K6EngineAdapter().parse_results((ROOT / "normal.js-summary.json").read_text(encoding="utf-8"))
    run = PerfRun(status="completed", exit_code=0, raw_metrics=asdict(data), evidence={"status": "partial", "error": "storage interrupted"})
    report = PerfReportBuilder().build(PerfResultParser().parse(data), PerfPlan(run_intent="regression", sla=SLAConfig(p99_ms=1000)), run)
    assert report.verdict == "inconclusive" and report.evidence_status == "partial"


def test_streamed_file_uses_explicit_identity_and_existing_client(tmp_path):
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from test_artifact_storage_service import _RustFSClient, _settings
    import asyncio

    client = _RustFSClient()
    objects = ArtifactStorageService(_settings(tmp_path))
    objects._rustfs_client = lambda: client
    path = tmp_path / "output.json"
    path.write_bytes(b"owned engine output")
    result = asyncio.run(objects.store_uploaded_file(path=path, object_prefix="unused", object_name="run-unique/report/output.json"))
    assert result["uri"] == "rustfs://qa-agent/run-unique/report/output.json"
    assert result["size_bytes"] == len(b"owned engine output")
    assert client.objects[("qa-agent", "run-unique/report/output.json")][0] == b"owned engine output"


def test_report_shows_and_escapes_evidence_warning():
    data = K6EngineAdapter().parse_results((ROOT / "normal.js-summary.json").read_text(encoding="utf-8"))
    warning = 'storage error: <script>alert("failure")</script>'
    run = PerfRun(status="completed", exit_code=0, raw_metrics=asdict(data), evidence={"status": "partial", "error": warning})
    report = PerfReportBuilder().build(PerfResultParser().parse(data), PerfPlan(run_intent="regression", sla=SLAConfig(p99_ms=1000)), run)
    assert warning in report.report_markdown
    assert "证据缺口" in report.report_html and "&lt;script&gt;" in report.report_html
    assert "<script>" not in report.report_html

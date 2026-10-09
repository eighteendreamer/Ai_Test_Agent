"""Regress actual JTL missing dependency facts, including evidence omissions."""
import csv
import io
import json
from pathlib import Path

import pytest

from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.request_validation import request_validation
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfRun, SLAConfig
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-jmeter-dependencies-fixed"


def captured():
    plan = PerfPlan.model_validate_json((EVIDENCE / "missing-plan.json").read_text(encoding="utf-8"))
    data = json.loads((EVIDENCE / "missing-summary.json").read_text(encoding="utf-8"))
    return plan, data, list(csv.DictReader(io.StringIO(data["__jtl"])))


def replace_rows(data, rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    data["__jtl"] = output.getvalue()


def test_actual_missing_requests_are_skipped_not_reported_as_http_success():
    plan, data, _ = captured()
    result = request_validation(data, plan)
    assert result["status"] == "failed" and not result["missing_checks"]
    assert [(t["name"], t["iterations"], t["blocked_by"]) for t in result["skipped_targets"]] == [
        ("bridge", 3, [0]), ("final", 3, [1])]
    assert result["targets"][1]["observed_status"] == []
    assert result["targets"][1]["checks"][0]["passed"] is None
    assert result["targets"][3]["executed_samples"] == 3
    assert result["targets"][3]["skipped_iterations"] == 0
    assert result["correlations"][0]["binding"]["skipped_iterations"] == 3


@pytest.mark.parametrize("corruption", ["remove-source", "unknown-extraction", "missing-independent", "invalid-iteration", "missing-thread", "duplicate-source"])
def test_absence_or_invalid_identity_cannot_be_claimed_as_proven_skip(corruption):
    plan, data, rows = captured()
    if corruption == "remove-source":
        rows = [r for r in rows if r["label"] != "source"]
    elif corruption == "unknown-extraction":
        for row in rows:
            row["qa_extract_0"] = ""
    elif corruption == "missing-independent":
        rows = [r for r in rows if r["label"] != "independent"]
    elif corruption in {"invalid-iteration", "missing-thread"}:
        for row in rows:
            row["qa_iteration" if corruption == "invalid-iteration" else "threadName"] = ""
    elif corruption == "duplicate-source":
        rows.append(next(r for r in rows if r["label"] == "source").copy())
    replace_rows(data, rows)
    result = request_validation(data, plan)
    assert result["missing_checks"] and result["status"] != "passed"
    if corruption not in {"missing-independent", "duplicate-source"}:
        assert result["skipped_targets"] == []


def test_legacy_jtl_keeps_evidence_gap_without_guessing_skip():
    plan, data, rows = captured()
    for row in rows:
        del row["qa_iteration"]
    replace_rows(data, rows)
    result = request_validation(data, plan)
    assert "skipped_targets" not in result
    assert "status:bridge" in result["missing_checks"]


def test_actual_request_skip_fails_report_even_when_exit_zero_and_sla_passes():
    plan, data, _ = captured()
    plan.run_intent, plan.sla = "regression", SLAConfig(p99_ms=10000, error_rate=0.01)
    parsed = PerfResultParser().parse(JMeterEngineAdapter().parse_results(json.dumps(data)))
    report = PerfReportBuilder().build(parsed, plan, PerfRun(status="completed", exit_code=0))
    assert report.sla_result.passed is True and report.verdict == "fail"
    assert report.metrics.samples == 6
    assert "跳过 bridge：3 轮" in report.report_markdown
    assert "跳过 final：3 轮" in report.report_html

"""Read actual native-type JTL and preserve the legacy observation contract."""
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

DOCS = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def captured(variant, directory="p5-perf-typed-fixed"):
    path = DOCS / directory
    plan = PerfPlan.model_validate_json((path / f"jmeter-{variant}-plan.json").read_text(encoding="utf-8"))
    data = json.loads((path / f"jmeter-{variant}-summary.json").read_bytes())
    return plan, data


@pytest.mark.parametrize("variant,type_name,passed", [("string-null", "string", True),
    ("object-header", "object", False), ("array-header", "array", False), ("source-status", "string", False)])
def test_actual_native_type_and_header_eligibility_are_separate(variant, type_name, passed):
    plan, data = captured(variant)
    result = request_validation(data, plan)
    detail = result["correlations"][0]
    assert detail["value_types"] == {type_name: 3}
    assert detail["header_eligibility"]["passed"] is passed
    assert detail["extraction"]["passed"] is (variant != "source-status")
    assert result["extracted"]["value"] is (variant != "source-status")
    if not passed:
        assert result["status"] == "failed" and not result["missing_checks"]
        assert result["skipped_targets"][0]["name"] == "consumer"
        assert result["targets"][1]["observed_status"] == []
    plan.run_intent, plan.sla = "regression", SLAConfig(p99_ms=10000, error_rate=1)
    report = PerfReportBuilder().build(PerfResultParser().parse(JMeterEngineAdapter().parse_results(json.dumps(data))),
        plan, PerfRun(status="completed", exit_code=0))
    assert report.verdict == ("pass" if passed else "fail")
    assert "类型" in report.report_markdown and "header可用" in report.report_html


def test_missing_new_header_fact_is_not_inferred_from_extraction():
    plan, data = captured("object-header")
    rows = list(csv.DictReader(io.StringIO(data["__jtl"])))
    for row in rows:
        del row["qa_header_0"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    data["__jtl"] = output.getvalue()
    result = request_validation(data, plan)
    assert "qa_header_0" in result["missing_checks"] and result["status"] == "missing"
    assert result["skipped_targets"] == []
    assert result["correlations"][0]["extraction"]["passed"]


def test_legacy_raw_checks_do_not_claim_typed_observation():
    plan, data = captured("array-header", "p5-perf-typed-reproduction")
    result = request_validation(data, plan)
    assert "value_types" not in result["correlations"][0]
    assert "header_eligibility" not in result["correlations"][0]
    assert "selector_language" not in result["correlations"][0]
    assert result["status"] == "passed"  # Old boolean facts are unchanged.


@pytest.mark.parametrize("variant,type_name,passed", [("zero", "number", True), ("false", "boolean", True),
    ("index", "string", True), ("filter", "string", True), ("quoted-key", "string", True),
    ("empty-array-only", "array", True), ("trailing-json", "invalid", False)])
def test_actual_native_paths_and_complete_json_response(variant, type_name, passed):
    plan, data = captured(variant, "p5-perf-typed-verified")
    result = request_validation(data, plan)
    detail = result["correlations"][0]
    assert detail["value_types"] == {type_name: 3}
    assert result["extracted"]["value"] is passed
    assert result["status"] == ("passed" if passed else "failed")
    if variant == "empty-array-only":
        assert "binding" not in detail and "header_eligibility" not in detail
        plan.run_intent = "regression"
        report = PerfReportBuilder().build(PerfResultParser().parse(JMeterEngineAdapter().parse_results(json.dumps(data))),
            plan, PerfRun(status="completed", exit_code=0))
        assert "仅提取" in report.report_markdown and "仅提取" in report.report_html
    elif not passed:
        assert result["skipped_targets"][0]["iterations"] == 3
        assert result["targets"][1]["observed_status"] == []

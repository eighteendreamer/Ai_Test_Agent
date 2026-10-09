"""Regressions grounded in actual pre-repair and fixed k6 summaries."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.perf_runner_service import ExecutionResult, PerfRunnerService
from src.application.performance.request_validation import request_validation
from src.modes.performance_testing_mode.plan_state import Correlation, PerfPlan, PerfRun, PerfTarget, SLAConfig
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from test_performance_runner import _Settings

DOCS = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"
BEFORE = DOCS / "p5-perf-correlation-before"
FIXED = DOCS / "p5-perf-correlation-verified"


def read(path):
    return json.loads(path.read_bytes())


def test_actual_stdout_name_is_not_evidence_of_extraction():
    result = ExecutionResult(**read(BEFORE / "missing-noise-execution.json"))
    plan = PerfPlan.model_validate(read(BEFORE / "missing-noise-plan.json"))
    assert "ticket" in result.stdout
    smoke = PerfRunnerService(_Settings())._validate_smoke(result, plan)
    assert not smoke.passed and smoke.extracted == {"ticket": False}
    assert smoke.request_validation["status"] == "missing"


def test_actual_valid_extract_passes_without_printing_name_or_value():
    result = ExecutionResult(**read(FIXED / "single-execution.json"))
    plan = PerfPlan.model_validate(read(FIXED / "single-plan.json"))
    smoke = PerfRunnerService(_Settings())._validate_smoke(result, plan)
    assert "ticket" not in result.stdout
    assert smoke.passed and smoke.extracted == {"ticket": True} and smoke.checked_status == [200]


def test_actual_chain_maps_source_extraction_and_header_check_once():
    result = ExecutionResult(**read(FIXED / "chain-execution.json"))
    plan = PerfPlan.model_validate(read(FIXED / "chain-plan.json"))
    validation = request_validation(json.loads(result.summary_json), plan)
    assert validation["status"] == "passed"
    correlation = validation["correlations"][0]
    assert (correlation["from_target"], correlation["into_target"], correlation["into_header"]) == ("login", "profile", "Authorization")
    assert correlation["extraction"]["passes"] == correlation["binding"]["passes"] == 3
    assert "value" not in correlation


@pytest.mark.parametrize("counts", [{"passes": True, "fails": 0}, {"passes": 0, "fails": 0},
    {"passes": -1, "fails": 0}, {"passes": 1.5, "fails": 0}, {"passes": 3}, {"passes": "NaN", "fails": 0}])
def test_invalid_actual_check_counter_is_missing_not_passed(counts):
    data = read(FIXED / "single-summary.json")
    data["root_group"]["checks"]["qa_extract_0"] = {"name": "qa_extract_0", **counts}
    plan = PerfPlan.model_validate(read(FIXED / "single-plan.json"))
    validation = request_validation(data, plan)
    assert validation["status"] == "missing" and validation["extracted"] == {"ticket": False}


def test_wrapped_group_check_lists_preserve_actual_counters():
    data = read(FIXED / "chain-summary.json")
    checks = list(data["root_group"]["checks"].values())
    data["root_group"] = {"checks": [], "groups": [{"checks": checks, "groups": []}]}
    plan = PerfPlan.model_validate(read(FIXED / "chain-plan.json"))
    validation = request_validation(data, plan)
    assert validation["status"] == "passed" and validation["extracted"] == {"ticket": True}


def test_actual_valid_process_and_sla_cannot_hide_failed_business_check():
    data = read(FIXED / "single-summary.json")
    data["root_group"]["checks"]["qa_extract_0"].update(passes=2, fails=1)
    plan = PerfPlan.model_validate(read(FIXED / "single-plan.json"))
    plan.run_intent, plan.sla = "regression", SLAConfig(p99_ms=1000)
    raw = K6EngineAdapter().parse_results(json.dumps(data))
    report = PerfReportBuilder().build(PerfResultParser().parse(raw), plan,
        PerfRun(status="completed", exit_code=0, raw_metrics=asdict(raw), evidence={"status": "saved"}))
    assert report.sla_result.passed is True and report.verdict == "fail"
    assert "qa_extract_0" in report.report_markdown and "qa_extract_0" in report.report_html


def test_missing_required_checks_cannot_be_successful_baseline():
    data = read(FIXED / "single-summary.json")
    data.pop("root_group")
    plan = PerfPlan.model_validate(read(FIXED / "single-plan.json"))
    raw = K6EngineAdapter().parse_results(json.dumps(data))
    report = PerfReportBuilder().build(PerfResultParser().parse(raw), plan,
        PerfRun(status="completed", exit_code=0, raw_metrics=asdict(raw), evidence={"status": "saved"}))
    assert report.verdict == "inconclusive" and report.request_validation["status"] == "missing"


@pytest.mark.parametrize("fields", [{"from_target": "not-a-target"}, {"into_target": "login"},
    {"from_target": "profile"}, {"into_target": "not-a-target"}])
def test_ambiguous_or_impossible_identity_fails_generation(fields):
    plan = PerfPlan(targets=[PerfTarget(name="login", url="http://owned/login"), PerfTarget(name="profile", url="http://owned/profile")],
        correlations=[Correlation(extract="ticket", from_path="session.credential", into_header="Authorization", **fields)])
    with pytest.raises(ValueError):
        K6EngineAdapter().build_script(plan)


def test_old_correlation_serializes_exactly_original_three_fields():
    original = {"extract": "ticket", "from_path": "session.credential", "into_header": "Authorization"}
    assert Correlation.model_validate(original).model_dump(mode="json") == original
    assert Correlation(**original, from_target="login").model_dump(mode="json") == {**original, "from_target": "login"}

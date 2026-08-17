from src.application.testing.verification_service import VerificationService


def _verify(tool_key, output, context_bundle=None):
    return VerificationService().build_results(
        session_id="session-1",
        turn_id="turn-1",
        trace_id="trace-1",
        tool_results=[
            {
                "call_id": "call-1",
                "job_id": "job-1",
                "tool_key": tool_key,
                "status": "completed",
                "summary": "工具流程结束",
                "output": output,
            }
        ],
        context_bundle=context_bundle,
    )[0]


def _performance_output(**changes):
    output = {
        "status": "completed",
        "ok": True,
        "summary": "性能回归完成",
        "run_id": "perf-run-1",
        "run_intent": "regression",
        "verdict": "pass",
        "metrics": {
            "samples": 500,
            "throughput_tps": 25.0,
            "p95_ms": 180.0,
            "p99_ms": 240.0,
            "error_rate": 0.001,
        },
        "sla_result": {"passed": True, "violations": []},
        "engine_threshold_crosscheck": {"agree": True, "detail": "一致"},
    }
    output.update(changes)
    return output


def _performance_context(*assertions, request=None):
    context = {
        "test_case": {
            "case_id": "case-1",
            "assertions": list(assertions),
        }
    }
    if request is not None:
        context["performance_testing_request"] = request
    return context


def test_api_runner_uses_task_assertions_instead_of_top_level_completed_status():
    result = _verify(
        "api-test-runner",
        {
            "status": "completed",
            "ok": False,
            "summary": "API task executed",
            "task_result": {
                "task_id": "case-1",
                "status": "failed",
                "check_results": [
                    {"name": "status_code=200", "passed": False, "actual": 500}
                ],
            },
        },
    )

    assert result.status.value == "failed"
    assert result.failed_count == 1


def test_ui_exploration_success_is_not_reported_as_test_case_passed():
    result = _verify(
        "ui-automation-runner",
        {
            "status": "completed",
            "phase": "exploration_completed",
            "summary": "页面图谱采集完成",
            "exploration_result": {"status": "success"},
        },
    )

    assert result.status.value == "not_run"
    assert result.assertion_count == 0


def test_compatibility_dispatch_is_not_reported_as_completed_verification():
    result = _verify(
        "compatibility-test-runner",
        {
            "status": "partial",
            "phase": "dispatching",
            "summary": "Runner tasks queued",
            "runner_summary": {"queued_count": 3, "total_count": 3},
        },
    )

    assert result.status.value == "not_run"
    assert result.passed_count == 0


def test_performance_runner_requires_regression_sla_evidence_for_pass():
    result = _verify(
        "performance-test-runner",
        _performance_output(),
        context_bundle=_performance_context(
            {"kind": "p95_ms", "expected": 250},
            {"kind": "p99_ms", "expected": 300},
            {"kind": "error_rate", "expected": 0.01},
        ),
    )

    assert result.status.value == "passed"
    assert result.assertion_count == 3
    assert result.passed_count == 3
    assert result.failed_count == 0
    assert result.metadata["verdict"] == "pass"
    assert result.metadata["run_intent"] == "regression"


def test_performance_sla_violation_maps_to_failed_verification():
    result = _verify(
        "performance-test-runner",
        _performance_output(
            verdict="fail",
            sla_result={
                "passed": False,
                "violations": [
                    {"metric": "p95_ms", "actual": 320, "threshold": 250}
                ],
            },
        ),
        context_bundle=_performance_context(
            {"kind": "p95_ms", "expected": 250}
        ),
    )

    assert result.status.value == "failed"
    assert result.failed_count == 1


def test_performance_probe_baseline_is_not_reported_as_test_case_passed():
    result = _verify(
        "performance-test-runner",
        _performance_output(run_intent="probe", verdict="baseline"),
        context_bundle=_performance_context(
            {"kind": "p95_ms", "expected": 250}
        ),
    )

    assert result.status.value == "not_run"
    assert result.passed_count == 0


def test_performance_regression_without_sla_or_baseline_is_not_run():
    result = _verify(
        "performance-test-runner",
        _performance_output(),
        context_bundle=_performance_context(),
    )

    assert result.status.value == "not_run"
    assert result.passed_count == 0


def test_performance_zero_sample_output_cannot_pass():
    result = _verify(
        "performance-test-runner",
        _performance_output(
            metrics={
                "samples": 0,
                "throughput_tps": 0,
                "p95_ms": 0,
                "p99_ms": 0,
                "error_rate": 0,
            }
        ),
        context_bundle=_performance_context(
            {"kind": "p95_ms", "expected": 250}
        ),
    )

    assert result.status.value == "partial"
    assert result.passed_count == 0


def test_performance_engine_crosscheck_disagreement_cannot_pass():
    result = _verify(
        "performance-test-runner",
        _performance_output(
            engine_threshold_crosscheck={"agree": False, "detail": "阈值不一致"}
        ),
        context_bundle=_performance_context(
            {"kind": "p95_ms", "expected": 250}
        ),
    )

    assert result.status.value == "partial"
    assert result.passed_count == 0


def test_performance_baseline_regression_maps_to_failed_verification():
    result = _verify(
        "performance-test-runner",
        _performance_output(
            verdict="fail",
            baseline_comparison={"p95_delta_pct": 25.0, "regressed": True},
        ),
        context_bundle=_performance_context(),
    )

    assert result.status.value == "failed"
    assert result.failed_count == 1


def test_performance_baseline_regression_cannot_be_overridden_by_pass_verdict():
    result = _verify(
        "performance-test-runner",
        _performance_output(
            verdict="pass",
            baseline_comparison={"p95_delta_pct": 25.0, "regressed": True},
        ),
        context_bundle=_performance_context(),
    )

    assert result.status.value == "failed"
    assert result.passed_count == 0


def test_performance_clean_baseline_comparison_can_supply_pass_criterion():
    result = _verify(
        "performance-test-runner",
        _performance_output(
            baseline_comparison={"p95_delta_pct": -5.0, "regressed": False}
        ),
        context_bundle=_performance_context(),
    )

    assert result.status.value == "passed"


def test_performance_unknown_assertions_are_not_counted_as_passed():
    result = _verify(
        "performance-test-runner",
        _performance_output(),
        context_bundle=_performance_context(
            {"kind": "p95_ms", "expected": 250},
            {"kind": "unsupported_business_rule", "expected": "ok"},
            request={"sla_p95_ms": 250},
        ),
    )

    assert result.status.value == "passed"
    assert result.assertion_count == 2
    assert result.passed_count == 1

from src.application.testing.verification_service import VerificationService


def _verify(tool_key, output):
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
    )[0]


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

import asyncio

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor


def test_executor_reuses_only_complete_matching_checkpoint():
    task = ApiTestTask(task_id="task-1", method="GET", path="/health")
    task.execution_checkpoint = {
        "completed_request": {
            "idempotency_key": task.idempotency_key,
            "status": "completed",
            "response_status": 200,
            "response_headers": {"content-type": "application/json"},
            "response_body": {"ok": True},
            "check_results": [{"name": "status", "passed": True}],
            "duration_ms": 12,
            "completed_at": "2026-09-09T00:00:00+00:00",
        }
    }
    result = asyncio.run(ApiTaskExecutor(credential_manager=CredentialManager()).execute(task))
    assert result.status == "completed"
    assert result.response_status == 200
    assert result.response_body == {"ok": True}


def test_executor_does_not_reuse_checkpoint_with_missing_evidence():
    task = ApiTestTask(task_id="task-1", method="GET", path="http://127.0.0.1:1/unreachable")
    task.execution_checkpoint = {
        "completed_request": {
            "idempotency_key": task.idempotency_key,
            "status": "completed",
            "response_status": 200,
        }
    }
    result = asyncio.run(ApiTaskExecutor(credential_manager=CredentialManager(), timeout_seconds=0.01).execute(task))
    assert result.status == "failed"
    assert result.last_error

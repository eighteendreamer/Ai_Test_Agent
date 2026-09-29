import asyncio

from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign,
    ApiTestTask,
    ExecutionPolicy,
)
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.evaluation import ApiTestingEvaluationPolicy
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.task_pool import ApiTaskPool


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


def test_api_campaign_retries_parent_and_runs_previously_skipped_dependents():
    create = ApiTestTask(task_id="create", method="POST", path="/orders", execution_mode="write", status="ready")
    read = ApiTestTask(task_id="read", method="GET", path="/orders/{id}", depends_on=["create"], status="blocked")
    verify = ApiTestTask(task_id="verify", method="GET", path="/orders/{id}/status", depends_on=["read"], status="blocked")
    pool = ApiTaskPool([create, read, verify])
    calls = []

    async def execute(task):
        calls.append(task.task_id)
        task.attempts += 1
        if task.task_id == "create" and task.attempts == 1:
            return task.model_copy(update={"status": "failed", "last_error": "timeout"})
        return task.model_copy(update={"status": "completed", "response_status": 200})

    result = asyncio.run(ApiTestCoordinator(
        pool=pool,
        policy=ExecutionPolicy(max_retries=1),
        executor_fn=execute,
    ).run_all())

    assert {task.task_id: task.status for task in result} == {
        "create": "completed", "read": "completed", "verify": "completed",
    }
    assert calls == ["create", "create", "read", "verify"]


def test_api_coverage_excludes_tasks_that_have_not_run():
    tasks = [
        ApiTestTask(task_id="create", method="POST", path="/orders", status="ready", capability="orders.create"),
        ApiTestTask(task_id="read", method="GET", path="/orders/{id}", depends_on=["create"], status="blocked", capability="orders.read"),
    ]

    campaign = ApiTestCampaign(campaign_id="orders", tasks=tasks)
    coverage = ApiTestingEvaluationPolicy().evaluate(
        campaign=campaign, total_available_endpoints=2,
    ).coverage

    assert coverage.tested_endpoints == 0
    assert coverage.coverage_rate == 0
    assert coverage.tested_capabilities == []


def test_restored_dependency_skip_reopens_but_explicit_skip_stays_terminal():
    create = ApiTestTask(task_id="create", method="POST", path="/orders", status="ready")
    read = ApiTestTask(task_id="read", method="GET", path="/orders/1", depends_on=["create"], status="blocked")
    manual = ApiTestTask(task_id="manual", method="GET", path="/health", status="blocked")
    pool = ApiTaskPool([create, read, manual])
    pool.mark_skipped("manual", "Excluded by reviewer")
    pool.mark_failed("create", "timeout")

    restored = ApiTaskPool([ApiTestTask.model_validate(task.model_dump()) for task in pool.all_tasks])
    restored.get("create").status = "ready"
    restored.resolve_blocked()
    assert restored.get("read").status == "blocked"
    restored.mark_completed("create")

    assert restored.get("read").status == "ready"
    assert restored.get("manual").status == "skipped"
    assert restored.get("manual").last_error == "Excluded by reviewer"


def test_pending_api_task_without_failed_dependencies_remains_pending():
    pool = ApiTaskPool([ApiTestTask(task_id="pending", method="GET", path="/health")])

    assert pool.resolve_blocked() == 0
    assert pool.get("pending").status == "pending"


def test_legacy_dependency_skip_restores_after_parent_succeeds():
    parent = ApiTestTask(task_id="create", method="POST", path="/orders", status="completed")
    dependent = ApiTestTask(
        task_id="read", method="GET", path="/orders/1", depends_on=["create"],
        status="skipped", last_error="Skipped because dependency create failed.",
    )
    pool = ApiTaskPool([parent, dependent])

    assert pool.resolve_blocked() == 1
    assert dependent.status == "ready"
    assert dependent.last_error == ""

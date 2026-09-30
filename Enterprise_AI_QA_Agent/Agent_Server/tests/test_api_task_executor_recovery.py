import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from src.domain.models import SessionRecord
from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign,
    ApiTestingState,
    ApiTestTask,
    ExecutionPolicy,
)
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.evaluation import ApiTestingEvaluationPolicy
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore
from src.schemas.session import ChatMessage, MessageRole, RuntimeMode, SessionMode, SessionStatus


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


def test_api_subagent_retry_has_a_distinct_dispatch_identity():
    task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
    coordinator = ApiSubagentCoordinator(
        pool=ApiTaskPool([task]),
        policy=ExecutionPolicy(max_retries=1),
        coordinator_runtime_service=None,
        session_store=InMemorySessionStore(),
        parent_context={"session_id": "parent", "turn_id": "turn"},
        credential_manager=CredentialManager(),
    )
    task.attempts = 1
    first = coordinator._build_worker_spec(task)
    task.attempts = 2
    second = coordinator._build_worker_spec(task)

    assert first["task_id"] == "read"
    assert second["task_id"] == "read:attempt:2"
    assert first["context"]["api_task_id"] == second["context"]["api_task_id"] == "read"
    assert '"attempts": 0' in first["prompt"]
    assert '"attempts": 1' in second["prompt"]


def test_api_subagent_retry_links_the_second_worker_session():
    async def scenario():
        store = InMemorySessionStore()
        now = datetime.now(UTC)
        for index in (1, 2):
            await store.save_session(SessionRecord(
                id=f"child-{index}", title="API worker", status=SessionStatus.completed,
                session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
                mode_key="api_testing", created_at=now, updated_at=now,
            ))

        class Dispatcher:
            def __init__(self):
                self.task_ids = []

            async def dispatch(self, payload, context):
                task_id = payload["workers"][0]["task_id"]
                self.task_ids.append(task_id)
                return {"ok": True, "workers": [{
                    "task_id": task_id,
                    "child_session_id": f"child-{len(self.task_ids)}",
                    "status": "running",
                }]}

        task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
        dispatcher = Dispatcher()
        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([task]), policy=ExecutionPolicy(max_retries=1),
            coordinator_runtime_service=dispatcher, session_store=store,
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(), poll_interval_seconds=0.1,
        )
        await coordinator._dispatch_batch([task])
        assert task.status == "failed"
        task.status = "ready"
        await coordinator._dispatch_batch([task])
        return task, dispatcher.task_ids

    task, task_ids = asyncio.run(scenario())
    assert task_ids == ["read", "read:attempt:2"]
    assert task.attempts == 2
    assert task.worker_session_id == "child-2"
    assert task.last_error != "worker_dispatch_missing"


def test_api_unresolved_dispatch_claim_stops_retry():
    class Dispatcher:
        async def dispatch(self, payload, context):
            return {"ok": False, "status": "recovery_required",
                    "error": "coordinator_dispatch_claim_unresolved", "workers": []}

    async def scenario():
        task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([task]), policy=ExecutionPolicy(max_retries=2),
            coordinator_runtime_service=Dispatcher(), session_store=InMemorySessionStore(),
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
        )
        tasks = await coordinator.run_all()
        return tasks[0], coordinator.has_unsettled_workers

    task, unsettled = asyncio.run(scenario())
    assert unsettled is True
    assert task.status == "failed"
    assert task.attempts == 1
    assert task.last_error == "coordinator_dispatch_claim_unresolved"


def test_api_duplicate_terminal_dispatch_reads_persisted_child_result():
    async def scenario():
        store = InMemorySessionStore()
        now = datetime.now(UTC)
        task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
        task_result = task.model_dump(mode="json")
        task_result.update({
            "status": "completed",
            "response_status": 200,
            "response_headers": {"content-type": "application/json"},
            "response_body": {"orders": []},
            "check_results": [],
            "duration_ms": 5,
        })
        await store.save_session(SessionRecord(
            id="completed-api-child", title="API worker", status=SessionStatus.completed,
            session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
            mode_key="api_testing", created_at=now, updated_at=now,
            messages=[ChatMessage(
                id="api-runner-result", role=MessageRole.tool,
                content=json.dumps({"task_result": task_result, "summary": "HTTP 200"}),
                created_at=now, metadata={"tool_key": "api-test-runner"},
            )],
        ))

        class Dispatcher:
            async def dispatch(self, payload, context):
                return {"ok": True, "status": "duplicate", "workers": [{
                    "task_id": payload["workers"][0]["task_id"],
                    "child_session_id": "completed-api-child",
                    "status": "completed",
                }]}

        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([task]), policy=ExecutionPolicy(max_retries=0),
            coordinator_runtime_service=Dispatcher(), session_store=store,
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
        )
        return (await coordinator.run_all())[0]

    task = asyncio.run(scenario())
    assert task.status == "completed"
    assert task.worker_session_id == "completed-api-child"
    assert task.worker_status == SessionStatus.completed.value
    assert task.response_status == 200


def test_api_failed_worker_receipt_keeps_the_launch_error():
    class Dispatcher:
        async def dispatch(self, payload, context):
            return {"ok": False, "workers": [{
                "task_id": payload["workers"][0]["task_id"],
                "status": "failed", "error": "Unknown agent: api-executor-worker",
                "child_session_id": "",
            }]}

    async def scenario():
        task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([task]), policy=ExecutionPolicy(max_retries=0),
            coordinator_runtime_service=Dispatcher(), session_store=InMemorySessionStore(),
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
        )
        return (await coordinator.run_all())[0]

    task = asyncio.run(scenario())
    assert task.status == "failed"
    assert task.last_error == "Unknown agent: api-executor-worker"


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


def test_api_child_session_stuck_running_reaches_bounded_wait_result():
    async def scenario():
        store = InMemorySessionStore()
        now = datetime.now(UTC)
        child = SessionRecord(
            id="stuck-child", title="API worker", status=SessionStatus.running,
            session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
            mode_key="api_testing", created_at=now, updated_at=now,
        )
        await store.save_session(child)
        task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
        pool = ApiTaskPool([task])

        class Dispatcher:
            calls = 0

            async def dispatch(self, payload, context):
                self.calls += 1
                return {
                    "ok": True,
                    "workers": [{
                        "task_id": payload["workers"][0]["task_id"],
                        "child_session_id": child.id,
                        "status": "running",
                    }],
                }

        dispatcher = Dispatcher()
        coordinator = ApiSubagentCoordinator(
            pool=pool,
            policy=ExecutionPolicy(max_retries=1, request_timeout_seconds=0.05),
            coordinator_runtime_service=dispatcher,
            session_store=store,
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
            poll_interval_seconds=0.1,
        )
        tasks = await asyncio.wait_for(coordinator.run_all(), timeout=1.0)
        return tasks, dispatcher.calls, (await store.get_session(child.id)).status

    tasks, dispatch_calls, child_status = asyncio.run(scenario())

    assert len(tasks) == 1
    assert tasks[0].status == "failed"
    assert tasks[0].worker_session_id == "stuck-child"
    assert tasks[0].worker_status == "running"
    assert tasks[0].last_error.startswith("worker_timeout:")
    assert dispatch_calls == 1
    assert child_status == SessionStatus.running


def test_api_runtime_reports_unsettled_worker_as_incomplete_campaign():
    async def scenario():
        store = InMemorySessionStore()
        now = datetime.now(UTC)
        await store.save_session(SessionRecord(
            id="stuck-child", title="API worker", status=SessionStatus.running,
            session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
            mode_key="api_testing", created_at=now, updated_at=now,
        ))

        class Dispatcher:
            async def dispatch(self, payload, context):
                return {
                    "ok": True,
                    "workers": [{
                        "task_id": payload["workers"][0]["task_id"],
                        "child_session_id": "stuck-child",
                        "status": "running",
                    }],
                }

        campaign = ApiTestCampaign(
            campaign_id="orders",
            tasks=[ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")],
            execution_policy=ExecutionPolicy(request_timeout_seconds=0.05),
        )
        runtime = ApiTestingModeRuntime(
            api_docs_service=None,
            coordinator_runtime_service=Dispatcher(),
            session_store=store,
        )
        state = ApiTestingState(campaign=campaign)
        context = SimpleNamespace(
            session_id="parent", turn_id="turn", trace_id="trace",
            selected_model_key="", context_bundle={},
        )
        result = await asyncio.wait_for(runtime._execute_campaign(state, context), timeout=1.0)
        return result, runtime._build_output(result), (await store.get_session("stuck-child")).status

    state, output, child_status = asyncio.run(scenario())

    assert state.phase == "failed"
    assert state.execution_checkpoint["last_event_type"] == "campaign_execution_incomplete"
    assert state.execution_checkpoint["phase"] == "failed"
    assert state.campaign.tasks[0].worker_status == "running"
    assert state.report is not None
    assert "execution stopped with unsettled worker sessions" in state.report.summary
    assert output["status"] == "failed"
    assert output["summary"] == state.report.summary
    assert "worker_timeout:" in output["report_markdown"]
    assert output["evaluation_result"]["coverage"]["tested_endpoints"] == 0
    classification = output["evaluation_result"]["failure_classifications"][0]
    assert classification["category"] == "worker_unsettled"
    assert classification["is_transient"] is False
    assert "执行未完成" in output["evaluation_result"]["summary"]
    assert any("核对子会话" in item for item in output["evaluation_result"]["recommendations"])
    assert all("接口超时" not in item for item in output["evaluation_result"]["recommendations"])
    assert child_status == SessionStatus.running


def test_api_worker_session_read_cannot_block_campaign_past_deadline():
    async def scenario():
        class HangingStore:
            async def get_session(self, session_id):
                await asyncio.Event().wait()

        class Dispatcher:
            async def dispatch(self, payload, context):
                return {
                    "ok": True,
                    "workers": [{
                        "task_id": payload["workers"][0]["task_id"],
                        "child_session_id": "unresponsive-store-child",
                        "status": "running",
                    }],
                }

        task = ApiTestTask(task_id="read", method="GET", path="/orders", status="ready")
        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([task]),
            policy=ExecutionPolicy(request_timeout_seconds=0.05),
            coordinator_runtime_service=Dispatcher(),
            session_store=HangingStore(),
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
            poll_interval_seconds=0.1,
        )
        return await asyncio.wait_for(coordinator.run_all(), timeout=1.0)

    tasks = asyncio.run(scenario())

    assert tasks[0].status == "failed"
    assert tasks[0].last_error.startswith("worker_timeout:")


def test_api_restored_running_task_stops_before_dispatching_other_work():
    async def scenario():
        class Dispatcher:
            calls = 0

            async def dispatch(self, payload, context):
                self.calls += 1
                return {"ok": True, "workers": []}

        dispatcher = Dispatcher()
        running = ApiTestTask(
            task_id="old-write", method="POST", path="/orders", status="running",
            worker_session_id="existing-child", worker_status="running",
        )
        ready = ApiTestTask(task_id="next-write", method="POST", path="/orders", status="ready")
        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([running, ready]),
            policy=ExecutionPolicy(max_retries=1, request_timeout_seconds=0.05),
            coordinator_runtime_service=dispatcher,
            session_store=InMemorySessionStore(),
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
        )
        tasks = await asyncio.wait_for(coordinator.run_all(), timeout=1.0)
        return tasks, dispatcher.calls, coordinator.has_unsettled_workers

    tasks, dispatch_calls, unsettled = asyncio.run(scenario())

    assert unsettled
    assert dispatch_calls == 0
    assert tasks[0].status == "failed"
    assert tasks[0].last_error.startswith("worker_unsettled:")
    assert tasks[0].worker_session_id == "existing-child"
    assert tasks[1].status == "ready"

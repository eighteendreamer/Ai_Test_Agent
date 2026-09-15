from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.application.test_runs.case_execution import CaseExecutionOutcome
from src.application.test_runs.execution_service import TestRunExecutionService as _ExecutionService
from src.schemas.run_management import RunItemExecuteRequest
from src.schemas.tool_runtime import ToolExecutionRecord
from src.runtime.task_deferred import TaskDeferred
from src.runtime.resource_lease_manager import ResourceLease
from tests.test_case_execution_service import _records


@pytest.mark.asyncio
async def test_execution_service_persists_waiting_resource_and_defers_message():
    now, run, item, case, version = _records()

    class Runs:
        def __init__(self): self.waiting = None
        async def start_item(self, item_id, payload): return item.model_copy(update={"status": "running"})
        async def get_record(self, run_id): return run
        async def get_latest_attempt(self, item_id): return None
        async def mark_waiting_resource(self, item_id, lease_token, reason):
            self.waiting = (item_id, lease_token, reason)
            return item.model_copy(update={"status": "waiting_resource", "waiting_reason": reason})

    class Cases:
        async def get_case(self, case_id): return case
        async def get_version(self, version_id): return version

    class Adapter:
        async def execute(self, **kwargs):
            return CaseExecutionOutcome(
                completion=None,
                tool_record=ToolExecutionRecord(
                    call_id="call-1", tool_key="browser", tool_name="Browser", status="waiting_resource",
                    summary="waiting browser", trace_id="trace-1", input={}, output={"waiting_reason": "browser_capacity"},
                    started_at=now, completed_at=now,
                ),
                verification_results=[],
            )

    runs = Runs()
    service = _ExecutionService(run_service=runs, test_case_service=Cases(), adapter=Adapter())
    with pytest.raises(TaskDeferred, match="browser_capacity"):
        await service.execute_item(item.id, RunItemExecuteRequest(lease_token="lease-1"))
    assert runs.waiting == (item.id, "lease-1", "browser_capacity")


@pytest.mark.asyncio
async def test_execution_resource_requirements_claim_account_environment_and_docker_slots():
    now, run, item, case, version = _records()
    version = version.model_copy(update={"test_data": {
        "runner_arguments": {"test_account_id": "account-1", "environment_id": "env-1", "runner_backend": "docker"},
    }})

    class Manager:
        def __init__(self): self.calls = []
        async def acquire_first_available(self, **kwargs):
            self.calls.append(kwargs)
            return ResourceLease(resource_id=kwargs["resource_ids"][0], resource_type=kwargs["resource_type"],
                                 project_id=kwargs.get("project_id"), session_id=kwargs.get("session_id"),
                                 run_id=kwargs.get("run_id"), run_item_id=kwargs.get("run_item_id"),
                                 attempt_id=kwargs.get("attempt_id"), worker_id=kwargs["worker_id"],
                                 lease_token="lease", fencing_token=1, lease_expire_at=now)
        async def release(self, lease): return True

    settings = type("Settings", (), {
        "app_env": "testing",
        "orchestration": type("Orchestration", (), {"resource_default_lease_seconds": 30, "resource_docker_slots": 2})(),
        "security": type("Security", (), {"security_runner_backend": "local"})(),
    })()
    manager = Manager()
    service = _ExecutionService(run_service=object(), test_case_service=object(), adapter=object(), security_settings=settings)
    service.set_resource_lease_manager(manager)
    leases = await service._claim_execution_resources(run=run, item=item, version=version, attempt_id="attempt-1")
    assert [call["resource_type"] for call in manager.calls] == ["test_account", "environment", "docker"]
    assert manager.calls[-1]["resource_ids"] == ["docker-slot-0", "docker-slot-1"]
    assert all(lease.attempt_id == "attempt-1" for lease in leases)
    await service._release_execution_resources(leases)

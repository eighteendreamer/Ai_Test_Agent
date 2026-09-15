from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.application.test_runs.case_execution import CaseExecutionOutcome
from src.application.test_runs.execution_service import TestRunExecutionService as _ExecutionService
from src.schemas.run_management import RunItemExecuteRequest
from src.schemas.tool_runtime import ToolExecutionRecord
from src.runtime.task_deferred import TaskDeferred
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

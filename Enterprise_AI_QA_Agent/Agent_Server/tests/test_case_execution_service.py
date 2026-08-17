from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.run_management import router
from src.application.test_runs.case_execution import CaseExecutionOutcome
from src.application.test_runs.execution_service import TestRunExecutionService as _ExecutionService
from src.schemas.case_management import TestCaseRecord as _CaseRecord, TestCaseVersionRecord as _CaseVersionRecord
from src.schemas.run_management import (
    RunItemCompleteRequest,
    RunItemExecuteRequest,
    TestCaseResultRecord as _CaseResultRecord,
    TestRunItemRecord as _RunItemRecord,
    TestRunRecord as _RunRecord,
)
from src.schemas.tool_runtime import ToolExecutionRecord


def _records():
    now = datetime.now(timezone.utc)
    run = _RunRecord(
        id="run-1",
        project_id="project-1",
        suite_id="suite-1",
        mode_key="api_testing",
        session_id="session-1",
        created_at=now,
        updated_at=now,
    )
    item = _RunItemRecord(
        id="item-1",
        run_id=run.id,
        case_id="case-1",
        case_version_id="version-1",
        position=1,
        status="claimed",
        attempt_no=1,
        lease_owner="worker-1",
        lease_token="lease-1",
        lease_expires_at=now.replace(year=now.year + 1),
        created_at=now,
        updated_at=now,
    )
    case = _CaseRecord(
        id=item.case_id,
        project_id=run.project_id,
        case_key="case_1",
        title="用例 1",
        mode_key=run.mode_key,
        case_type="api",
        lifecycle_status="active",
        active_version_id=item.case_version_id,
        created_at=now,
        updated_at=now,
    )
    version = _CaseVersionRecord.model_construct(
        id=item.case_version_id,
        case_id=case.id,
        version=1,
        preconditions=[],
        steps=[],
        assertions=[],
        test_data={},
        cleanup=[],
        content_hash="a" * 64,
        source_refs=[],
        model_key="model-1",
        prompt_version="prompt-1",
        skill_versions={"skill": "v1"},
        created_at=now,
    )
    return now, run, item, case, version


@pytest.mark.asyncio
async def test_execution_service_starts_executes_and_completes_with_the_same_lease():
    now, run, item, case, version = _records()

    class FakeRuns:
        def __init__(self):
            self.completed_payload = None

        async def start_item(self, item_id, payload):
            assert payload.lease_token == "lease-1"
            return item.model_copy(update={"status": "running"})

        async def get_record(self, run_id):
            return run

        async def complete_item(self, item_id, payload):
            self.completed_payload = payload
            return _CaseResultRecord(
                id="result-1",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-1",
                attempt_no=1,
                status=payload.status,
                summary=payload.summary,
                actual=payload.actual,
                evidence_refs=payload.evidence_refs,
                artifact_ids=payload.artifact_ids,
                verification_ids=payload.verification_ids,
                tool_job_id=payload.tool_job_id,
                metrics=payload.metrics,
                error_message=payload.error_message,
                payload_hash="b" * 64,
                created_at=now,
            )

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class FakeAdapter:
        async def execute(self, **kwargs):
            running_item = kwargs["item"]
            return CaseExecutionOutcome(
                completion=RunItemCompleteRequest(
                    lease_token=running_item.lease_token,
                    status="passed",
                    summary="真实断言通过",
                    tool_job_id="job-1",
                ),
                tool_record=ToolExecutionRecord(
                    call_id="call-1",
                    job_id="job-1",
                    tool_key="api-test-runner",
                    tool_name="API Test Runner",
                    status="completed",
                    summary="工具完成",
                ),
                verification_results=[],
            )

    runs = FakeRuns()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=FakeAdapter(),
    )

    result = await service.execute_item(
        item.id,
        RunItemExecuteRequest(lease_token="lease-1"),
    )

    assert result.status == "passed"
    assert runs.completed_payload.lease_token == "lease-1"
    assert runs.completed_payload.tool_job_id == "job-1"


@pytest.mark.asyncio
async def test_execution_service_heartbeats_slow_item_and_completes_with_same_lease():
    now, run, item, case, version = _records()
    item = item.model_copy(update={"lease_expires_at": datetime.now(timezone.utc)})

    class FakeRuns:
        def __init__(self):
            self.heartbeats = []
            self.completed_payload = None

        async def start_item(self, item_id, payload):
            assert payload.lease_token == "lease-1"
            return item.model_copy(update={"status": "running"})

        async def get_record(self, run_id):
            return run

        async def heartbeat_item(self, item_id, payload):
            self.heartbeats.append((item_id, payload))
            return item.model_copy(update={"status": "running"})

        async def complete_item(self, item_id, payload):
            self.completed_payload = payload
            return _CaseResultRecord(
                id="result-slow-1",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-slow-1",
                attempt_no=1,
                status=payload.status,
                summary=payload.summary,
                actual=payload.actual,
                evidence_refs=payload.evidence_refs,
                artifact_ids=payload.artifact_ids,
                verification_ids=payload.verification_ids,
                tool_job_id=payload.tool_job_id,
                metrics=payload.metrics,
                error_message=payload.error_message,
                payload_hash="c" * 64,
                created_at=now,
            )

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class SlowAdapter:
        async def execute(self, **kwargs):
            await asyncio.sleep(0.06)
            return CaseExecutionOutcome(
                completion=RunItemCompleteRequest(
                    lease_token=kwargs["item"].lease_token,
                    status="passed",
                    summary="慢执行通过",
                ),
                tool_record=None,
                verification_results=[],
            )

    runs = FakeRuns()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=SlowAdapter(),
        lease_heartbeat_interval_seconds=0.01,
    )

    result = await service.execute_item(item.id, RunItemExecuteRequest(lease_token="lease-1"))

    assert result.status == "passed"
    assert len(runs.heartbeats) >= 3
    assert all(payload.lease_token == "lease-1" for _, payload in runs.heartbeats)
    assert runs.completed_payload.lease_token == "lease-1"
    assert runs.completed_payload.status == "passed"


@pytest.mark.asyncio
async def test_execution_service_heartbeat_failure_turns_result_into_error():
    now, run, item, case, version = _records()

    class FakeRuns:
        def __init__(self):
            self.completed_payload = None

        async def start_item(self, item_id, payload):
            return item.model_copy(update={"status": "running"})

        async def get_record(self, run_id):
            return run

        async def heartbeat_item(self, item_id, payload):
            raise RuntimeError("lease store unavailable")

        async def complete_item(self, item_id, payload):
            self.completed_payload = payload
            return _CaseResultRecord(
                id="result-heartbeat-error",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-heartbeat-error",
                attempt_no=1,
                status=payload.status,
                summary=payload.summary,
                actual=payload.actual,
                error_message=payload.error_message,
                payload_hash="d" * 64,
                created_at=now,
            )

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class SlowAdapter:
        async def execute(self, **kwargs):
            await asyncio.sleep(0.03)
            return CaseExecutionOutcome(
                completion=RunItemCompleteRequest(
                    lease_token=kwargs["item"].lease_token,
                    status="passed",
                    summary="不应被视为通过",
                ),
                tool_record=None,
                verification_results=[],
            )

    runs = FakeRuns()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=SlowAdapter(),
        lease_heartbeat_interval_seconds=0.01,
    )

    result = await service.execute_item(item.id, RunItemExecuteRequest(lease_token="lease-1"))

    assert result.status == "error"
    assert runs.completed_payload.status == "error"
    assert "lease store unavailable" in (runs.completed_payload.error_message or "")
    assert runs.completed_payload.actual["lease_heartbeat_error"]


@pytest.mark.asyncio
async def test_execute_item_system_api_uses_execution_service():
    now, run, item, case, version = _records()

    class FakeExecutionService:
        async def execute_item(self, item_id, payload):
            return _CaseResultRecord(
                id="result-1",
                run_id=run.id,
                run_item_id=item_id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-1",
                attempt_no=1,
                status="passed",
                summary=f"lease={payload.lease_token}",
                payload_hash="b" * 64,
                created_at=now,
            )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.test_run_execution_service = FakeExecutionService()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/run-items/item-1/execute",
            json={"lease_token": "lease-1"},
        )

    assert response.status_code == 200
    assert response.json()["id"] == "result-1"

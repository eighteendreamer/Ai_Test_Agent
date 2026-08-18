from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.run_management import router
from src.application.permissions.permission_service import PermissionService
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.case_execution import CaseExecutionOutcome
from src.application.test_runs.execution_service import TestRunExecutionService as _ExecutionService
from src.schemas.agent import ToolDescriptor
from src.schemas.case_management import TestCaseRecord as _CaseRecord, TestCaseVersionRecord as _CaseVersionRecord
from src.schemas.run_management import (
    RunItemApprovalDecisionRequest,
    RunItemCompleteRequest,
    RunItemExecuteRequest,
    TestCaseResultRecord as _CaseResultRecord,
    TestRunItemRecord as _RunItemRecord,
    TestRunRecord as _RunRecord,
)
from src.schemas.session import ToolApprovalStatus
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


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
async def test_security_case_execution_requires_verified_session_grant():
    now, run, item, case, version = _records()
    run = run.model_copy(update={"mode_key": "security_testing"})
    case = case.model_copy(update={"mode_key": "security_testing"})
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "command_profile": "http_headers_probe",
                    "target": "https://example.test",
                }
            }
        }
    )

    class FakeRuns:
        def __init__(self):
            self.completed_payload = None

        async def start_item(self, item_id, payload):
            return item.model_copy(update={"status": "running"})

        async def get_record(self, run_id):
            return run

        async def complete_item(self, item_id, payload):
            self.completed_payload = payload
            return _CaseResultRecord(
                id="result-security-1",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-security-1",
                attempt_no=1,
                status=payload.status,
                summary=payload.summary,
                actual=payload.actual,
                error_message=payload.error_message,
                payload_hash="e" * 64,
                created_at=now,
            )

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class FakeSessions:
        async def get_session(self, session_id):
            return SimpleNamespace(
                metadata={
                    "security_authorization": {
                        "status": "verified",
                        "targets": ["https://other.example.test"],
                    }
                }
            )

    class CapturingAdapter:
        def __init__(self):
            self.context = None

        async def execute(self, **kwargs):
            self.context = kwargs["trusted_context_bundle"]
            return CaseExecutionOutcome(
                completion=RunItemCompleteRequest(
                    lease_token=kwargs["item"].lease_token,
                    status="blocked",
                    summary="安全用例契约执行到 Runner 前完成验证",
                ),
                tool_record=None,
                verification_results=[],
            )

    runs = FakeRuns()
    adapter = CapturingAdapter()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=adapter,
        session_store=FakeSessions(),
        security_settings=SimpleNamespace(
            security_target_allowlist="example.test",
            app_env="testing",
        ),
    )

    result = await service.execute_item(item.id, RunItemExecuteRequest(lease_token="lease-1"))

    assert result.status == "blocked"
    assert adapter.context["trusted_security_authorization"]["status"] == "verified"
    assert runs.completed_payload.status == "blocked"


@pytest.mark.asyncio
async def test_security_case_execution_blocks_without_matching_grant_before_adapter():
    now, run, item, case, version = _records()
    run = run.model_copy(update={"mode_key": "security_testing"})
    case = case.model_copy(update={"mode_key": "security_testing"})
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "command_profile": "http_headers_probe",
                    "target": "https://other.example.test",
                }
            }
        }
    )

    class FakeRuns:
        def __init__(self):
            self.completed_payload = None

        async def start_item(self, item_id, payload):
            return item.model_copy(update={"status": "running"})

        async def get_record(self, run_id):
            return run

        async def complete_item(self, item_id, payload):
            self.completed_payload = payload
            return _CaseResultRecord(
                id="result-security-blocked",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-security-blocked",
                attempt_no=1,
                status=payload.status,
                summary=payload.summary,
                actual=payload.actual,
                error_message=payload.error_message,
                payload_hash="f" * 64,
                created_at=now,
            )

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class FakeSessions:
        async def get_session(self, session_id):
            return SimpleNamespace(
                metadata={
                    "security_authorization": {
                        "status": "verified",
                        "targets": ["https://example.test"],
                    }
                }
            )

    class FailingAdapter:
        async def execute(self, **kwargs):
            raise AssertionError("security adapter must not run outside grant scope")

    runs = FakeRuns()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=FailingAdapter(),
        session_store=FakeSessions(),
        security_settings=SimpleNamespace(
            security_target_allowlist="example.test",
            app_env="testing",
        ),
    )

    result = await service.execute_item(item.id, RunItemExecuteRequest(lease_token="lease-1"))

    assert result.status == "blocked"
    assert "outside" in (runs.completed_payload.error_message or "")


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


@pytest.mark.asyncio
async def test_run_item_approval_system_api_resolves_waiting_item():
    now, run, item, case, version = _records()

    class FakeExecutionService:
        async def resolve_item_approval(self, item_id, payload):
            assert item_id == item.id
            assert payload.approval_id == "approval-route-1"
            assert payload.decision == ToolApprovalStatus.approved
            return _CaseResultRecord(
                id="result-approval-route",
                run_id=run.id,
                run_item_id=item_id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-approval-route",
                attempt_no=1,
                status="passed",
                summary="approval route resumed item",
                payload_hash="c" * 64,
                created_at=now,
            )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.test_run_execution_service = FakeExecutionService()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/run-items/item-1/approval",
            json={
                "approval_id": "approval-route-1",
                "decision": "approved",
                "reason": "approved for test",
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == "result-approval-route"


@pytest.mark.asyncio
async def test_high_risk_security_case_waits_for_approval_without_calling_runner_or_completing():
    now, run, item, case, version = _records()
    run = run.model_copy(update={"mode_key": "security_testing"})
    case = case.model_copy(update={"mode_key": "security_testing"})
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "command_profile": "hydra_basic_login",
                    "target": "https://example.test",
                }
            }
        }
    )

    class FakeRuns:
        def __init__(self):
            self.completed_payload = None
            self.waiting = None
            self.current_item = item
            self.result = None

        async def start_item(self, item_id, payload):
            self.current_item = self.current_item.model_copy(update={"status": "running"})
            return self.current_item

        async def get_record(self, run_id):
            return run

        async def mark_waiting_approval(self, item_id, payload):
            self.waiting = payload
            self.current_item = self.current_item.model_copy(
                update={
                    "status": "waiting_approval",
                    "lease_expires_at": None,
                    "approval_id": payload.approval_id,
                    "tool_job_id": payload.tool_job_id,
                }
            )
            return self.current_item

        async def get_item(self, item_id):
            return self.current_item

        async def resume_waiting_approval(self, item_id, approval_id, lease_seconds=90):
            assert approval_id == self.current_item.approval_id
            self.current_item = self.current_item.model_copy(
                update={"status": "claimed", "lease_expires_at": now.replace(year=now.year + 1)}
            )
            return self.current_item

        async def complete_item(self, item_id, payload):
            self.completed_payload = payload
            self.current_item = self.current_item.model_copy(
                update={"status": payload.status, "result_id": "result-approved-1"}
            )
            self.result = _CaseResultRecord(
                id="result-approved-1",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-approved-1",
                attempt_no=1,
                status=payload.status,
                summary=payload.summary,
                actual=payload.actual,
                tool_job_id=payload.tool_job_id,
                payload_hash="9" * 64,
                created_at=now,
            )
            return self.result

        async def get_result(self, result_id):
            assert self.result is not None
            assert result_id == self.result.id
            return self.result

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class FakeSessions:
        def __init__(self):
            self.approvals = []

        async def get_session(self, session_id):
            return SimpleNamespace(
                metadata={
                    "security_authorization": {
                        "status": "verified",
                        "targets": [
                            "https://example.test",
                            "https://changed.example.test",
                        ],
                    }
                }
            )

        async def save_approval(self, session_id, approval):
            self.approvals.append(approval)

        async def list_approvals(self, session_id):
            return list(self.approvals)

        async def resolve_approval(self, session_id, approval_id, status, reason=None):
            approval = next(item for item in self.approvals if item.id == approval_id)
            approval.status = status
            approval.decision_note = reason
            approval.resolved_at = now
            return approval

    class FakeJobs:
        def __init__(self):
            self.waiting = []
            self.resume_requests = []
            self.resume_available = False

        async def create_job(self, **kwargs):
            return SimpleNamespace(id="job-approval-1")

        async def mark_waiting_approval(self, job_id, summary, metadata=None):
            self.waiting.append((job_id, summary, metadata))

        async def get_job(self, job_id):
            if not self.resume_available:
                return None
            return SimpleNamespace(id=job_id)

        async def request_resume(self, job_id, reason=None):
            self.resume_requests.append((job_id, reason))
            if not self.resume_available:
                return None
            return SimpleNamespace(id=job_id)

    class ApprovalAdapter:
        def __init__(self):
            self.execute_calls = 0
            self.allow_execute = False
            self.execute_kwargs = None

        def build_invocation(self, **kwargs):
            runner_arguments = kwargs["version"].test_data.get("runner_arguments", {})
            tool = ToolDescriptor(
                key="security-scan-runner",
                name="Security Scan Runner",
                description="test",
                category="execution",
                owner_mode_key="security_testing",
            )
            call = ModelToolCall(
                id="call-approval-1",
                name=tool.key,
                arguments={
                    "command_profile": runner_arguments["command_profile"],
                    "target": runner_arguments["target"],
                },
            )
            return SimpleNamespace(
                tool=tool,
                call=call,
                context=ToolExecutionContext(
                    session_id=run.session_id,
                    turn_id=call.id,
                    trace_id="trace-approval-1",
                    user_message="security approval",
                    normalized_input="security approval",
                    context_bundle=kwargs["trusted_context_bundle"],
                ),
            )

        async def execute(self, **kwargs):
            self.execute_calls += 1
            self.execute_kwargs = kwargs
            if not self.allow_execute:
                raise AssertionError("runner must not execute before approval")
            return CaseExecutionOutcome(
                completion=RunItemCompleteRequest(
                    lease_token=kwargs["item"].lease_token,
                    status="passed",
                    summary="approved security runner passed",
                    tool_job_id=kwargs["tool_job_id"],
                ),
                tool_record=ToolExecutionRecord(
                    call_id="call-approval-1",
                    job_id=kwargs["tool_job_id"],
                    tool_key="security-scan-runner",
                    tool_name="Security Scan Runner",
                    status="completed",
                    summary="approved security runner passed",
                ),
                verification_results=[],
            )

    runs = FakeRuns()
    sessions = FakeSessions()
    jobs = FakeJobs()
    adapter = ApprovalAdapter()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=adapter,
        session_store=sessions,
        permission_service=PermissionService(),
        tool_job_service=jobs,
        security_settings=SimpleNamespace(
            security_target_allowlist="example.test,changed.example.test",
            app_env="testing",
        ),
    )

    pending = await service.execute_item(
        item.id,
        RunItemExecuteRequest(lease_token="lease-1"),
    )

    assert pending.status == "waiting_approval"
    assert pending.approval_id == sessions.approvals[0].id
    assert pending.tool_job_id == "job-approval-1"
    assert runs.waiting.approval_id == pending.approval_id
    assert runs.completed_payload is None
    assert adapter.execute_calls == 0

    with pytest.raises(ValueError, match="does not match run item"):
        await service.resolve_item_approval(
            item.id,
            SimpleNamespace(
                approval_id="approval-forged",
                decision=ToolApprovalStatus.approved,
                reason="stale approval callback",
            ),
        )
    assert sessions.approvals[0].status == ToolApprovalStatus.pending
    assert runs.current_item.status == "waiting_approval"
    assert jobs.resume_requests == []
    assert adapter.execute_calls == 0

    original_version = version
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "command_profile": "hydra_basic_login",
                    "target": "https://changed.example.test",
                }
            }
        }
    )
    with pytest.raises(ValueError, match="scope changed"):
        await service.resolve_item_approval(
            item.id,
            RunItemApprovalDecisionRequest(
                approval_id=pending.approval_id,
                decision=ToolApprovalStatus.approved,
                reason="scope must be re-approved",
            ),
        )
    assert sessions.approvals[0].status == ToolApprovalStatus.pending
    version = original_version

    adapter.allow_execute = True
    with pytest.raises(RuntimeError, match="Tool job not found"):
        await service.resolve_item_approval(
            item.id,
            RunItemApprovalDecisionRequest(
                approval_id=pending.approval_id,
                decision=ToolApprovalStatus.approved,
                reason="approved but job temporarily unavailable",
            ),
        )
    assert adapter.execute_calls == 0
    assert runs.current_item.status == "waiting_approval"
    assert jobs.resume_requests == []
    assert sessions.approvals[0].status == ToolApprovalStatus.pending
    jobs.resume_available = True
    result = await service.resolve_item_approval(
        item.id,
        RunItemApprovalDecisionRequest(
            approval_id=pending.approval_id,
            decision=ToolApprovalStatus.approved,
            reason="approved for authorized test",
        ),
    )

    assert result.status == "passed"
    assert result.tool_job_id == "job-approval-1"
    assert sessions.approvals[0].status == ToolApprovalStatus.approved
    assert jobs.resume_requests[0][0] == "job-approval-1"
    assert adapter.execute_kwargs["tool_job_id"] == "job-approval-1"
    assert adapter.execute_kwargs["server_approval_granted"] is True

    repeated = await service.resolve_item_approval(
        item.id,
        RunItemApprovalDecisionRequest(
            approval_id=pending.approval_id,
            decision=ToolApprovalStatus.approved,
            reason="duplicate approval callback",
        ),
    )
    assert repeated.id == result.id
    assert adapter.execute_calls == 1
    with pytest.raises(ValueError, match="already resolved as approved"):
        await service.resolve_item_approval(
            item.id,
            RunItemApprovalDecisionRequest(
                approval_id=pending.approval_id,
                decision=ToolApprovalStatus.denied,
                reason="conflicting approval callback",
            ),
        )


@pytest.mark.asyncio
async def test_security_approval_denial_blocks_once_without_running_adapter():
    now, run, item, case, version = _records()
    run = run.model_copy(update={"mode_key": "security_testing"})
    case = case.model_copy(update={"mode_key": "security_testing"})
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "command_profile": "hydra_basic_login",
                    "target": "https://example.test",
                }
            }
        }
    )

    class FakeRuns:
        def __init__(self):
            self.current_item = item
            self.denials = []
            self.result = None
            self.start_calls = 0
            self.resume_calls = 0

        async def start_item(self, item_id, payload):
            self.start_calls += 1
            self.current_item = self.current_item.model_copy(update={"status": "running"})
            return self.current_item

        async def get_record(self, run_id):
            return run

        async def mark_waiting_approval(self, item_id, payload):
            self.current_item = self.current_item.model_copy(
                update={
                    "status": "waiting_approval",
                    "lease_expires_at": None,
                    "approval_id": payload.approval_id,
                    "tool_job_id": payload.tool_job_id,
                }
            )
            return self.current_item

        async def get_item(self, item_id):
            return self.current_item

        async def resume_waiting_approval(self, item_id, approval_id, lease_seconds=90):
            self.resume_calls += 1
            raise AssertionError("denied approval must not resume the preserved lease")

        async def finalize_denied_approval(self, item_id, payload):
            self.denials.append(payload)
            self.current_item = self.current_item.model_copy(
                update={"status": "blocked", "result_id": "result-denied-1"}
            )
            self.result = _CaseResultRecord(
                id="result-denied-1",
                run_id=run.id,
                run_item_id=item.id,
                case_id=case.id,
                case_version_id=version.id,
                attempt_id="attempt-denied-1",
                attempt_no=1,
                status="blocked",
                summary=payload.summary,
                actual=payload.actual,
                error_message=payload.error_message,
                tool_job_id=payload.tool_job_id,
                payload_hash="d" * 64,
                created_at=now,
            )
            return self.result

        async def get_result(self, result_id):
            assert self.result is not None
            assert result_id == self.result.id
            return self.result

    class FakeCases:
        async def get_case(self, case_id):
            return case

        async def get_version(self, version_id):
            return version

    class FakeSessions:
        def __init__(self):
            self.approvals = []

        async def get_session(self, session_id):
            return SimpleNamespace(
                metadata={
                    "security_authorization": {
                        "status": "verified",
                        "targets": ["https://example.test"],
                    }
                }
            )

        async def save_approval(self, session_id, approval):
            self.approvals.append(approval)

        async def list_approvals(self, session_id):
            return list(self.approvals)

        async def resolve_approval(self, session_id, approval_id, status, reason=None):
            approval = next(item for item in self.approvals if item.id == approval_id)
            approval.status = status
            approval.decision_note = reason
            approval.resolved_at = now
            return approval

    class FakeJobs:
        def __init__(self):
            self.denied = []

        async def create_job(self, **kwargs):
            return SimpleNamespace(id="job-denied-1")

        async def mark_waiting_approval(self, job_id, summary, metadata=None):
            return None

        async def get_job(self, job_id):
            return SimpleNamespace(id=job_id)

        async def mark_denied(self, job_id, summary, output_payload=None):
            self.denied.append((job_id, summary, output_payload))
            return SimpleNamespace(id=job_id)

    class NeverRunAdapter:
        def __init__(self):
            self.execute_calls = 0

        def build_invocation(self, **kwargs):
            tool = ToolDescriptor(
                key="security-scan-runner",
                name="Security Scan Runner",
                description="test",
                category="execution",
                owner_mode_key="security_testing",
            )
            call = ModelToolCall(
                id="call-denied-1",
                name=tool.key,
                arguments={
                    "command_profile": "hydra_basic_login",
                    "target": "https://example.test",
                },
            )
            return SimpleNamespace(
                tool=tool,
                call=call,
                context=ToolExecutionContext(
                    session_id=run.session_id,
                    turn_id=call.id,
                    trace_id="trace-denied-1",
                    user_message="security denial",
                    normalized_input="security denial",
                    context_bundle=kwargs["trusted_context_bundle"],
                ),
            )

        async def execute(self, **kwargs):
            self.execute_calls += 1
            raise AssertionError("denied security case must not run adapter")

    runs = FakeRuns()
    sessions = FakeSessions()
    jobs = FakeJobs()
    adapter = NeverRunAdapter()
    service = _ExecutionService(
        run_service=runs,
        test_case_service=FakeCases(),
        adapter=adapter,
        session_store=sessions,
        permission_service=PermissionService(),
        tool_job_service=jobs,
        security_settings=SimpleNamespace(
            security_target_allowlist="example.test",
            app_env="testing",
        ),
    )

    pending = await service.execute_item(item.id, RunItemExecuteRequest(lease_token="lease-1"))
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "command_profile": "hydra_basic_login",
                    "target": "https://authorization-now-invalid.example.test",
                }
            }
        }
    )
    result = await service.resolve_item_approval(
        item.id,
        RunItemApprovalDecisionRequest(
            approval_id=pending.approval_id,
            decision=ToolApprovalStatus.denied,
            reason="target is outside the approved change window",
        ),
    )

    assert pending.status == "waiting_approval"
    assert result.status == "blocked"
    assert adapter.execute_calls == 0
    assert jobs.denied[0][0] == "job-denied-1"
    assert runs.denials[0].actual["approval_id"] == pending.approval_id
    assert runs.start_calls == 1
    assert runs.resume_calls == 0

    repeated = await service.resolve_item_approval(
        item.id,
        RunItemApprovalDecisionRequest(
            approval_id=pending.approval_id,
            decision=ToolApprovalStatus.denied,
            reason="duplicate denial callback",
        ),
    )
    assert repeated.id == result.id
    assert len(runs.denials) == 1

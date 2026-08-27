from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.test_runs.case_execution import CaseExecutionAdapter
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.case_management import (
    TestCaseAssertion as _CaseAssertion,
    TestCaseRecord as _CaseRecord,
    TestCaseSourceRef as _CaseSourceRef,
    TestCaseStep as _CaseStep,
    TestCaseVersionRecord as _CaseVersionRecord,
)
from src.schemas.run_management import TestRunItemRecord as _RunItemRecord, TestRunRecord as _RunRecord
from src.schemas.session import VerificationStatus
from src.schemas.tool_job import ToolJobStatus
from src.schemas.tool_runtime import ToolExecutionRecord


def _fixture(mode_key: str = "api_testing"):
    now = datetime.now(timezone.utc)
    case = _CaseRecord(
        id="case-1",
        project_id="project-1",
        case_key="order_read",
        title="读取订单",
        mode_key=mode_key,
        case_type="happy_path",
        lifecycle_status="active",
        active_version_id="version-1",
        created_at=now,
        updated_at=now,
    )
    version = _CaseVersionRecord(
        id="version-1",
        case_id=case.id,
        version=1,
        steps=[
            _CaseStep(
                order=1,
                action="GET /orders/42",
                data={"endpoint": "https://example.test/orders/42", "method": "GET"},
            )
        ],
        assertions=[
            _CaseAssertion(kind="status_code", expected=200, description="返回成功")
        ],
        test_data={
            "runner_arguments": {
                "objective": "执行固定订单用例",
                "task": {
                    "task_id": "case-1",
                    "method": "GET",
                    "path": "/orders/42",
                    "full_url": "https://example.test/orders/42",
                    "assertions": [{"kind": "status_code", "expected": 200}],
                },
            }
        },
        source_refs=[_CaseSourceRef(source_type="api_doc", source_id="doc-1")],
        model_key="model-1",
        prompt_version="prompt-1",
        skill_versions={"generate-test-cases": "sha256:case"},
        content_hash="a" * 64,
        created_at=now,
    )
    run = _RunRecord(
        id="run-1",
        project_id=case.project_id,
        suite_id="suite-1",
        mode_key=mode_key,
        session_id="session-1",
        created_at=now,
        updated_at=now,
    )
    item = _RunItemRecord(
        id="item-1",
        run_id=run.id,
        case_id=case.id,
        case_version_id=version.id,
        position=1,
        status="running",
        attempt_no=1,
        lease_owner="worker-1",
        lease_token="lease-1",
        created_at=now,
        updated_at=now,
    )
    return case, version, run, item


def test_adapter_builds_fixed_version_invocation_without_mutating_case_data():
    case, version, run, item = _fixture()
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda mode_key: ToolDescriptor(
            key="api-test-runner",
            name="API Test Runner",
            description="test",
            category="execution",
            owner_mode_key=mode_key,
        )
    )

    invocation = adapter.build_invocation(case=case, version=version, run=run, item=item)

    assert invocation.tool.key == "api-test-runner"
    assert invocation.call.arguments["worker_action"] == "execute_task"
    assert invocation.call.arguments["task"]["task_id"] == "case-1"
    assert invocation.call.arguments["test_case"]["version_id"] == "version-1"
    assert version.test_data["runner_arguments"]["task"]["task_id"] == "case-1"
    assert invocation.context.context_bundle["test_case"]["case_id"] == "case-1"


def test_api_adapter_derives_runner_task_from_generic_case_envelope():
    case, version, run, item = _fixture()
    version = version.model_copy(update={"test_data": {}})
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda mode_key: ToolDescriptor(
            key="api-test-runner",
            name="API Test Runner",
            description="test",
            category="execution",
            owner_mode_key=mode_key,
        )
    )

    invocation = adapter.build_invocation(case=case, version=version, run=run, item=item)

    assert invocation.call.arguments["worker_action"] == "execute_task"
    assert invocation.call.arguments["task"]["full_url"] == "https://example.test/orders/42"
    assert invocation.call.arguments["task"]["assertions"] == [
        {
            "kind": "status_code",
            "expected": 200,
            "path": "",
            "description": "返回成功",
        }
    ]


def test_adapter_rejects_execution_without_a_real_project_session():
    case, version, run, item = _fixture()
    run = run.model_copy(update={"session_id": None})
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda mode_key: ToolDescriptor(
            key="api-test-runner",
            name="API Test Runner",
            description="test",
            category="execution",
            owner_mode_key=mode_key,
        )
    )

    with pytest.raises(ValueError, match="requires a bound session"):
        adapter.build_invocation(case=case, version=version, run=run, item=item)


@pytest.mark.parametrize(
    ("mode_key", "tool_key"),
    [
        ("smoke_testing", "smoke-suite-runner"),
        ("ui_automation", "ui-automation-runner"),
    ],
)
def test_adapter_translates_generic_http_step_for_non_api_required_modes(
    mode_key,
    tool_key,
):
    case, version, run, item = _fixture(mode_key)
    version = version.model_copy(update={"test_data": {}})
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key=tool_key,
            name=tool_key,
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    invocation = adapter.build_invocation(case=case, version=version, run=run, item=item)

    if mode_key == "smoke_testing":
        assert invocation.call.arguments["action"] == "execute_approved_plan"
        smoke_case = invocation.call.arguments["plan"]["cases"][0]
        assert smoke_case["case_id"] == case.id
        assert smoke_case["steps"][0]["api"]["url"] == "https://example.test/orders/42"
        assert smoke_case["steps"][0]["api"]["expected_status"] == 200
    else:
        assert invocation.call.arguments["subdirection"] == "test_execution"
        assert invocation.call.arguments["target_url"] == "https://example.test/orders/42"


def test_performance_adapter_derives_target_but_preserves_reviewed_load_and_safety():
    case, version, run, item = _fixture("performance_testing")
    version = version.model_copy(
        update={
            "test_data": {
                "runner_arguments": {
                    "target_rate_rps": 25,
                    "duration_seconds": 30,
                    "run_intent": "regression",
                    "sla_p95_ms": 250,
                    "sla_error_rate": 0.01,
                    "confirm_target": True,
                }
            }
        }
    )
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="performance-test-runner",
            name="Performance Test Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    invocation = adapter.build_invocation(case=case, version=version, run=run, item=item)

    arguments = invocation.call.arguments
    assert arguments["target_url"] == "https://example.test/orders/42"
    assert arguments["method"] == "GET"
    assert arguments["run_intent"] == "regression"
    assert arguments["target_rate_rps"] == 25
    assert arguments["duration_seconds"] == 30
    assert arguments["sla_p95_ms"] == 250
    assert arguments["confirm_target"] is True
    assert version.test_data["runner_arguments"].get("target_url") is None


@pytest.mark.parametrize(
    ("runner_arguments", "message"),
    [
        (
            {"target_rate_rps": 25, "run_intent": "regression"},
            "explicit confirm_target=true",
        ),
        (
            {"run_intent": "regression", "confirm_target": True},
            "positive target_rate_rps or virtual_users",
        ),
        (
            {"target_rate_rps": 25, "confirm_target": True},
            "explicit run_intent",
        ),
    ],
)
def test_performance_adapter_blocks_incomplete_reviewed_execution_contract(
    runner_arguments,
    message,
):
    case, version, run, item = _fixture("performance_testing")
    version = version.model_copy(
        update={"test_data": {"runner_arguments": runner_arguments}}
    )
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="performance-test-runner",
            name="Performance Test Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    with pytest.raises(ValueError, match=message):
        adapter.build_invocation(case=case, version=version, run=run, item=item)


def test_security_mode_without_profile_is_blocked_before_runtime():
    case, version, run, item = _fixture("security_testing")
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    with pytest.raises(ValueError, match="requires explicit command_profile"):
        adapter.build_invocation(case=case, version=version, run=run, item=item)


def test_security_adapter_requires_explicit_profile_and_preserves_trusted_context():
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(
        update={
            "assertions": [
                _CaseAssertion(kind="runner_success", expected=True),
                _CaseAssertion(kind="parsed_field", target="status_code", expected=200),
            ],
            "test_data": {
                "runner_arguments": {
                    "command_profile": "http_headers_probe",
                    "target": "https://example.test",
                    "risk_level": "low",
                }
            }
        }
    )
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    invocation = adapter.build_invocation(
        case=case,
        version=version,
        run=run,
        item=item,
        trusted_context_bundle={
            "trusted_security_authorization": {
                "status": "verified",
                "targets": ["https://example.test"],
            }
        },
    )

    assert invocation.call.arguments["worker_action"] == "execute_security_task"
    assert invocation.call.arguments["command_profile"] == "http_headers_probe"
    assert invocation.call.arguments["task"]["target"] == "https://example.test"
    assert invocation.context.context_bundle["trusted_security_authorization"]["status"] == "verified"


def test_security_adapter_accepts_assertions_declared_by_selected_profile():
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(
        update={
            "assertions": [
                _CaseAssertion(kind="runner_success", expected=True),
                _CaseAssertion(kind="parsed_field", target="status_code", expected=200),
            ],
            "test_data": {
                "runner_arguments": {
                    "command_profile": "http_headers_probe",
                    "target": "https://example.test",
                }
            },
        }
    )
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    invocation = adapter.build_invocation(case=case, version=version, run=run, item=item)

    assert invocation.call.arguments["command_profile"] == "http_headers_probe"


@pytest.mark.parametrize(
    ("profile_key", "assertion", "message"),
    [
        (
            "whatweb_fingerprint",
            _CaseAssertion(kind="finding_count", operator="lte", expected=0),
            "does not support assertion kind",
        ),
        (
            "http_headers_probe",
            _CaseAssertion(kind="parsed_field", target="unknown_field", expected="value"),
            "does not expose parsed field",
        ),
        (
            "http_headers_probe",
            _CaseAssertion(
                kind="parsed_field",
                target="headers.x-frame-options",
                expected="DENY",
            ),
            "does not expose parsed field",
        ),
        (
            "http_headers_probe",
            _CaseAssertion(kind="finding_count", operator="contains", expected=0),
            "does not support operator",
        ),
    ],
)
def test_security_adapter_blocks_assertions_outside_selected_profile_capabilities(
    profile_key,
    assertion,
    message,
):
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(
        update={
            "assertions": [assertion],
            "test_data": {
                "runner_arguments": {
                    "command_profile": profile_key,
                    "target": "https://example.test",
                }
            },
        }
    )
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    with pytest.raises(ValueError, match=message):
        adapter.build_invocation(case=case, version=version, run=run, item=item)


def test_security_adapter_blocks_missing_profile():
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(update={"test_data": {"runner_arguments": {"target": "https://example.test"}}})
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    with pytest.raises(ValueError, match="requires explicit command_profile"):
        adapter.build_invocation(case=case, version=version, run=run, item=item)


@pytest.mark.asyncio
async def test_security_case_without_profile_never_calls_tool_runtime():
    case, version, run, item = _fixture("security_testing")
    calls = []

    class FakeRuntime:
        async def execute(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("security runtime must not be called")

    class FakeJobs:
        async def get_job_detail(self, job_id):
            raise AssertionError("security ToolJob must not be created")

    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        ),
        runtime_service=FakeRuntime(),
        tool_job_service=FakeJobs(),
    )

    with pytest.raises(ValueError, match="requires explicit command_profile"):
        await adapter.execute(case=case, version=version, run=run, item=item)

    assert calls == []


@pytest.mark.asyncio
async def test_adapter_projects_real_tool_job_and_verification_output():
    case, version, run, item = _fixture()

    class FakeRuntime:
        async def execute(self, tool, call, context):
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="completed",
                summary="工具已执行",
                trace_id=context.trace_id,
                job_id="job-1",
                input=call.arguments,
                output={"summary": "模型摘要，不作为事实结果"},
            )

    class FakeJobs:
        async def get_job_detail(self, job_id):
            return SimpleNamespace(
                id=job_id,
                status="completed",
                summary="真实作业完成",
                output_payload={
                    "status": "completed",
                    "ok": True,
                    "summary": "真实 API 断言通过",
                    "checks": [{"passed": True, "name": "status", "actual": 200}],
                },
                artifacts=[SimpleNamespace(id="artifact-1", label="响应证据", path="rustfs://bucket/a")],
            )

    adapter = CaseExecutionAdapter(
        tool_resolver=lambda mode_key: ToolDescriptor(
            key="api-test-runner",
            name="API Test Runner",
            description="test",
            category="execution",
            owner_mode_key=mode_key,
        ),
        runtime_service=FakeRuntime(),
        tool_job_service=FakeJobs(),
    )

    outcome = await adapter.execute(case=case, version=version, run=run, item=item)

    assert outcome.completion.status == "passed"
    assert outcome.completion.tool_job_id == "job-1"
    assert outcome.completion.artifact_ids == ["artifact-1"]
    assert len(outcome.verification_results) == 1
    assert outcome.completion.verification_ids == [outcome.verification_results[0].id]
    assert outcome.completion.actual["verification_results"][0]["passed_count"] == 1


@pytest.mark.asyncio
async def test_security_case_closes_real_runtime_job_artifact_verification_chain():
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(
        update={
            "assertions": [_CaseAssertion(kind="runner_success", expected=True)],
            "test_data": {
                "runner_arguments": {
                    "command_profile": "http_headers_probe",
                    "target": "https://example.test",
                }
            },
        }
    )
    store = InMemoryToolJobStore()
    jobs = ToolJobService(store)
    runtime = ToolRuntimeService(tool_job_service=jobs)
    runner_calls = []

    async def deterministic_security_runner(arguments, context):
        runner_calls.append((arguments, context))
        return {
            "status": "completed",
            "ok": True,
            "semantic_success": True,
            "summary": "安全 Runner 已完成并生成证据",
            "command_profile": arguments["command_profile"],
            "target": arguments["target"],
            "parsed_result": {"status_code": 200},
            "findings": [],
            "artifacts": [
                {
                    "type": "json",
                    "label": "security-runner-evidence",
                    "content": '{"status":"completed","ok":true}',
                }
            ],
        }

    runtime._handlers["security-scan-runner"] = deterministic_security_runner
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        ),
        runtime_service=runtime,
        tool_job_service=jobs,
    )

    outcome = await adapter.execute(
        case=case,
        version=version,
        run=run,
        item=item,
        trusted_context_bundle={
            "trusted_security_authorization": {
                "status": "verified",
                "targets": ["https://example.test"],
            }
        },
    )

    job = await jobs.get_job(outcome.completion.tool_job_id)
    artifacts = await jobs.list_artifacts(tool_job_id=job.id)
    verification = outcome.verification_results[0]
    assert len(runner_calls) == 1
    assert job.status == ToolJobStatus.completed
    assert len(artifacts) == 1
    assert artifacts[0].tool_job_id == job.id
    assert outcome.tool_record.job_id == job.id
    assert outcome.completion.status == "passed"
    assert outcome.completion.tool_job_id == job.id
    assert outcome.completion.artifact_ids == [artifacts[0].id]
    assert verification.status == VerificationStatus.passed
    assert verification.evidence[0].source_id == job.id
    assert outcome.completion.verification_ids == [verification.id]


@pytest.mark.parametrize(
    ("mode_key", "tool_key", "runner_output"),
    [
        (
            "ui_automation",
            "ui-automation-runner",
            {
                "status": "completed",
                "ok": True,
                "summary": "UI assertions completed",
                "verification_result": {
                    "summary": "UI assertion passed",
                    "checks": [{"name": "order title", "passed": True}],
                },
                "artifacts": [
                    {
                        "type": "screenshot",
                        "label": "ui-final-state",
                        "content": "deterministic-ui-evidence",
                    }
                ],
            },
        ),
        (
            "compatibility_testing",
            "compatibility-test-runner",
            {
                "status": "completed",
                "ok": True,
                "summary": "Compatibility matrix completed",
                "runner_summary": {
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "pending": 0,
                },
                "artifacts": [
                    {
                        "type": "json",
                        "label": "compatibility-matrix",
                        "content": '{"chrome":"passed"}',
                    }
                ],
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_ui_and_compatibility_close_real_system_evidence_chain(
    mode_key,
    tool_key,
    runner_output,
):
    case, version, run, item = _fixture(mode_key)
    if mode_key == "compatibility_testing":
        version = version.model_copy(
            update={
                "test_data": {
                    "runner_arguments": {
                        "objective": "Run one reviewed compatibility case",
                    }
                }
            }
        )
    store = InMemoryToolJobStore()
    jobs = ToolJobService(store)
    runtime = ToolRuntimeService(tool_job_service=jobs)
    calls = []

    async def deterministic_runner(arguments, context):
        calls.append((arguments, context))
        return runner_output

    runtime._handlers[tool_key] = deterministic_runner
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key=tool_key,
            name=tool_key,
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        ),
        runtime_service=runtime,
        tool_job_service=jobs,
    )

    outcome = await adapter.execute(case=case, version=version, run=run, item=item)

    job = await jobs.get_job(outcome.completion.tool_job_id)
    artifacts = await jobs.list_artifacts(tool_job_id=job.id)
    verification = outcome.verification_results[0]
    assert len(calls) == 1
    assert job.status == ToolJobStatus.completed
    assert len(artifacts) == 1
    assert artifacts[0].tool_job_id == job.id
    assert outcome.completion.status == "passed"
    assert outcome.completion.artifact_ids == [artifacts[0].id]
    assert verification.status == VerificationStatus.passed
    assert any(item.source_id == job.id for item in verification.evidence)
    assert outcome.completion.verification_ids == [verification.id]


@pytest.mark.asyncio
async def test_security_approval_resume_reuses_tool_job_and_injects_server_only_grant():
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(
        update={
            "assertions": [_CaseAssertion(kind="runner_success", expected=True)],
            "test_data": {
                "runner_arguments": {
                    "command_profile": "hydra_basic_login",
                    "target": "https://example.test",
                }
            },
        }
    )
    captured = {}

    class FakeRuntime:
        async def execute(self, tool, call, context):
            captured["arguments"] = call.arguments
            captured["tool_job_id"] = context.tool_job_id
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="completed",
                summary="approved security execution",
                trace_id=context.trace_id,
                job_id=context.tool_job_id,
                input=call.arguments,
            )

    class FakeJobs:
        async def get_job_detail(self, job_id):
            return SimpleNamespace(
                id=job_id,
                summary="approved security execution",
                output_payload={
                    "status": "completed",
                    "ok": True,
                    "semantic_success": True,
                    "summary": "approved security execution",
                    "command_profile": "hydra_basic_login",
                    "parsed_result": {"credential_count": 0},
                    "findings": [],
                },
                artifacts=[],
            )

    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        ),
        runtime_service=FakeRuntime(),
        tool_job_service=FakeJobs(),
    )

    outcome = await adapter.execute(
        case=case,
        version=version,
        run=run,
        item=item,
        trusted_context_bundle={"trusted_security_authorization": {"status": "verified"}},
        tool_job_id="job-waiting-1",
        server_approval_granted=True,
    )

    assert captured["tool_job_id"] == "job-waiting-1"
    assert captured["arguments"]["_server_approval_granted"] is True
    assert outcome.completion.tool_job_id == "job-waiting-1"
    assert outcome.completion.status == "passed"


def test_security_case_cannot_forge_server_only_approval_argument():
    case, version, run, item = _fixture("security_testing")
    version = version.model_copy(
        update={
            "assertions": [_CaseAssertion(kind="runner_success", expected=True)],
            "test_data": {
                "runner_arguments": {
                    "command_profile": "hydra_basic_login",
                    "target": "https://example.test",
                    "_server_approval_granted": True,
                }
            },
        }
    )
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda owner_mode_key: ToolDescriptor(
            key="security-scan-runner",
            name="Security Scan Runner",
            description="test",
            category="execution",
            owner_mode_key=owner_mode_key,
        )
    )

    invocation = adapter.build_invocation(
        case=case,
        version=version,
        run=run,
        item=item,
        trusted_context_bundle={"trusted_security_authorization": {"status": "verified"}},
    )

    assert "_server_approval_granted" not in invocation.call.arguments
    assert (
        "_server_approval_granted"
        not in invocation.call.arguments["test_case"]["test_data"]["runner_arguments"]
    )

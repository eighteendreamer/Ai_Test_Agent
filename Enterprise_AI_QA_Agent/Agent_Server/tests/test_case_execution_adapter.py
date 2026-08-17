from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.application.test_runs.case_execution import CaseExecutionAdapter
from src.schemas.agent import ToolDescriptor
from src.schemas.case_management import (
    TestCaseAssertion as _CaseAssertion,
    TestCaseRecord as _CaseRecord,
    TestCaseSourceRef as _CaseSourceRef,
    TestCaseStep as _CaseStep,
    TestCaseVersionRecord as _CaseVersionRecord,
)
from src.schemas.run_management import TestRunItemRecord as _RunItemRecord, TestRunRecord as _RunRecord
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
                artifacts=[SimpleNamespace(id="artifact-1", label="响应证据", path="minio://bucket/a")],
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

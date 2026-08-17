from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.run_management import router
from src.application.test_runs.case_execution import CaseExecutionAdapter
from src.application.test_runs.execution_service import TestRunExecutionService as _TestRunExecutionService
from src.application.test_runs.run_service import TestRunService as _TestRunService
from src.application.test_runs.run_store import InMemoryTestRunStore
from src.schemas.agent import ToolDescriptor
from src.schemas.case_management import (
    TestCaseAssertion as _TestCaseAssertion,
    TestCaseRecord as _TestCaseRecord,
    TestCaseSourceRef as _TestCaseSourceRef,
    TestCaseStep as _TestCaseStep,
    TestCaseVersionRecord as _TestCaseVersionRecord,
)
from src.schemas.project import ProjectRecord
from src.schemas.suite_management import (
    TestSuiteItemRecord as _TestSuiteItemRecord,
    TestSuiteRecord as _TestSuiteRecord,
)
from src.schemas.tool_runtime import ToolExecutionRecord as _ToolExecutionRecord


class _Projects:
    def __init__(self, project):
        self.project = project

    async def require_active(self, project_id):
        assert project_id == self.project.id
        return self.project

    async def get(self, project_id):
        assert project_id == self.project.id
        return self.project


class _Suites:
    def __init__(self, suite):
        self.suite = suite

    async def get(self, suite_id):
        assert suite_id == self.suite.suite.id
        return self.suite


class _Cases:
    def __init__(self, case, version):
        self.case = case
        self.version = version

    async def get_cases(self, ids):
        return {case_id: self.case for case_id in ids}

    async def get_versions(self, ids):
        return {version_id: self.version for version_id in ids}

    async def get_case(self, case_id):
        assert case_id == self.case.id
        return self.case

    async def get_version(self, version_id):
        assert version_id == self.version.id
        return self.version


class _Sessions:
    def __init__(self):
        self.events = []

    async def get_session(self, session_id):
        return SimpleNamespace(id=session_id, project_id="project-1")

    async def append_event(self, session_id, event):
        self.events.append((session_id, event))


class _FakeRuntime:
    def __init__(self):
        self.output = None

    async def execute(self, tool, call, context):
        assert tool.key == "performance-test-runner"
        return _ToolExecutionRecord(
            call_id=call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="completed",
            summary="性能工具完成",
            trace_id=context.trace_id,
            job_id="job-performance-1",
            input=call.arguments,
            output=self.output or {},
        )


class _FakeJobs:
    def __init__(self, runtime):
        self.runtime = runtime

    async def get_job_detail(self, job_id):
        return SimpleNamespace(
            id=job_id,
            status="completed",
            summary="性能报告已入库",
            output_payload=self.runtime.output,
            artifacts=[
                SimpleNamespace(
                    id="artifact-md",
                    label="Performance report (Markdown)",
                    path="inline://artifact",
                ),
                SimpleNamespace(
                    id="artifact-html",
                    label="Performance report (HTML)",
                    path="inline://artifact",
                ),
                SimpleNamespace(
                    id="artifact-json",
                    label="Performance metrics and SLA (JSON)",
                    path="inline://artifact",
                ),
            ],
        )


def _harness():
    now = datetime.now(timezone.utc)
    project = ProjectRecord(
        id="project-1",
        project_key="orders",
        name="Orders",
        status="active",
        created_at=now,
        updated_at=now,
    )
    case = _TestCaseRecord(
        id="case-1",
        project_id=project.id,
        case_key="orders_perf",
        title="订单接口性能回归",
        mode_key="performance_testing",
        case_type="performance",
        lifecycle_status="active",
        active_version_id="version-1",
        created_at=now,
        updated_at=now,
    )
    version = _TestCaseVersionRecord(
        id="version-1",
        case_id=case.id,
        version=1,
        steps=[
            _TestCaseStep(
                order=1,
                action="GET /orders/42",
                data={"endpoint": "https://example.test/orders/42", "method": "GET"},
            )
        ],
        assertions=[_TestCaseAssertion(kind="p95_ms", expected=250)],
        test_data={
            "runner_arguments": {
                "target_rate_rps": 25,
                "duration_seconds": 30,
                "run_intent": "regression",
                "sla_p95_ms": 250,
                "confirm_target": True,
            }
        },
        source_refs=[_TestCaseSourceRef(source_type="api_doc", source_id="doc-1")],
        model_key="model-1",
        prompt_version="prompt-1",
        skill_versions={"generate-test-cases": "v1"},
        content_hash="a" * 64,
        created_at=now,
    )
    suite = _TestSuiteRecord(
        id="suite-1",
        project_id=project.id,
        name="Orders performance suite",
        status="active",
        created_at=now,
        updated_at=now,
    )
    suite_bundle = SimpleNamespace(
        suite=suite,
        items=[
            _TestSuiteItemRecord(
                id="suite-item-1",
                suite_id=suite.id,
                case_id=case.id,
                case_version_id=version.id,
                position=1,
                created_at=now,
            )
        ],
    )
    runtime = _FakeRuntime()
    jobs = _FakeJobs(runtime)
    adapter = CaseExecutionAdapter(
        tool_resolver=lambda mode_key: ToolDescriptor(
            key="performance-test-runner",
            name="Performance Test Runner",
            description="test",
            category="execution",
            owner_mode_key=mode_key,
        ),
        runtime_service=runtime,
        tool_job_service=jobs,
    )
    store = InMemoryTestRunStore()
    sessions = _Sessions()
    run_service = _TestRunService(
        store=store,
        project_service=_Projects(project),
        suite_service=_Suites(suite_bundle),
        test_case_service=_Cases(case, version),
        session_store=sessions,
    )
    execution_service = _TestRunExecutionService(
        run_service=run_service,
        test_case_service=_Cases(case, version),
        adapter=adapter,
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.test_run_service = run_service
    app.state.test_run_execution_service = execution_service
    return app, runtime


def _output(*, failed: bool = False):
    return {
        "status": "completed",
        "ok": True,
        "summary": "性能回归完成",
        "run_id": "perf-run-1",
        "run_intent": "regression",
        "verdict": "fail" if failed else "pass",
        "metrics": {
            "samples": 500,
            "throughput_tps": 25.0,
            "p95_ms": 320.0 if failed else 180.0,
            "p99_ms": 400.0 if failed else 240.0,
            "error_rate": 0.02 if failed else 0.001,
        },
        "sla_result": {
            "passed": not failed,
            "violations": (
                [{"metric": "p95_ms", "actual": 320, "threshold": 250}]
                if failed
                else []
            ),
        },
        "engine_threshold_crosscheck": {"agree": True, "detail": "一致"},
        "baseline_comparison": (
            {"p95_delta_pct": 25.0, "regressed": True} if failed else None
        ),
        "artifacts": [],
    }


@pytest.mark.asyncio
async def test_performance_case_run_projects_result_evidence_and_failure_regression():
    app, runtime = _harness()
    runtime.output = _output()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/suites/suite-1/runs",
            json={"mode_key": "performance_testing", "session_id": "session-1"},
        )
        assert created.status_code == 201
        run_id = created.json()["run"]["id"]

        claimed = await client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={"worker_id": "worker-1", "limit": 1, "lease_seconds": 300},
        )
        assert claimed.status_code == 200
        claim = claimed.json()["claims"][0]

        completed = await client.post(
            f"/api/v1/run-items/{claim['item']['id']}/execute",
            json={"lease_token": claim["lease_token"]},
        )
        assert completed.status_code == 200
        passed = completed.json()
        assert passed["status"] == "passed"
        assert passed["tool_job_id"] == "job-performance-1"
        assert passed["artifact_ids"] == [
            "artifact-md",
            "artifact-html",
            "artifact-json",
        ]
        assert passed["verification_ids"]
        assert passed["metrics"]["samples"] == 500
        assert passed["metrics"]["p95_ms"] == 180.0

    app, runtime = _harness()
    runtime.output = _output(failed=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/suites/suite-1/runs",
            json={"mode_key": "performance_testing", "session_id": "session-1"},
        )
        run_id = created.json()["run"]["id"]
        claimed = await client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={"worker_id": "worker-1", "limit": 1, "lease_seconds": 300},
        )
        claim = claimed.json()["claims"][0]
        completed = await client.post(
            f"/api/v1/run-items/{claim['item']['id']}/execute",
            json={"lease_token": claim["lease_token"]},
        )
        failed = completed.json()
        assert failed["status"] == "failed"
        assert failed["verification_ids"]

        regression = await client.post(
            f"/api/v1/runs/{run_id}/regression",
            json={"result_ids": [failed["id"]]},
        )
        assert regression.status_code == 201
        assert regression.json()["run"]["run_kind"] == "regression"
        assert regression.json()["items"][0]["case_version_id"] == "version-1"

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.run_management import router as run_router
from src.application.test_runs.run_service import TestRunService as _RunService
from src.application.test_runs.run_store import InMemoryTestRunStore
from src.schemas.case_management import (
    TestCaseAssertion as _CaseAssertion,
    TestCaseRecord as _CaseRecord,
    TestCaseSourceRef as _CaseSourceRef,
    TestCaseStep as _CaseStep,
    TestCaseVersionRecord as _CaseVersionRecord,
)
from src.schemas.project import ProjectRecord
from src.schemas.run_management import (
    RegressionRunCreateRequest,
    RunClaimRequest,
    RunItemCompleteRequest,
    RunItemLeaseRequest,
    TestRunCreateRequest as _RunCreateRequest,
)
from src.schemas.suite_management import (
    TestSuiteItemRecord as _SuiteItemRecord,
    TestSuiteRecord as _SuiteRecord,
)


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
    def __init__(self, cases, versions):
        self.cases = cases
        self.versions = versions

    async def get_cases(self, ids):
        return {item: self.cases[item] for item in ids}

    async def get_versions(self, ids):
        return {item: self.versions[item] for item in ids}

    async def get_case(self, case_id):
        return self.cases[case_id]

    async def get_version(self, version_id):
        return self.versions[version_id]


def _components():
    now = datetime.now(timezone.utc)
    project = ProjectRecord(
        id="project-1",
        project_key="orders",
        name="Orders",
        status="active",
        created_at=now,
        updated_at=now,
    )
    cases = {}
    versions = {}
    suite_items = []
    for index in range(2):
        case_id = f"case-{index}"
        version_id = f"version-{index}"
        case = _CaseRecord(
            id=case_id,
            project_id=project.id,
            case_key=f"case_{index}",
            title=f"订单用例 {index}",
            mode_key="api_testing",
            case_type="api",
            lifecycle_status="active",
            active_version_id=version_id,
            created_at=now,
            updated_at=now,
        )
        version = _CaseVersionRecord(
            id=version_id,
            case_id=case_id,
            version=1,
            steps=[_CaseStep(order=1, action=f"GET /orders/{index}")],
            assertions=[_CaseAssertion(kind="status_code", expected=200)],
            source_refs=[_CaseSourceRef(source_type="api_doc", source_id="doc-1")],
            model_key="model-1",
            prompt_version="prompt-1",
            skill_versions={"generate-test-cases": "v1"},
            content_hash=(str(index) * 64)[:64],
            created_at=now,
        )
        cases[case_id] = case
        versions[version_id] = version
        versions[f"{version_id}-v2"] = version.model_copy(
            update={
                "id": f"{version_id}-v2",
                "version": 2,
                "content_hash": (chr(97 + index) * 64),
            }
        )
        suite_items.append(
            _SuiteItemRecord(
                id=f"suite-item-{index}",
                suite_id="suite-1",
                case_id=case_id,
                case_version_id=version_id,
                position=index + 1,
                created_at=now,
            )
        )
    suite = SimpleNamespace(
        suite=_SuiteRecord(
            id="suite-1",
            project_id=project.id,
            name="Orders suite",
            status="active",
            created_at=now,
            updated_at=now,
        ),
        items=suite_items,
    )
    service = _RunService(
        store=InMemoryTestRunStore(),
        project_service=_Projects(project),
        suite_service=_Suites(suite),
        test_case_service=_Cases(cases, versions),
    )
    return service, cases, versions


@pytest.mark.asyncio
async def test_regression_run_selects_failures_and_freezes_original_versions():
    service, cases, versions = _components()
    parent = await service.create_run(
        "suite-1",
        _RunCreateRequest(mode_key="api_testing"),
    )
    claims = await service.claim(
        parent.run.id,
        RunClaimRequest(worker_id="worker-1", limit=2, lease_seconds=300),
    )
    for index, claim in enumerate(claims.claims):
        await service.start_item(
            claim.item.id,
            RunItemLeaseRequest(lease_token=claim.lease_token),
        )
        await service.complete_item(
            claim.item.id,
            RunItemCompleteRequest(
                lease_token=claim.lease_token,
                status="failed" if index == 0 else "passed",
                summary="failed" if index == 0 else "passed",
            ),
        )

    regression = await service.create_regression(
        parent.run.id,
        RegressionRunCreateRequest(),
    )
    detail = await service.get(regression.run.id)
    original = await service.get(parent.run.id)

    assert regression.run.run_kind == "regression"
    assert regression.run.parent_run_id == parent.run.id
    assert len(detail.items) == 1
    assert detail.items[0].case_id == "case-0"
    assert detail.items[0].case_version_id == "version-0"
    assert detail.items[0].regression_source_result_id == original.results[0].id
    assert original.run.run_kind == "normal"
    assert original.items[0].result_id == original.results[0].id

    regression_claim = (
        await service.claim(
            regression.run.id,
            RunClaimRequest(worker_id="regression-worker", lease_seconds=300),
        )
    ).claims[0]
    await service.start_item(
        regression_claim.item.id,
        RunItemLeaseRequest(lease_token=regression_claim.lease_token),
    )
    regression_result = await service.complete_item(
        regression_claim.item.id,
        RunItemCompleteRequest(
            lease_token=regression_claim.lease_token,
            status="passed",
            summary="regression passed",
        ),
    )

    assert regression_result.regression_source_result_id == original.results[0].id


@pytest.mark.asyncio
async def test_regression_rejects_passed_result_and_accepts_explicit_new_version():
    service, cases, versions = _components()
    parent = await service.create_run("suite-1", _RunCreateRequest(mode_key="api_testing"))
    claims = (
        await service.claim(
            parent.run.id,
            RunClaimRequest(worker_id="worker-1", limit=2, lease_seconds=300),
        )
    ).claims
    results = []
    for index, claim in enumerate(claims):
        await service.start_item(
            claim.item.id,
            RunItemLeaseRequest(lease_token=claim.lease_token),
        )
        results.append(
            await service.complete_item(
                claim.item.id,
                RunItemCompleteRequest(
                    lease_token=claim.lease_token,
                    status="passed" if index == 0 else "failed",
                    summary="completed",
                ),
            )
        )

    with pytest.raises(ValueError, match="not eligible"):
        await service.create_regression(
            parent.run.id,
            RegressionRunCreateRequest(result_ids=[results[0].id]),
        )

    with pytest.raises(ValueError, match="active version"):
        await service.create_regression(
            parent.run.id,
            RegressionRunCreateRequest(
                result_ids=[results[1].id],
                version_overrides={"case-1": "version-1-v2"},
            ),
        )

    cases["case-1"] = cases["case-1"].model_copy(
        update={"active_version_id": "version-1-v2"}
    )
    regression = await service.create_regression(
        parent.run.id,
        RegressionRunCreateRequest(
            result_ids=[results[1].id],
            version_overrides={"case-1": "version-1-v2"},
        ),
    )

    assert regression.items[0].case_version_id == "version-1-v2"
    assert regression.items[0].regression_source_result_id == results[1].id


@pytest.mark.asyncio
async def test_regression_reports_missing_source_run_item_before_sorting(monkeypatch):
    service, _, _ = _components()
    parent = await service.create_run("suite-1", _RunCreateRequest(mode_key="api_testing"))
    claim = (
        await service.claim(
            parent.run.id,
            RunClaimRequest(worker_id="worker-1", limit=1, lease_seconds=300),
        )
    ).claims[0]
    await service.start_item(
        claim.item.id,
        RunItemLeaseRequest(lease_token=claim.lease_token),
    )
    await service.complete_item(
        claim.item.id,
        RunItemCompleteRequest(
            lease_token=claim.lease_token,
            status="failed",
            summary="failed",
        ),
    )
    remaining_claim = (
        await service.claim(
            parent.run.id,
            RunClaimRequest(worker_id="worker-1", limit=1, lease_seconds=300),
        )
    ).claims[0]
    await service.start_item(
        remaining_claim.item.id,
        RunItemLeaseRequest(lease_token=remaining_claim.lease_token),
    )
    await service.complete_item(
        remaining_claim.item.id,
        RunItemCompleteRequest(
            lease_token=remaining_claim.lease_token,
            status="passed",
            summary="passed",
        ),
    )
    damaged = await service.get(parent.run.id)
    damaged.items = [item for item in damaged.items if item.id != claim.item.id]
    original_get = service.get

    async def get_with_damaged_parent(run_id):
        if run_id == parent.run.id:
            return damaged
        return await original_get(run_id)

    monkeypatch.setattr(service, "get", get_with_damaged_parent)

    with pytest.raises(KeyError, match=f"Regression source run item not found: {claim.item.id}"):
        await service.create_regression(parent.run.id, RegressionRunCreateRequest())


@pytest.mark.asyncio
async def test_regression_system_api_creates_a_new_fixed_version_run():
    service, _, _ = _components()
    app = FastAPI()
    app.include_router(run_router, prefix="/api/v1")
    app.state.test_run_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/suites/suite-1/runs",
            json={"mode_key": "api_testing"},
        )
        parent_run_id = created.json()["run"]["id"]
        claimed = await client.post(
            f"/api/v1/runs/{parent_run_id}/claim",
            json={"worker_id": "api-worker", "limit": 2, "lease_seconds": 300},
        )
        claims = claimed.json()["claims"]
        for index, claim in enumerate(claims):
            item_id = claim["item"]["id"]
            lease_token = claim["lease_token"]
            await client.post(
                f"/api/v1/run-items/{item_id}/start",
                json={"lease_token": lease_token},
            )
            await client.post(
                f"/api/v1/run-items/{item_id}/complete",
                json={
                    "lease_token": lease_token,
                    "status": "failed" if index == 0 else "passed",
                    "summary": "system api result",
                },
            )
        regression = await client.post(
            f"/api/v1/runs/{parent_run_id}/regression",
            json={},
        )

    assert regression.status_code == 201
    payload = regression.json()
    assert payload["run"]["run_kind"] == "regression"
    assert payload["run"]["parent_run_id"] == parent_run_id
    assert len(payload["items"]) == 1
    assert payload["items"][0]["case_version_id"] == "version-0"
    assert payload["items"][0]["regression_source_result_id"]

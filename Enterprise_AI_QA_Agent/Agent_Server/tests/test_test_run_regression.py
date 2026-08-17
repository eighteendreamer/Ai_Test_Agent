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
    TestCaseResultRecord as _CaseResultRecord,
    TestRunCreateRequest as _RunCreateRequest,
    TestRunItemRecord as _RunItemRecord,
    TestRunRecord as _RunRecord,
    TestRunStats as _RunStats,
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


def _components(*, store=None, session_store=None):
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
        store=store or InMemoryTestRunStore(),
        project_service=_Projects(project),
        suite_service=_Suites(suite),
        test_case_service=_Cases(cases, versions),
        session_store=session_store,
    )
    return service, cases, versions


class _NoDetailRegressionStore(InMemoryTestRunStore):
    def __init__(self):
        super().__init__()
        self.reject_detail_reads = False

    async def get_run(self, run_id):
        if self.reject_detail_reads:
            raise AssertionError("Regression creation must not load the full parent run detail")
        return await super().get_run(run_id)


class _EventSessions:
    def __init__(self):
        self.events = []

    async def append_event(self, session_id, event):
        self.events.append((session_id, event))


def _seed_failed_parent(store, case_count, *, session_id=None):
    now = datetime.now(timezone.utc)
    parent = _RunRecord(
        id="bulk-parent-run",
        project_id="project-1",
        suite_id="suite-1",
        mode_key="api_testing",
        session_id=session_id,
        status="completed",
        stats=_RunStats(total=case_count, failed=case_count),
        created_at=now,
        updated_at=now,
        completed_at=now,
    )
    store._runs[parent.id] = parent
    store._item_ids_by_run[parent.id] = []
    store._attempt_ids_by_run[parent.id] = []
    store._result_ids_by_run[parent.id] = []
    for index in range(case_count):
        item_id = f"bulk-item-{index}"
        result_id = f"bulk-result-{index}"
        item = _RunItemRecord(
            id=item_id,
            run_id=parent.id,
            case_id=f"bulk-case-{index}",
            case_version_id=f"bulk-version-{index}",
            position=index + 1,
            status="failed",
            result_id=result_id,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        result = _CaseResultRecord(
            id=result_id,
            run_id=parent.id,
            run_item_id=item_id,
            case_id=item.case_id,
            case_version_id=item.case_version_id,
            attempt_id=f"bulk-attempt-{index}",
            attempt_no=1,
            status="failed",
            summary="failed",
            payload_hash=f"{index:064x}"[-64:],
            created_at=now,
        )
        store._items[item_id] = item
        store._results[result_id] = result
        store._item_ids_by_run[parent.id].append(item_id)
        store._result_ids_by_run[parent.id].append(result_id)
    return parent


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
async def test_regression_creation_does_not_load_full_parent_run_detail():
    store = _NoDetailRegressionStore()
    service, _, _ = _components(store=store)
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
                summary="completed",
            ),
        )

    store.reject_detail_reads = True
    regression = await service.create_regression(
        parent.run.id,
        RegressionRunCreateRequest(),
    )

    assert len(regression.items) == 1
    assert regression.items[0].case_id == "case-0"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_count", [1_000, 10_000])
async def test_default_regression_capacity_scales_with_selected_failures(case_count):
    store = _NoDetailRegressionStore()
    sessions = _EventSessions()
    service, _, _ = _components(store=store, session_store=sessions)
    parent = _seed_failed_parent(store, case_count, session_id="bulk-session")

    store.reject_detail_reads = True
    regression = await service.create_regression(
        parent.id,
        RegressionRunCreateRequest(),
    )

    assert len(regression.items) == case_count
    assert regression.items[0].regression_source_result_id == "bulk-result-0"
    assert regression.items[-1].regression_source_result_id == (
        f"bulk-result-{case_count - 1}"
    )
    assert regression.items[-1].position == case_count
    assert regression.attempts == []
    assert len(sessions.events) == 1
    event_payload = sessions.events[0][1].payload
    assert event_payload["source_result_count"] == case_count
    assert len(event_payload["source_result_ids"]) == 100
    assert event_payload["source_result_ids_truncated"] is True


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
async def test_regression_reports_missing_source_run_item_before_sorting():
    store = InMemoryTestRunStore()
    service, _, _ = _components(store=store)
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
    del store._items[claim.item.id]

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


@pytest.mark.asyncio
async def test_regression_system_api_handles_one_thousand_failed_results():
    store = _NoDetailRegressionStore()
    service, _, _ = _components(store=store)
    parent = _seed_failed_parent(store, 1_000)
    store.reject_detail_reads = True
    app = FastAPI()
    app.include_router(run_router, prefix="/api/v1")
    app.state.test_run_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/runs/{parent.id}/regression",
            json={},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["run"]["stats"]["total"] == 1_000
    assert len(payload["items"]) == 1_000
    assert payload["items"][-1]["regression_source_result_id"] == "bulk-result-999"


@pytest.mark.asyncio
async def test_regression_center_system_api_uses_keyset_pagination():
    service, _, _ = _components()
    parent = await service.create_run(
        "suite-1",
        _RunCreateRequest(mode_key="api_testing"),
    )
    claims = (
        await service.claim(
            parent.run.id,
            RunClaimRequest(worker_id="worker-1", limit=2, lease_seconds=300),
        )
    ).claims
    for claim in claims:
        await service.start_item(
            claim.item.id,
            RunItemLeaseRequest(lease_token=claim.lease_token),
        )
        await service.complete_item(
            claim.item.id,
            RunItemCompleteRequest(
                lease_token=claim.lease_token,
                status="failed",
                summary=f"failed {claim.item.case_id}",
                evidence_refs=[
                    {
                        "evidence_type": "http_response",
                        "evidence_id": f"evidence-{claim.item.case_id}",
                    }
                ],
                artifact_ids=[f"artifact-{claim.item.case_id}"],
                verification_ids=[f"verification-{claim.item.case_id}"],
            ),
        )
    await service.create_regression(parent.run.id, RegressionRunCreateRequest())
    app = FastAPI()
    app.include_router(run_router, prefix="/api/v1")
    app.state.test_run_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/api/v1/projects/project-1/regression-failures",
            params={"limit": 1},
        )
        assert first.status_code == 200
        first_payload = first.json()
        second = await client.get(
            "/api/v1/projects/project-1/regression-failures",
            params={"limit": 1, "cursor": first_payload["next_cursor"]},
        )

    assert second.status_code == 200
    second_payload = second.json()
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]
    assert len(first_payload["items"]) == 1
    assert len(second_payload["items"]) == 1
    assert first_payload["items"][0]["source_result_id"] != (
        second_payload["items"][0]["source_result_id"]
    )
    assert first_payload["items"][0]["case_title"].startswith("订单用例")
    assert first_payload["items"][0]["evidence_count"] == 1
    assert first_payload["items"][0]["artifact_count"] == 1
    assert first_payload["items"][0]["verification_count"] == 1
    assert first_payload["items"][0]["regression_batch_count"] == 1
    assert first_payload["items"][0]["latest_regression"]["item_status"] == "queued"


@pytest.mark.asyncio
async def test_regression_context_system_api_exposes_only_public_evidence_links():
    store = InMemoryTestRunStore()
    service, _, _ = _components(store=store)
    parent = await service.create_run(
        "suite-1",
        _RunCreateRequest(mode_key="api_testing"),
    )
    store._runs[parent.run.id] = parent.run.model_copy(update={"session_id": "session-1"})
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
    result = await service.complete_item(
        claim.item.id,
        RunItemCompleteRequest(
            lease_token=claim.lease_token,
            status="failed",
            summary="status assertion failed",
            actual={
                "response_path": "C:/private/results/response.json",
                "verification_results": [
                    {
                        "id": "verification-1",
                        "session_id": "session-1",
                        "turn_id": "turn-1",
                        "trace_id": "trace-1",
                        "verifier": "http_status",
                        "status": "failed",
                        "summary": "expected 200, got 500; dump C:/private/verify.json",
                        "assertion_count": 1,
                        "passed_count": 0,
                        "failed_count": 1,
                        "evidence": [],
                        "metadata": {"internal_path": "C:/private/verification.json"},
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
            evidence_refs=[
                {
                    "evidence_type": "artifact",
                    "evidence_id": "artifact-1",
                    "label": "C:/private/results/response.json",
                    "uri": "minio://private-bucket/internal-key",
                    "metadata": {"local_path": "C:/private/results/response.json"},
                }
            ],
            artifact_ids=["artifact-1"],
            verification_ids=["verification-1"],
            metrics={
                "duration_ms": 125,
                "storage_uri": "minio://private-bucket/internal-metrics.json",
            },
            error_message="HTTP 500",
        ),
    )
    app = FastAPI()
    app.include_router(run_router, prefix="/api/v1")
    app.state.test_run_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/test-results/{result.id}/regression-context"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_result_id"] == result.id
    assert payload["summary"] == "status assertion failed"
    assert payload["metrics"] == {"duration_ms": 125}
    assert payload["evidence"] == [
        {
            "evidence_type": "artifact",
            "evidence_id": "artifact-1",
            "label": "artifact",
        }
    ]
    assert payload["artifacts"] == [
        {
            "artifact_id": "artifact-1",
            "content_url": "/api/v1/sessions/session-1/artifacts/artifact-1/content",
        }
    ]
    assert payload["verifications"][0]["id"] == "verification-1"
    assert payload["verifications"][0]["status"] == "failed"
    serialized = response.text
    assert "minio://" not in serialized
    assert "C:/private" not in serialized
    assert '"actual"' not in serialized


@pytest.mark.asyncio
async def test_regression_batches_system_api_uses_keyset_timeline():
    service, _, _ = _components()
    parent = await service.create_run(
        "suite-1",
        _RunCreateRequest(mode_key="api_testing"),
    )
    claims = (
        await service.claim(
            parent.run.id,
            RunClaimRequest(worker_id="worker-1", limit=2, lease_seconds=300),
        )
    ).claims
    source_result_id = None
    for index, claim in enumerate(claims):
        await service.start_item(
            claim.item.id,
            RunItemLeaseRequest(lease_token=claim.lease_token),
        )
        result = await service.complete_item(
            claim.item.id,
            RunItemCompleteRequest(
                lease_token=claim.lease_token,
                status="failed" if index == 0 else "passed",
                summary="failed" if index == 0 else "passed",
            ),
        )
        if index == 0:
            source_result_id = result.id
    assert source_result_id
    await service.create_regression(
        parent.run.id,
        RegressionRunCreateRequest(result_ids=[source_result_id]),
    )
    await service.create_regression(
        parent.run.id,
        RegressionRunCreateRequest(result_ids=[source_result_id]),
    )
    app = FastAPI()
    app.include_router(run_router, prefix="/api/v1")
    app.state.test_run_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            f"/api/v1/test-results/{source_result_id}/regression-batches",
            params={"limit": 1},
        )
        assert first.status_code == 200
        first_payload = first.json()
        second = await client.get(
            f"/api/v1/test-results/{source_result_id}/regression-batches",
            params={"limit": 1, "cursor": first_payload["next_cursor"]},
        )

    assert second.status_code == 200
    second_payload = second.json()
    assert first_payload["source_result_id"] == source_result_id
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]
    assert len(first_payload["items"]) == 1
    assert len(second_payload["items"]) == 1
    assert first_payload["items"][0]["run_id"] != second_payload["items"][0]["run_id"]
    assert first_payload["items"][0]["run_kind"] == "regression"
    assert first_payload["items"][0]["parent_run_id"] == parent.run.id
    assert first_payload["items"][0]["item_status"] == "queued"

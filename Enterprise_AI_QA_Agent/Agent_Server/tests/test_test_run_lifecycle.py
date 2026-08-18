from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module

import httpx
import pytest
from fastapi import FastAPI

from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.application.test_cases.case_service import TestCaseService as CaseService
from src.application.test_cases.case_store import InMemoryTestCaseStore
from src.application.test_suites.suite_service import TestSuiteService as SuiteService
from src.application.test_suites.suite_store import InMemoryTestSuiteStore
from src.runtime.store import InMemorySessionStore
from src.domain.models import SessionRecord
from src.schemas.case_management import TestCaseDraftCreateRequest as CaseDraftRequest
from src.schemas.project import ProjectCreateRequest
from src.schemas.suite_management import TestSuiteCreateRequest as SuiteCreateRequest
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _run(awaitable):
    return asyncio.run(awaitable)


def _components():
    try:
        routes = import_module("src.api.routes.run_management")
        service_module = import_module("src.application.test_runs.run_service")
        store_module = import_module("src.application.test_runs.run_store")
    except ModuleNotFoundError as exc:
        pytest.fail(f"test run lifecycle module is not implemented: {exc}")
    return routes.router, service_module.TestRunService, store_module.InMemoryTestRunStore


async def _build_components(
    case_count: int = 3,
    *,
    clock: _Clock | None = None,
    run_store=None,
    lease_reaper_interval_seconds: float | None = None,
):
    router, run_service_type, run_store_type = _components()
    projects = ProjectService(store=InMemoryProjectStore())
    await projects.initialize()
    project = await projects.create(ProjectCreateRequest(project_key="orders-run", name="Orders Run"))
    cases = CaseService(store=InMemoryTestCaseStore(), project_service=projects)
    await cases.initialize()
    suite_items = []
    for index in range(case_count):
        created = await cases.create_draft(
            project_id=project.id,
            payload=CaseDraftRequest(
                case_key=f"orders-run-{index}",
                title=f"订单运行用例 {index}",
                mode_key="api_testing",
                case_type="api",
                steps=[{"order": 1, "action": f"执行订单接口 {index}", "expected": "返回成功"}],
                assertions=[{"kind": "status_code", "operator": "equals", "expected": 200}],
                source_refs=[{"source_type": "api_doc", "source_id": "orders-doc"}],
                model_key="mock-model",
                prompt_version="v1",
                skill_versions={"generate-test-cases": "sha256:test"},
            ),
        )
        await cases.submit_review(created.case.id)
        active = await cases.activate(created.case.id)
        suite_items.append(
            {"case_id": active.id, "case_version_id": active.active_version_id}
        )
    suites = SuiteService(
        store=InMemoryTestSuiteStore(),
        project_service=projects,
        test_case_service=cases,
    )
    await suites.initialize()
    suite = await suites.create(
        project.id,
        SuiteCreateRequest(name="订单固定套件", items=suite_items),
    )
    sessions = InMemorySessionStore()
    await sessions.initialize()
    run_service = run_service_type(
        store=run_store or run_store_type(),
        project_service=projects,
        suite_service=suites,
        test_case_service=cases,
        session_store=sessions,
        clock=clock,
        lease_reaper_interval_seconds=lease_reaper_interval_seconds,
    )
    await run_service.initialize()
    app = FastAPI()
    app.state.test_run_service = run_service
    app.state.session_store = sessions
    app.include_router(router, prefix="/api/v1")
    return app, project, suite, projects, run_service


def _request(app: FastAPI, method: str, path: str, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return _run(send())


def _create_run(app: FastAPI, suite_id: str):
    return _request(
        app,
        "POST",
        f"/api/v1/suites/{suite_id}/runs",
        json={"mode_key": "api_testing"},
    )


def test_create_run_freezes_suite_items_and_lists_project_history():
    app, project, suite, _, _ = _run(_build_components())

    created = _create_run(app, suite.suite.id)
    listed = _request(app, "GET", f"/api/v1/projects/{project.id}/runs")
    detail = _request(app, "GET", f"/api/v1/runs/{created.json()['run']['id']}")

    assert created.status_code == 201
    body = created.json()
    assert body["run"]["project_id"] == project.id
    assert body["run"]["suite_id"] == suite.suite.id
    assert body["run"]["status"] == "queued"
    assert body["run"]["stats"]["total"] == 3
    assert body["run"]["stats"]["queued"] == 3
    assert [item["case_version_id"] for item in body["items"]] == [
        item.case_version_id for item in suite.items
    ]
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [body["run"]["id"]]
    assert detail.status_code == 200
    assert len(detail.json()["items"]) == 3


def test_cancelled_run_exposes_idempotent_resource_reconciliation_endpoint():
    app, _, suite, _, _ = _run(_build_components(case_count=1))
    created = _create_run(app, suite.suite.id)
    run_id = created.json()["run"]["id"]

    cancelled = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/cancel",
        json={"reason": "operator stop"},
    )
    reconciled = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/reconcile-resources",
    )

    assert cancelled.status_code == 200
    assert reconciled.status_code == 200
    assert reconciled.json()["run"]["status"] == "cancelled"


def test_claim_start_heartbeat_and_completion_are_lease_guarded_and_idempotent():
    clock = _Clock()
    app, _, suite, _, _ = _run(_build_components(case_count=1, clock=clock))
    run_id = _create_run(app, suite.suite.id).json()["run"]["id"]

    claimed = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/claim",
        json={"worker_id": "worker-a", "limit": 1, "lease_seconds": 60},
    )
    claim = claimed.json()["claims"][0]
    item_id = claim["item"]["id"]
    token = claim["lease_token"]
    second_claim = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/claim",
        json={"worker_id": "worker-b", "limit": 1, "lease_seconds": 60},
    )
    wrong_start = _request(
        app,
        "POST",
        f"/api/v1/run-items/{item_id}/start",
        json={"lease_token": "wrong-token"},
    )
    started = _request(
        app,
        "POST",
        f"/api/v1/run-items/{item_id}/start",
        json={"lease_token": token},
    )
    clock.advance(30)
    heartbeat = _request(
        app,
        "POST",
        f"/api/v1/run-items/{item_id}/heartbeat",
        json={"lease_token": token, "lease_seconds": 120},
    )
    completion_payload = {
        "lease_token": token,
        "status": "passed",
        "summary": "订单接口返回 200",
        "actual": {"status_code": 200},
        "artifact_ids": ["artifact-1"],
        "verification_ids": ["verification-1"],
    }
    completed = _request(
        app,
        "POST",
        f"/api/v1/run-items/{item_id}/complete",
        json=completion_payload,
    )
    repeated = _request(
        app,
        "POST",
        f"/api/v1/run-items/{item_id}/complete",
        json=completion_payload,
    )
    conflicting = _request(
        app,
        "POST",
        f"/api/v1/run-items/{item_id}/complete",
        json={**completion_payload, "status": "failed", "summary": "different"},
    )
    detail = _request(app, "GET", f"/api/v1/runs/{run_id}")

    assert claimed.status_code == 200
    assert claim["item"]["status"] == "claimed"
    assert claim["attempt"]["attempt_no"] == 1
    assert claim["version"]["id"] == suite.items[0].case_version_id
    assert claim["version"]["steps"][0]["action"] == "执行订单接口 0"
    assert second_claim.status_code == 200
    assert second_claim.json()["claims"] == []
    assert wrong_start.status_code == 409
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert heartbeat.status_code == 200
    assert heartbeat.json()["heartbeat_at"] == clock.now.isoformat().replace("+00:00", "Z")
    assert completed.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["id"] == completed.json()["id"]
    assert conflicting.status_code == 409
    assert detail.json()["run"]["status"] == "completed"
    assert detail.json()["run"]["stats"]["passed"] == 1
    assert len(detail.json()["attempts"]) == 1
    assert len(detail.json()["results"]) == 1


def test_expired_lease_is_requeued_as_a_new_attempt_and_terminal_items_are_not_reclaimed():
    clock = _Clock()
    app, _, suite, _, _ = _run(_build_components(case_count=1, clock=clock))
    run_id = _create_run(app, suite.suite.id).json()["run"]["id"]
    first = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/claim",
        json={"worker_id": "stale-worker", "limit": 1, "lease_seconds": 15},
    ).json()["claims"][0]
    clock.advance(16)

    recovered = _request(app, "POST", f"/api/v1/runs/{run_id}/recover-expired")
    second = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/claim",
        json={"worker_id": "healthy-worker", "limit": 1, "lease_seconds": 60},
    ).json()["claims"][0]
    _request(
        app,
        "POST",
        f"/api/v1/run-items/{second['item']['id']}/start",
        json={"lease_token": second["lease_token"]},
    )
    _request(
        app,
        "POST",
        f"/api/v1/run-items/{second['item']['id']}/complete",
        json={"lease_token": second["lease_token"], "status": "failed", "summary": "真实失败"},
    )
    after_terminal = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/claim",
        json={"worker_id": "worker-c", "limit": 1, "lease_seconds": 60},
    )
    detail = _request(app, "GET", f"/api/v1/runs/{run_id}").json()

    assert recovered.status_code == 200
    assert recovered.json()["recovered_count"] == 1
    assert second["item"]["id"] == first["item"]["id"]
    assert second["attempt"]["attempt_no"] == 2
    assert second["lease_token"] != first["lease_token"]
    assert after_terminal.json()["claims"] == []
    assert [attempt["status"] for attempt in detail["attempts"]] == ["expired", "failed"]


def test_online_lease_reaper_requeues_expired_items_through_the_api_state():
    async def scenario():
        clock = _Clock()
        app, _, suite, _, run_service = await _build_components(
            case_count=1,
            clock=clock,
            lease_reaper_interval_seconds=0.1,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/suites/{suite.suite.id}/runs",
                json={"mode_key": "api_testing"},
            )
            run_id = created.json()["run"]["id"]
            claimed = await client.post(
                f"/api/v1/runs/{run_id}/claim",
                json={"worker_id": "stale-worker", "limit": 1, "lease_seconds": 15},
            )
            assert claimed.status_code == 200
            item_id = claimed.json()["claims"][0]["item"]["id"]
            clock.advance(16)

            await run_service.start_lease_reaper()
            await asyncio.sleep(0.15)
            await run_service.stop_lease_reaper()

            detail = await client.get(f"/api/v1/runs/{run_id}")

        assert detail.status_code == 200
        item = next(value for value in detail.json()["items"] if value["id"] == item_id)
        assert item["status"] == "queued"
        assert item["lease_token"] is None
        assert detail.json()["attempts"][0]["status"] == "expired"

    asyncio.run(scenario())


def test_cancel_marks_only_non_terminal_items_and_archived_project_rejects_new_run():
    app, project, suite, projects, _ = _run(_build_components(case_count=2))
    run_id = _create_run(app, suite.suite.id).json()["run"]["id"]

    cancelled = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/cancel",
        json={"reason": "operator stop"},
    )
    claim_after_cancel = _request(
        app,
        "POST",
        f"/api/v1/runs/{run_id}/claim",
        json={"worker_id": "worker-a", "limit": 2, "lease_seconds": 60},
    )
    _run(projects.archive(project.id))
    archived_create = _create_run(app, suite.suite.id)

    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert cancelled.json()["run"]["stats"]["cancelled"] == 2
    assert claim_after_cancel.json()["claims"] == []
    assert archived_create.status_code == 409


def test_concurrent_workers_claim_every_item_once_without_duplicates():
    async def scenario():
        app, _, suite, _, _ = await _build_components(case_count=200)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/suites/{suite.suite.id}/runs",
                json={"mode_key": "api_testing"},
            )
            run_id = created.json()["run"]["id"]

            async def claim(worker_index: int):
                response = await client.post(
                    f"/api/v1/runs/{run_id}/claim",
                    json={
                        "worker_id": f"worker-{worker_index}",
                        "limit": 10,
                        "lease_seconds": 60,
                    },
                )
                assert response.status_code == 200
                return response.json()["claims"]

            batches = await asyncio.gather(*(claim(index) for index in range(20)))
            item_ids = [claim["item"]["id"] for batch in batches for claim in batch]
            lease_tokens = [claim["lease_token"] for batch in batches for claim in batch]
            no_more = await client.post(
                f"/api/v1/runs/{run_id}/claim",
                json={"worker_id": "worker-extra", "limit": 10, "lease_seconds": 60},
            )

        assert len(item_ids) == 200
        assert len(set(item_ids)) == 200
        assert len(set(lease_tokens)) == 200
        assert no_more.json()["claims"] == []

    asyncio.run(scenario())


def test_main_registers_test_run_system_routes():
    main_module = import_module("src.main")
    paths = {route.path for route in main_module.app.routes if hasattr(route, "path")}
    for included in main_module.app.routes:
        router = getattr(included, "original_router", None)
        context = getattr(included, "include_context", None)
        if router is None or context is None:
            continue
        paths.update(
            f"{context.prefix}{route.path}"
            for route in router.routes
            if hasattr(route, "path")
        )

    assert "/api/v1/suites/{suite_id}/runs" in paths
    assert "/api/v1/runs/{run_id}/claim" in paths
    assert "/api/v1/run-items/{item_id}/complete" in paths


def test_worker_hot_path_does_not_reload_the_full_run_detail():
    _, _, store_type = _components()

    class CountingStore(store_type):
        def __init__(self):
            super().__init__()
            self.detail_reads = 0

        async def get_run(self, run_id):
            self.detail_reads += 1
            return await super().get_run(run_id)

    async def scenario():
        store = CountingStore()
        app, _, suite, _, _ = await _build_components(case_count=50, run_store=store)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/suites/{suite.suite.id}/runs",
                json={"mode_key": "api_testing"},
            )
            run_id = created.json()["run"]["id"]
            store.detail_reads = 0
            claimed = await client.post(
                f"/api/v1/runs/{run_id}/claim",
                json={"worker_id": "worker-hot-path", "limit": 50, "lease_seconds": 300},
            )
            for claim in claimed.json()["claims"]:
                item_id = claim["item"]["id"]
                token = claim["lease_token"]
                await client.post(
                    f"/api/v1/run-items/{item_id}/start",
                    json={"lease_token": token},
                )
                await client.post(
                    f"/api/v1/run-items/{item_id}/complete",
                    json={
                        "lease_token": token,
                        "status": "passed",
                        "summary": "pass",
                    },
                )

        assert store.detail_reads == 0

    asyncio.run(scenario())


def test_service_startup_recovers_expired_worker_leases():
    async def scenario():
        clock = _Clock()
        _, _, store_type = _components()
        store = store_type()
        app, _, suite, _, service = await _build_components(
            case_count=1,
            clock=clock,
            run_store=store,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/suites/{suite.suite.id}/runs",
                json={"mode_key": "api_testing"},
            )
            run_id = created.json()["run"]["id"]
            await client.post(
                f"/api/v1/runs/{run_id}/claim",
                json={"worker_id": "crashed-worker", "limit": 1, "lease_seconds": 15},
            )
        clock.advance(16)

        await service.initialize()
        detail = await service.get(run_id)

        assert detail.items[0].status == "queued"
        assert detail.attempts[0].status == "expired"

    asyncio.run(scenario())


def test_project_bound_run_reuses_session_events_for_sse_history():
    async def scenario():
        app, project, suite, _, _ = await _build_components(case_count=1)
        sessions = app.state.session_store
        now = project.created_at
        await sessions.save_session(
            SessionRecord(
                id="run-session-1",
                title="Run session",
                status=SessionStatus.running,
                session_mode=SessionMode.normal,
                runtime_mode=RuntimeMode.interactive,
                mode_key="api_testing",
                project_id=project.id,
                created_at=now,
                updated_at=now,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/suites/{suite.suite.id}/runs",
                json={"mode_key": "api_testing", "session_id": "run-session-1"},
            )
            run_id = created.json()["run"]["id"]
            await client.post(
                f"/api/v1/runs/{run_id}/claim",
                json={"worker_id": "event-worker", "limit": 1, "lease_seconds": 60},
            )
        events = await sessions.list_events("run-session-1")

        assert [event.type for event in events] == [
            "test_run.created",
            "test_run.items_claimed",
        ]
        assert all(event.payload["run_id"] == run_id for event in events)

    asyncio.run(scenario())

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI

from src.api.routes.projects import router
from src.application.projects.legacy_smoke_history_service import LegacySmokeHistoryService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore


class _FakeSmokeCatalog:
    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self._bindings: dict[str, dict] = {}

    async def initialize(self) -> None:
        return None

    async def bind_project_scope(self, *, project_id: str, project_scope: str) -> dict:
        existing = self._bindings.get(project_scope)
        if existing is not None and existing["project_id"] != project_id:
            raise ValueError(f"Legacy Smoke project_scope is already bound to another project: {project_scope}")
        if existing is None:
            existing = {
                "project_id": project_id,
                "project_scope": project_scope,
                "created_at": datetime.now(timezone.utc),
            }
            self._bindings[project_scope] = existing
        return dict(existing)

    async def unbind_project_scope(self, *, project_id: str, project_scope: str) -> bool:
        existing = self._bindings.get(project_scope)
        if existing is None or existing["project_id"] != project_id:
            return False
        del self._bindings[project_scope]
        return True

    async def list_project_scope_bindings(self, project_id: str) -> list[dict]:
        return [
            dict(item)
            for item in self._bindings.values()
            if item["project_id"] == project_id
        ]

    async def list_legacy_runs(
        self,
        *,
        project_scopes: list[str],
        cursor_started_at: datetime | None,
        cursor_run_id: str | None,
        limit: int,
    ) -> tuple[list[dict], bool]:
        records = sorted(
            (item for item in self._records if item["project_scope"] in project_scopes),
            key=lambda item: (item["started_at"], item["run_id"]),
            reverse=True,
        )
        if cursor_started_at is not None and cursor_run_id is not None:
            records = [
                item
                for item in records
                if (item["started_at"], item["run_id"]) < (cursor_started_at, cursor_run_id)
            ]
        return records[:limit], len(records) > limit


def _record(run_id: str, started_at: datetime, *, scope: str = "orders-v1") -> dict:
    return {
        "run_id": run_id,
        "plan_id": "smoke-orders",
        "plan_version": 1,
        "project_scope": scope,
        "status": "partial",
        "verdict": "partial",
        "total_cases": 2,
        "passed_cases": 1,
        "failed_cases": 0,
        "blocked_cases": 1,
        "started_at": started_at,
        "completed_at": started_at + timedelta(seconds=5),
        "metadata": {
            "summary": "旧冒烟历史快照",
            "case_results": [
                {
                    "case_id": "legacy-case-partial",
                    "title": "支付回调检查",
                    "case_type": "api",
                    "status": "partial",
                    "summary": "人工复核后继续",
                    "assertion_count": 2,
                    "passed_count": 1,
                    "failed_count": 0,
                    "duration_ms": 32,
                    "evidence": [{"uri": "minio://must-not-be-exposed"}],
                },
                {
                    "case_id": "legacy-case-not-run",
                    "title": "管理后台检查",
                    "case_type": "ui",
                    "status": "not_run",
                    "summary": "历史未执行",
                },
            ],
        },
    }


async def _build_app() -> FastAPI:
    projects = ProjectService(store=InMemoryProjectStore())
    await projects.initialize()
    catalog = _FakeSmokeCatalog(
        [
            _record("run-new", datetime(2026, 8, 18, tzinfo=timezone.utc)),
            _record("run-old", datetime(2026, 8, 17, tzinfo=timezone.utc)),
            _record("run-other", datetime(2026, 8, 19, tzinfo=timezone.utc), scope="payments-v1"),
        ]
    )
    history = LegacySmokeHistoryService(project_service=projects, catalog=catalog)
    await history.initialize()
    app = FastAPI()
    app.state.project_service = projects
    app.state.legacy_smoke_history_service = history
    app.include_router(router, prefix="/api/v1")
    return app


def _request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def _create_project(app: FastAPI, project_key: str) -> dict:
    response = _request(
        app,
        "POST",
        "/api/v1/projects",
        json={"project_key": project_key, "name": project_key},
    )
    assert response.status_code == 201
    return response.json()


def test_legacy_smoke_history_requires_explicit_scope_binding_and_is_read_only():
    app = asyncio.run(_build_app())
    project = _create_project(app, "orders")

    unbound = _request(app, "GET", f"/api/v1/projects/{project['id']}/legacy-smoke-runs")
    bound = _request(
        app,
        "PUT",
        f"/api/v1/projects/{project['id']}/legacy-smoke-bindings",
        json={"project_scope": "orders-v1"},
    )
    listed = _request(
        app,
        "GET",
        f"/api/v1/projects/{project['id']}/legacy-smoke-runs?limit=1",
    )

    assert unbound.status_code == 200
    assert unbound.json()["items"] == []
    assert bound.status_code == 200
    assert bound.json()["project_scope"] == "orders-v1"
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["has_more"] is True
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["source_system"] == "legacy_smoke_catalog"
    assert item["read_only"] is True
    assert item["legacy_run_id"] == "run-new"
    assert item["case_results"][0]["legacy_status"] == "partial"
    assert item["case_results"][0]["mapped_status"] == "blocked"
    assert item["case_results"][1]["legacy_status"] == "not_run"
    assert item["case_results"][1]["mapped_status"] == "skipped"
    assert "minio://must-not-be-exposed" not in str(payload)

    next_page = _request(
        app,
        "GET",
        f"/api/v1/projects/{project['id']}/legacy-smoke-runs?cursor={payload['next_cursor']}&limit=1",
    )
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["legacy_run_id"] == "run-old"


def test_legacy_smoke_scope_cannot_be_bound_to_two_projects_and_binding_can_be_removed():
    app = asyncio.run(_build_app())
    first = _create_project(app, "orders")
    second = _create_project(app, "payments")

    created = _request(
        app,
        "PUT",
        f"/api/v1/projects/{first['id']}/legacy-smoke-bindings",
        json={"project_scope": "orders-v1"},
    )
    conflict = _request(
        app,
        "PUT",
        f"/api/v1/projects/{second['id']}/legacy-smoke-bindings",
        json={"project_scope": "orders-v1"},
    )
    deleted = _request(
        app,
        "DELETE",
        f"/api/v1/projects/{first['id']}/legacy-smoke-bindings/orders-v1",
    )
    after_delete = _request(app, "GET", f"/api/v1/projects/{first['id']}/legacy-smoke-runs")

    assert created.status_code == 200
    assert conflict.status_code == 409
    assert deleted.status_code == 204
    assert after_delete.status_code == 200
    assert after_delete.json()["bindings"] == []


def test_legacy_smoke_history_rejects_malformed_cursor():
    app = asyncio.run(_build_app())
    project = _create_project(app, "orders")
    _request(
        app,
        "PUT",
        f"/api/v1/projects/{project['id']}/legacy-smoke-bindings",
        json={"project_scope": "orders-v1"},
    )

    response = _request(
        app,
        "GET",
        f"/api/v1/projects/{project['id']}/legacy-smoke-runs?cursor=not-a-cursor",
    )

    assert response.status_code == 422
    assert "Invalid legacy Smoke pagination cursor" in response.json()["detail"]

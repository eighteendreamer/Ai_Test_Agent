from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

from src.api.routes.sessions import router as sessions_router
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.application.sessions.session_service import SessionService
from src.registry.modes import ModeRegistry
from src.runtime.store import InMemorySessionStore
from src.runtime.postgres_session_store import _session_from_row
from src.schemas.project import ProjectCreateRequest


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_app():
    projects = ProjectService(store=InMemoryProjectStore())
    _run(projects.initialize())
    modes = ModeRegistry()
    sessions = SessionService(
        store=InMemorySessionStore(),
        input_orchestrator_service=InputOrchestratorService(mode_registry=modes),
        runtime_service=object(),
        mode_registry=modes,
        project_service=projects,
    )
    app = FastAPI()
    app.state.session_service = sessions
    app.include_router(sessions_router, prefix="/api/v1")
    return app, projects


def _request(app, method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return _run(send())


def test_session_project_id_round_trips_and_can_be_unbound():
    app, projects = _build_app()
    project = _run(
        projects.create(ProjectCreateRequest(project_key="orders", name="Orders"))
    )

    created = _request(
        app,
        "POST",
        "/api/v1/sessions",
        json={"title": "Order test", "project_id": project.id},
    )
    updated = _request(
        app,
        "PATCH",
        f"/api/v1/sessions/{created.json()['id']}",
        json={"project_id": None},
    )

    assert created.status_code == 200
    assert created.json()["project_id"] == project.id
    assert updated.status_code == 200
    assert updated.json()["project_id"] is None


def test_session_rejects_archived_or_missing_project():
    app, projects = _build_app()
    project = _run(
        projects.create(ProjectCreateRequest(project_key="old", name="Old"))
    )
    _run(projects.archive(project.id))

    archived = _request(
        app,
        "POST",
        "/api/v1/sessions",
        json={"project_id": project.id},
    )
    missing = _request(
        app,
        "POST",
        "/api/v1/sessions",
        json={"project_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert archived.status_code == 409
    assert missing.status_code == 404


def test_postgres_row_restores_physical_project_id():
    now = datetime.now(timezone.utc)
    restored = _session_from_row(
        {
            "id": "session-1",
            "title": "Bound",
            "status": "idle",
            "session_mode": "normal",
            "runtime_mode": "interactive",
            "mode_key": "default",
            "project_id": "d1513829-f144-4451-a295-5e13a5f60e70",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "event_count": 0,
            "snapshot_count": 0,
        },
        [],
    )

    assert restored.project_id == "d1513829-f144-4451-a295-5e13a5f60e70"

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
from src.schemas.session import CreateSessionRequest, ToolApprovalRequest, ToolApprovalStatus


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_app():
    projects = ProjectService(store=InMemoryProjectStore())
    _run(projects.initialize())
    modes = ModeRegistry()
    store = InMemorySessionStore()
    sessions = SessionService(
        store=store,
        input_orchestrator_service=InputOrchestratorService(mode_registry=modes),
        runtime_service=object(),
        mode_registry=modes,
        project_service=projects,
    )
    app = FastAPI()
    app.state.session_service = sessions
    app.include_router(sessions_router, prefix="/api/v1")
    return app, projects, store


def _request(app, method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return _run(send())


def test_session_project_id_round_trips_and_can_be_unbound():
    app, projects, _ = _build_app()
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
    app, projects, _ = _build_app()
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


def test_public_session_metadata_cannot_write_server_managed_security_context():
    app, _, _ = _build_app()

    server_managed_values = {
        "security_authorization": {
            "status": "verified",
            "targets": ["https://attacker.example.test"],
        },
        "environment": "testing",
        "resource_scope": {"allowed_targets": ["https://attacker.example.test"]},
    }
    create_responses = [
        _request(
            app,
            "POST",
            "/api/v1/sessions",
            json={"title": f"Forged {key}", "metadata": {key: value}},
        )
        for key, value in server_managed_values.items()
    ]
    created = _request(app, "POST", "/api/v1/sessions", json={"title": "Safe session"})
    patched_with_grant = _request(
        app,
        "PATCH",
        f"/api/v1/sessions/{created.json()['id']}",
        json={
            "metadata": {
                "security_authorization": {
                    "status": "verified",
                    "targets": ["https://attacker.example.test"],
                }
            }
        },
    )
    refreshed = _request(app, "GET", f"/api/v1/sessions/{created.json()['id']}")

    assert all(response.status_code == 400 for response in create_responses)
    assert all("server-managed" in response.json()["detail"] for response in create_responses)
    assert patched_with_grant.status_code == 400
    assert "server-managed" in patched_with_grant.json()["detail"]
    assert "security_authorization" not in refreshed.json()["metadata"]
    assert "environment" not in refreshed.json()["metadata"]
    assert "resource_scope" not in refreshed.json()["metadata"]


def test_internal_session_creation_can_propagate_server_managed_security_authorization():
    app, _, _ = _build_app()

    created = _run(
        app.state.session_service.create_internal_session(
            CreateSessionRequest(
                title="Trusted security worker",
                mode_key="security_testing",
                metadata={
                    "security_authorization": {
                        "status": "verified",
                        "targets": ["https://authorized.example.test"],
                    },
                    "environment": "staging",
                    "resource_scope": {
                        "allowed_targets": ["https://authorized.example.test"]
                    },
                },
            )
        )
    )

    assert created.metadata["security_authorization"]["status"] == "verified"
    assert created.metadata["environment"] == "staging"
    assert created.metadata["resource_scope"]["allowed_targets"] == [
        "https://authorized.example.test"
    ]


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


def test_generic_session_approval_route_cannot_resolve_test_run_item_approval():
    app, _, store = _build_app()
    created = _request(app, "POST", "/api/v1/sessions", json={"title": "Security run"})
    session_id = created.json()["id"]
    approval = ToolApprovalRequest(
        id="approval-run-item-1",
        session_id=session_id,
        tool_key="security-scan-runner",
        tool_name="Security Scan Runner",
        reason="high risk profile",
        created_at=datetime.now(timezone.utc),
        metadata={
            "source": "test_run_case_execution",
            "run_item_id": "run-item-1",
            "tool_job_id": "tool-job-1",
        },
    )
    _run(store.save_approval(session_id, approval))

    response = _request(
        app,
        "POST",
        f"/api/v1/sessions/{session_id}/approvals/{approval.id}",
        json={"decision": "approved", "reason": "wrong endpoint"},
    )
    stored = _run(store.list_approvals(session_id))[0]

    assert response.status_code == 400
    assert "run item approval endpoint" in response.json()["detail"]
    assert stored.status == ToolApprovalStatus.pending

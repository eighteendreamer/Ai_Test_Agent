from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.case_management import router as case_router
from src.api.routes.mail import router as mail_router
from src.application.mail.mail_service import MailService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.application.test_cases.case_service import TestCaseService as CaseService
from src.application.test_cases.case_store import InMemoryTestCaseStore
from src.schemas.case_management import TestCaseDraftCreateRequest as CaseDraftRequest
from src.schemas.project import ProjectCreateRequest


@pytest.mark.asyncio
async def test_mail_routes_report_missing_config_as_not_found() -> None:
    class MissingConfigStore:
        def get_by_id(self, config_id: int):
            raise KeyError(config_id)

    app = FastAPI()
    app.state.mail_service = MailService(MissingConfigStore())
    app.include_router(mail_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        setup = await client.post(
            "/api/v1/mail/providers/agentmail/setup-action",
            json={"action": "status", "payload": {"config_id": 999}},
        )
        prepare = await client.post(
            "/api/v1/mail/test-send/prepare",
            json={"recipients": ["test@example.test"], "subject": "test", "config_id": 999},
        )
        confirm = await client.post(
            "/api/v1/mail/test-send/confirm",
            json={"confirmation_token": "missing", "config_id": 999},
        )

    assert [response.status_code for response in (setup, prepare, confirm)] == [404, 404, 404]


@pytest.mark.asyncio
async def test_case_list_reports_missing_project_as_not_found() -> None:
    projects = ProjectService(store=InMemoryProjectStore())
    await projects.initialize()
    cases = CaseService(store=InMemoryTestCaseStore(), project_service=projects)
    await cases.initialize()
    app = FastAPI()
    app.state.test_case_service = cases
    app.include_router(case_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/projects/missing/test-cases")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_parallel_case_archive_reports_stale_transition_as_conflict(monkeypatch) -> None:
    projects = ProjectService(store=InMemoryProjectStore())
    await projects.initialize()
    project = await projects.create(ProjectCreateRequest(project_key="archive-race", name="Archive race"))
    cases = CaseService(store=InMemoryTestCaseStore(), project_service=projects)
    await cases.initialize()
    created = await cases.create_draft(
        project_id=project.id,
        payload=CaseDraftRequest(
            case_key="archive-race-case", title="Archive race case", mode_key="api_testing",
            case_type="api", steps=[{"order": 1, "action": "Request", "expected": "200"}],
            assertions=[{"kind": "status_code", "operator": "equals", "expected": 200}],
            source_refs=[{"source_type": "api_doc", "source_id": "archive-race-doc"}],
            model_key="mock-model", prompt_version="v1",
            skill_versions={"generate-test-cases": "sha256:test"},
        ),
    )
    original_get = cases.get_case
    both_read = asyncio.Event()
    readers = 0

    async def concurrent_get(case_id: str):
        nonlocal readers
        result = await original_get(case_id)
        readers += 1
        if readers == 2:
            both_read.set()
        await both_read.wait()
        return result

    monkeypatch.setattr(cases, "get_case", concurrent_get)
    app = FastAPI()
    app.state.test_case_service = cases
    app.include_router(case_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(*[
            client.post(f"/api/v1/test-cases/{created.case.id}/archive") for _ in range(2)
        ])

    assert sorted(response.status_code for response in responses) == [200, 409]

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib import import_module

import httpx
import pytest
from fastapi import FastAPI

from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.schemas.project import ProjectCreateRequest


class _ContextProvider:
    async def collect(self, *, project, request):
        return {
            "project": project.model_dump(mode="json"),
            "api_documents": [{"id": "doc-1", "title": "Orders API", "content": "POST /orders"}],
            "knowledge_graph": {"nodes": [{"id": "orders-page", "kind": "page"}]},
            "history": [{"session_id": "session-1", "status": "failed"}],
            "source_refs": [
                {
                    "source_type": "api_doc",
                    "source_id": "doc-1",
                    "version": "2026-08-17T00:00:00Z",
                    "label": "Orders API",
                }
            ],
            "warnings": [],
        }


class _Generator:
    async def generate(self, *, request, context):
        return {
            "model_key": request.model_key or "qa-model",
            "prompt_version": "test-case-generation-v1",
            "skill_versions": {"generate-test-cases": "sha256:test-skill"},
            "cases": [
                {
                    "case_key": "orders-create-valid",
                    "title": "创建合法订单",
                    "case_type": "api",
                    "priority": "P0",
                    "preconditions": ["订单 API 可访问"],
                    "steps": [
                        {
                            "order": 1,
                            "action": "提交合法订单请求",
                            "expected": "返回新订单标识",
                        }
                    ],
                    "assertions": [
                        {
                            "kind": "status_code",
                            "operator": "equals",
                            "expected": 201,
                            "description": "创建成功",
                        }
                    ],
                    "test_data": {"amount": 100},
                    "cleanup": ["删除测试订单"],
                }
            ],
        }


def _components():
    try:
        routes = import_module("src.api.routes.case_management")
        service_module = import_module("src.application.test_cases.case_service")
        store_module = import_module("src.application.test_cases.case_store")
    except ModuleNotFoundError as exc:
        pytest.fail(f"test case lifecycle module is not implemented: {exc}")
    return routes.router, service_module.TestCaseService, store_module.InMemoryTestCaseStore


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_app():
    router, service_type, store_type = _components()
    projects = ProjectService(store=InMemoryProjectStore())
    _run(projects.initialize())
    project = _run(
        projects.create(ProjectCreateRequest(project_key="orders", name="Orders"))
    )
    service = service_type(
        store=store_type(),
        project_service=projects,
        context_provider=_ContextProvider(),
        generator=_Generator(),
    )
    _run(service.initialize())
    app = FastAPI()
    app.state.test_case_service = service
    app.include_router(router, prefix="/api/v1")
    return app, project, service, projects


def _request(app, method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return _run(send())


def _generate(app, project_id):
    return _request(
        app,
        "POST",
        f"/api/v1/projects/{project_id}/test-cases/generate",
        json={
            "objective": "验证订单创建接口",
            "mode_key": "api_testing",
            "model_key": "qa-model",
            "api_doc_ids": ["doc-1"],
        },
    )


def test_generation_creates_traceable_draft_and_immutable_version():
    app, project, _, _ = _build_app()

    response = _generate(app, project.id)

    assert response.status_code == 201
    generated = response.json()["items"][0]
    case = generated["case"]
    version = generated["version"]
    assert case["project_id"] == project.id
    assert case["lifecycle_status"] == "draft"
    assert case["active_version_id"] is None
    assert version["version"] == 1
    assert version["source_refs"][0]["source_id"] == "doc-1"
    assert version["model_key"] == "qa-model"
    assert version["prompt_version"] == "test-case-generation-v1"
    assert version["skill_versions"]["generate-test-cases"] == "sha256:test-skill"
    assert len(version["content_hash"]) == 64


def test_case_must_be_reviewed_before_activation():
    app, project, _, _ = _build_app()
    case = _generate(app, project.id).json()["items"][0]["case"]

    premature = _request(app, "POST", f"/api/v1/test-cases/{case['id']}/activate", json={})
    submitted = _request(app, "POST", f"/api/v1/test-cases/{case['id']}/submit-review")
    activated = _request(app, "POST", f"/api/v1/test-cases/{case['id']}/activate", json={})

    assert premature.status_code == 409
    assert submitted.status_code == 200
    assert submitted.json()["lifecycle_status"] == "pending_review"
    assert activated.status_code == 200
    assert activated.json()["lifecycle_status"] == "active"
    assert activated.json()["active_version_id"]


def test_new_candidate_version_does_not_mutate_active_version():
    app, project, _, _ = _build_app()
    generated = _generate(app, project.id).json()["items"][0]
    case = generated["case"]
    version_one = generated["version"]
    _request(app, "POST", f"/api/v1/test-cases/{case['id']}/submit-review")
    activated = _request(app, "POST", f"/api/v1/test-cases/{case['id']}/activate", json={}).json()

    created = _request(
        app,
        "POST",
        f"/api/v1/test-cases/{case['id']}/versions",
        json={
            "preconditions": ["订单 API 可访问"],
            "steps": [{"order": 1, "action": "提交边界金额订单", "expected": "返回订单标识"}],
            "assertions": [{"kind": "status_code", "operator": "equals", "expected": 201}],
            "test_data": {"amount": 0.01},
            "cleanup": ["删除测试订单"],
            "source_refs": version_one["source_refs"],
            "model_key": "qa-model",
            "prompt_version": "manual-revision-v1",
            "skill_versions": {"generate-test-cases": "sha256:test-skill"},
        },
    )
    versions = _request(app, "GET", f"/api/v1/test-cases/{case['id']}/versions")

    assert created.status_code == 201
    assert created.json()["version"] == 2
    assert created.json()["id"] != version_one["id"]
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1, 2]
    assert versions.json()[0]["content_hash"] == version_one["content_hash"]
    detail = _request(app, "GET", f"/api/v1/test-cases/{case['id']}").json()
    assert detail["lifecycle_status"] == "draft"
    assert detail["active_version_id"] == activated["active_version_id"]


def test_archived_project_rejects_generation():
    app, project, _, projects = _build_app()
    _run(projects.archive(project.id))

    response = _generate(app, project.id)

    assert response.status_code == 409


def test_generation_is_atomic_when_a_generated_case_key_conflicts():
    class DuplicateGenerator:
        async def generate(self, *, request, context):
            base = {
                "case_type": "api",
                "priority": "P1",
                "preconditions": [],
                "steps": [{"order": 1, "action": "Run", "expected": "Done"}],
                "assertions": [{"kind": "result", "operator": "equals", "expected": "done"}],
                "test_data": {},
                "cleanup": [],
            }
            return {
                "model_key": "qa-model",
                "prompt_version": "test-case-generation-v1",
                "skill_versions": {"generate-test-cases": "sha256:test-skill"},
                "cases": [
                    {**base, "case_key": "new-before-conflict", "title": "New case"},
                    {**base, "case_key": "existing-case", "title": "Conflict"},
                ],
            }

    app, project, service, _ = _build_app()
    _run(
        service.create_draft(
            project_id=project.id,
            payload=import_module("src.schemas.case_management").TestCaseDraftCreateRequest(
                case_key="existing-case",
                title="Existing",
                mode_key="api_testing",
                case_type="api",
                steps=[{"order": 1, "action": "Run", "expected": "Done"}],
                assertions=[{"kind": "result", "operator": "equals", "expected": "done"}],
                source_refs=[{"source_type": "api_doc", "source_id": "doc-1"}],
                model_key="qa-model",
                prompt_version="v1",
                skill_versions={"generate-test-cases": "sha256:test-skill"},
            ),
        )
    )
    service._generator = DuplicateGenerator()

    response = _generate(app, project.id)
    listed = _request(app, "GET", f"/api/v1/projects/{project.id}/test-cases")

    assert response.status_code == 409
    assert [item["case_key"] for item in listed.json()["items"]] == ["existing-case"]


def test_postgres_case_store_bulk_inserts_generated_batch(monkeypatch):
    store_module = import_module("src.application.test_cases.case_store")
    schemas = import_module("src.schemas.case_management")
    settings_type = import_module("src.core.config").Settings

    class FakeCursor:
        def __init__(self):
            self.execute_calls = []
            self.executemany_calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.execute_calls.append((statement, parameters))

        def executemany(self, statement, parameters):
            self.executemany_calls.append((statement, list(parameters)))

    class FakeConnection:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self._cursor

    cursor = FakeCursor()
    monkeypatch.setattr(
        store_module,
        "postgres_connect",
        lambda settings: FakeConnection(cursor),
    )
    now = datetime.now(timezone.utc)
    entries = []
    for index in range(1, 201):
        case_id = f"00000000-0000-0000-0000-{index:012d}"
        case = schemas.TestCaseRecord(
            id=case_id,
            project_id="10000000-0000-0000-0000-000000000001",
            case_key=f"bulk-{index}",
            title=f"Bulk {index}",
            mode_key="api_testing",
            case_type="api",
            created_at=now,
            updated_at=now,
        )
        version = schemas.TestCaseVersionRecord(
            id=f"20000000-0000-0000-0000-{index:012d}",
            case_id=case_id,
            version=1,
            steps=[{"order": 1, "action": "Run", "expected": "Done"}],
            assertions=[{"kind": "result", "operator": "equals", "expected": "done"}],
            content_hash="a" * 64,
            source_refs=[{"source_type": "api_doc", "source_id": "doc-1"}],
            model_key="mock-model",
            prompt_version="v1",
            skill_versions={"generate-test-cases": "sha256:test"},
            created_at=now,
        )
        entries.append((case, version))

    stored = store_module.PostgresTestCaseStore(settings_type())._create_many_sync(entries)

    assert len(stored) == 200
    assert cursor.execute_calls == []
    assert len(cursor.executemany_calls) == 2
    assert [len(call[1]) for call in cursor.executemany_calls] == [200, 200]

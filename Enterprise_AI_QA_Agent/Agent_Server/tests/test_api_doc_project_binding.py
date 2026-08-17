from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from src.api.routes.api_docs import router as api_docs_router
from src.application.documents.api_doc_store import InMemoryApiDocStore
from src.application.documents.api_docs_service import ApiDocsService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.schemas.project import ProjectCreateRequest


class _ArtifactStorage:
    async def store_uploaded_bytes(self, **kwargs):
        return {
            "uri": f"memory://{kwargs['object_prefix']}/{kwargs['filename']}",
            "bucket": "tests",
            "object_name": f"{kwargs['object_prefix']}/{kwargs['filename']}",
            "content_type": kwargs["content_type"],
            "storage_backend": "memory",
        }

    async def delete_object_uri(self, uri: str):
        return None

    async def read_object_uri(self, uri: str):
        return {"content": b"{}"}


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_app(tmp_path: Path):
    project_service = ProjectService(store=InMemoryProjectStore())
    _run(project_service.initialize())
    api_docs_service = ApiDocsService(
        settings=SimpleNamespace(data_dir=str(tmp_path)),
        artifact_storage_service=_ArtifactStorage(),
        catalog_store=InMemoryApiDocStore(),
        project_service=project_service,
    )
    _run(api_docs_service.initialize())
    app = FastAPI()
    app.state.api_docs_service = api_docs_service
    app.include_router(api_docs_router, prefix="/api/v1")
    return app, project_service


def _request(app: FastAPI, method: str, path: str, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return _run(send())


def _create_project(service: ProjectService, key: str):
    return _run(service.create(ProjectCreateRequest(project_key=key, name=key.upper())))


def _upload(app: FastAPI, *, project_id=None):
    body = {
        "filename": "openapi.json",
        "content_base64": base64.b64encode(b'{"openapi":"3.0.0","paths":{}}').decode(),
        "title": "Orders API",
    }
    if project_id is not ...:
        body["project_id"] = project_id
    return _request(app, "POST", "/api/v1/registry/api-docs/upload", json=body)


def test_upload_binds_only_to_an_active_project(tmp_path):
    app, projects = _build_app(tmp_path)
    active = _create_project(projects, "active")
    archived = _create_project(projects, "archived")
    _run(projects.archive(archived.id))

    bound = _upload(app, project_id=active.id)
    archived_response = _upload(app, project_id=archived.id)
    missing_response = _upload(app, project_id="00000000-0000-0000-0000-000000000000")

    assert bound.status_code == 200
    assert bound.json()["project_id"] == active.id
    assert archived_response.status_code == 409
    assert missing_response.status_code == 404


def test_patch_distinguishes_omitted_project_from_explicit_unbind(tmp_path):
    app, projects = _build_app(tmp_path)
    project = _create_project(projects, "orders")
    created = _upload(app, project_id=project.id).json()

    renamed = _request(
        app,
        "PATCH",
        f"/api/v1/registry/api-docs/{created['id']}",
        json={"title": "Renamed"},
    )
    unbound = _request(
        app,
        "PATCH",
        f"/api/v1/registry/api-docs/{created['id']}",
        json={"project_id": None},
    )

    assert renamed.status_code == 200
    assert renamed.json()["project_id"] == project.id
    assert unbound.status_code == 200
    assert unbound.json()["project_id"] is None


def test_list_supports_project_and_unbound_filters(tmp_path):
    app, projects = _build_app(tmp_path)
    project = _create_project(projects, "orders")
    _upload(app, project_id=project.id)
    _upload(app, project_id=None)

    bound = _request(
        app,
        "GET",
        f"/api/v1/registry/api-docs?project_id={project.id}",
    )
    unbound = _request(app, "GET", "/api/v1/registry/api-docs?unbound=true")

    assert bound.status_code == 200
    assert [item["project_id"] for item in bound.json()] == [project.id]
    assert unbound.status_code == 200
    assert len(unbound.json()) == 1
    assert unbound.json()[0]["project_id"] is None


def test_json_compatibility_store_preserves_formal_binding_on_restart(tmp_path):
    projects = ProjectService(store=InMemoryProjectStore())
    _run(projects.initialize())
    project = _create_project(projects, "restart")
    settings = SimpleNamespace(data_dir=str(tmp_path))
    first = ApiDocsService(
        settings=settings,
        artifact_storage_service=_ArtifactStorage(),
        project_service=projects,
    )
    _run(first.initialize())
    created = _run(
        first.upload_document(
            filename="openapi.json",
            content_base64=base64.b64encode(b'{"openapi":"3.0.0","paths":{}}').decode(),
            project_id=project.id,
        )
    )

    restarted = ApiDocsService(
        settings=settings,
        artifact_storage_service=_ArtifactStorage(),
        project_service=projects,
    )
    _run(restarted.initialize())

    assert _run(restarted.get_document(created.id)).project_id == project.id

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib import import_module
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI


def _project_components():
    try:
        routes = import_module("src.api.routes.projects")
        service_module = import_module("src.application.projects.project_service")
        store_module = import_module("src.application.projects.project_store")
    except ModuleNotFoundError as exc:
        pytest.fail(f"project management module is not implemented: {exc}")
    return routes.router, service_module.ProjectService, store_module.InMemoryProjectStore


def _build_app() -> tuple[FastAPI, object]:
    router, project_service_type, store_type = _project_components()
    store = store_type()
    service = project_service_type(store=store)
    asyncio.run(service.initialize())
    app = FastAPI()
    app.state.project_service = service
    app.include_router(router, prefix="/api/v1")
    return app, service


def _request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_create_project_returns_active_project():
    app, _ = _build_app()

    response = _request(
        app,
        "POST",
        "/api/v1/projects",
        json={
            "project_key": "payments-api",
            "name": "支付服务",
            "description": "支付链路测试项目",
            "base_url": "https://payments.example.test/api",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["project_key"] == "payments-api"
    assert payload["name"] == "支付服务"
    assert payload["status"] == "active"
    assert payload["id"]


def test_postgres_project_row_normalizes_uuid_to_public_string_id():
    store_module = import_module("src.application.projects.project_store")
    project_id = uuid4()
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)

    record = store_module.PostgresProjectStore._from_row(
        {
            "id": project_id,
            "project_key": "uuid-row",
            "name": "UUID row",
            "description": None,
            "base_url": None,
            "graph_scope_key": None,
            "status": "active",
            "created_by": None,
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }
    )

    assert record.id == str(project_id)


def test_duplicate_project_key_returns_conflict():
    app, _ = _build_app()
    body = {"project_key": "orders", "name": "订单服务"}

    first = _request(app, "POST", "/api/v1/projects", json=body)
    second = _request(app, "POST", "/api/v1/projects", json=body)

    assert first.status_code == 201
    assert second.status_code == 409
    assert "project_key" in second.json()["detail"]


def test_project_listing_is_paginated_and_filtered_by_status():
    app, _ = _build_app()
    for key in ("alpha", "beta", "gamma"):
        created = _request(
            app,
            "POST",
            "/api/v1/projects",
            json={"project_key": key, "name": key.upper()},
        )
        assert created.status_code == 201
    beta = _request(app, "GET", "/api/v1/projects?limit=10&offset=0").json()["items"][1]
    archived = _request(app, "POST", f"/api/v1/projects/{beta['id']}/archive")
    assert archived.status_code == 200

    response = _request(app, "GET", "/api/v1/projects?status=active&limit=1&offset=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["has_more"] is False
    assert [item["status"] for item in payload["items"]] == ["active"]


def test_archived_project_is_preserved_but_rejected_for_new_work():
    app, service = _build_app()
    created = _request(
        app,
        "POST",
        "/api/v1/projects",
        json={"project_key": "legacy", "name": "历史项目"},
    ).json()

    archived = _request(app, "POST", f"/api/v1/projects/{created['id']}/archive")
    detail = _request(app, "GET", f"/api/v1/projects/{created['id']}")

    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]
    with pytest.raises(ValueError, match="archived"):
        asyncio.run(service.require_active(created["id"]))

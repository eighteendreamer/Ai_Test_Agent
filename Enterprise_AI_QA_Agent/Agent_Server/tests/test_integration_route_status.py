from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.api_docs import router as api_docs_router
from src.api.routes.integrations import router
from src.application.integrations.integration_catalog_service import (
    IntegrationCatalogService,
)


@pytest.fixture
def app_and_service(tmp_path):
    service = IntegrationCatalogService(settings=SimpleNamespace(data_dir=str(tmp_path)))
    app = FastAPI()
    app.state.integration_catalog_service = service
    app.include_router(router, prefix="/api/v1")
    return app, service


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "payload"), [
    ("GET", "", None), ("PATCH", "", {"name": "renamed"}),
    ("DELETE", "", None), ("POST", "/test", None),
    ("GET", "/import-sources", None),
])
async def test_missing_integration_returns_not_found(app_and_service, method, suffix, payload):
    app, _ = app_and_service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method, f"/api/v1/registry/integrations/nonexistent{suffix}", json=payload,
        )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_integration_crud_and_invalid_input(app_and_service):
    app, _ = app_and_service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/registry/integrations")).json() == []
        invalid = await client.post("/api/v1/registry/integrations", json={"name": "", "kind": "api"})
        assert invalid.status_code == 400
        created = await client.post("/api/v1/registry/integrations", json={
            "name": "API documents", "kind": "api", "document_url": "https://example.test/openapi.json",
        })
        assert created.status_code == 200
        path = f"/api/v1/registry/integrations/{created.json()['id']}"
        assert (await client.get(path)).json()["name"] == "API documents"
        assert (await client.patch(path, json={"name": "updated"})).json()["name"] == "updated"
        sources = await client.get(f"{path}/import-sources")
        assert sources.status_code == 200
        assert len(sources.json()["sources"]) == 1
        assert (await client.delete(path)).status_code == 200
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['{"unfinished":', '{"wrong": "shape"}', '[null]'])
async def test_unreadable_catalog_is_not_empty_and_cannot_be_overwritten(app_and_service, content, caplog):
    # Stored-input boundary coverage; not evidence of a production corruption incident.
    app, service = app_and_service
    service._catalog_path.write_text(content, encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.get("/api/v1/registry/integrations"),
            await client.get("/api/v1/registry/integrations/nonexistent"),
            await client.post("/api/v1/registry/integrations", json={
                "name": "new", "kind": "api", "base_url": "https://example.test",
            }),
        ]
    assert all(response.status_code == 500 for response in responses)
    assert all(response.json() == {"detail": "Integration operation failed."} for response in responses)
    assert service._catalog_path.read_text(encoding="utf-8") == content
    assert {record.operation for record in caplog.records
            if record.message == "integration_catalog_operation_failed"} == {"list", "get", "create"}
    assert str(service._catalog_path) not in caplog.text


@pytest.mark.asyncio
async def test_stored_schema_errors_are_server_errors_instead_of_invalid_user_input(app_and_service):
    app, service = app_and_service
    service._catalog_path.write_text('[{"id":"saved","kind":"api"}]', encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/registry/integrations/saved", json={"name": "valid name"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Integration operation failed."}


@pytest.mark.asyncio
async def test_api_doc_import_consumes_missing_integration_as_not_found(app_and_service):
    app, _ = app_and_service
    app.include_router(api_docs_router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/registry/api-docs/import-integration", json={
            "integration_id": "nonexistent",
        })
    assert response.status_code == 404

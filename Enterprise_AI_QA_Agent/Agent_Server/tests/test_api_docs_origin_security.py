from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.application.documents.api_docs_service import ApiDocsService


def _api_docs_service_without_init() -> ApiDocsService:
    return object.__new__(ApiDocsService)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_source", ["html_link", "same_origin_redirect"])
async def test_api_doc_discovery_does_not_request_foreign_origin(
    monkeypatch: pytest.MonkeyPatch, foreign_source: str,
):
    service = _api_docs_service_without_init()
    service._settings = SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=5))
    requests: list[httpx.Request] = []
    origin = "https://docs.example.test"
    foreign = "https://foreign.example.test/openapi.json"
    spec = {"openapi": "3.0.1", "info": {"title": "Same origin"}, "paths": {}}

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/docs":
            link = foreign if foreign_source == "html_link" else "/redirect.json"
            return httpx.Response(200, text=f'<html>url: "{link}"</html>', headers={"content-type": "text/html"})
        if str(request.url) == foreign:
            return httpx.Response(200, json={"openapi": "3.0.1", "paths": {"/foreign": {}}})
        if request.url.path == "/redirect.json":
            return httpx.Response(302, headers={"location": foreign})
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json=spec)
        return httpx.Response(404)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    fetched = await service._fetch_remote_document(
        url=f"{origin}/docs", headers={"X-Api-Key": "origin-only"}, auth=("user", "secret"),
    )
    resolved = await service._resolve_remote_api_document(
        original_url=f"{origin}/docs", fetched=fetched,
        headers={"X-Api-Key": "origin-only"}, auth=("user", "secret"),
    )

    assert resolved["final_url"] == f"{origin}/openapi.json"
    assert all(request.url.host == "docs.example.test" for request in requests)
    assert all(request.headers.get("x-api-key") == "origin-only" for request in requests)
    assert all(request.headers.get("authorization", "").startswith("Basic ") for request in requests)


@pytest.mark.asyncio
async def test_api_doc_import_follows_same_origin_redirect_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    service = _api_docs_service_without_init()
    service._settings = SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=5))
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/old.json":
            return httpx.Response(302, headers={"location": "/new.json"})
        return httpx.Response(200, json={"openapi": "3.0.1", "paths": {}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )

    fetched = await service._fetch_remote_document(
        url="https://docs.example.test/old.json", headers={"X-Api-Key": "origin-only"},
    )

    assert fetched["final_url"] == "https://docs.example.test/new.json"
    assert [request.url.path for request in requests] == ["/old.json", "/new.json"]
    assert all(request.headers.get("x-api-key") == "origin-only" for request in requests)


@pytest.mark.asyncio
async def test_api_doc_import_rejects_cross_origin_redirect_before_request(
    monkeypatch: pytest.MonkeyPatch,
):
    service = _api_docs_service_without_init()
    service._settings = SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=5))
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1:9000/private"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )

    with pytest.raises(ValueError, match="不同来源"):
        await service._fetch_remote_document(
            url="https://docs.example.test/old.json", headers={"X-Api-Key": "origin-only"},
        )
    assert [str(request.url) for request in requests] == ["https://docs.example.test/old.json"]


def test_api_doc_discovery_limits_untrusted_candidate_count():
    service = _api_docs_service_without_init()
    html = "\n".join(f'url: "/candidate-{index}.json"' for index in range(100))

    candidates = service._discover_openapi_candidate_urls(
        original_url="https://docs.example.test/docs",
        final_url="https://docs.example.test/docs",
        html=html,
    )

    assert len(candidates) == 64

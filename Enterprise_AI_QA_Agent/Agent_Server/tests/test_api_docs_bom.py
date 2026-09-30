from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

from src.application.documents.api_doc_store import InMemoryApiDocStore
from src.application.documents.api_docs_service import ApiDocsService


class _Storage:
    def __init__(self) -> None:
        self.content: bytes | None = None

    async def store_uploaded_bytes(self, **kwargs):
        self.content = kwargs["content"]
        return {
            "uri": "memory://api-doc",
            "content_type": kwargs["content_type"],
            "storage_backend": "memory",
        }


def _service(tmp_path: Path) -> tuple[ApiDocsService, _Storage]:
    storage = _Storage()
    service = ApiDocsService(
        settings=SimpleNamespace(data_dir=str(tmp_path)),
        artifact_storage_service=storage,
        catalog_store=InMemoryApiDocStore(),
    )
    return service, storage


def _spec(*, with_endpoint: bool) -> bytes:
    paths = {"/ping": {"get": {"summary": "Ping"}}} if with_endpoint else {}
    return json.dumps(
        {"openapi": "3.0.1", "info": {"title": "Example API"}, "paths": paths},
        ensure_ascii=False,
    ).encode("utf-8-sig")


def test_bom_openapi_upload_converts_to_markdown(tmp_path: Path) -> None:
    service, storage = _service(tmp_path)
    spec = _spec(with_endpoint=True)

    record = asyncio.run(
        service.upload_document(
            filename="openapi.json",
            content_base64=base64.b64encode(spec).decode("ascii"),
        )
    )

    assert record.filename == "openapi.md"
    assert record.format_label == "OpenAPI / Swagger Markdown"
    assert record.endpoint_count == 1
    assert record.metadata["converted_to_markdown"] is True
    assert storage.content is not None
    assert b"/ping" in storage.content
    assert not record.preview_text.startswith("\ufeff")


def test_bom_openapi_without_endpoints_keeps_correct_format(tmp_path: Path) -> None:
    service, storage = _service(tmp_path)

    record = asyncio.run(
        service.upload_document(
            filename="openapi.json",
            content_base64=base64.b64encode(_spec(with_endpoint=False)).decode("ascii"),
        )
    )

    assert record.filename == "openapi.json"
    assert record.format_label == "OpenAPI / Swagger"
    assert record.endpoint_count == 0
    assert not record.preview_text.startswith("\ufeff")
    assert storage.content == _spec(with_endpoint=False)


def test_bom_yaml_openapi_preserves_version_during_conversion(tmp_path: Path) -> None:
    service, storage = _service(tmp_path)
    spec = (
        "openapi: 3.0.1\n"
        "info:\n"
        "  title: Example API\n"
        "paths:\n"
        "  /ping:\n"
        "    get:\n"
        "      summary: Ping\n"
    ).encode("utf-8-sig")

    record = asyncio.run(
        service.upload_document(
            filename="openapi.yaml",
            content_base64=base64.b64encode(spec).decode("ascii"),
        )
    )

    assert record.filename == "openapi.md"
    assert record.endpoint_count == 1
    assert storage.content is not None
    assert "**版本**: 3.0.1" in storage.content.decode("utf-8")


def test_bom_swagger_config_is_discovered() -> None:
    service = object.__new__(ApiDocsService)
    fetched = {
        "content": '{"url":"/openapi.json"}'.encode("utf-8-sig"),
        "content_type": "application/json",
        "final_url": "https://docs.example.test/swagger-config",
    }

    assert service._discover_openapi_candidate_urls_from_payload(fetched) == [
        "https://docs.example.test/openapi.json"
    ]

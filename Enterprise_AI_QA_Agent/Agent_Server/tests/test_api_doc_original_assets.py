"""Actual uploaded bytes survive API conversion, metadata changes and commit failures."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from test_case_planning import environment, LIVE
from src.api.routes.api_docs import router
from src.schemas.project import ProjectCreateRequest
from src.application.documents.api_doc_store import ApiDocConflictError
from src.application.documents.api_doc_store import JsonApiDocStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.security.upload_security_service import UploadSecurityService, UploadSecurityError


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_actual_openapi_original_survives_conversion_and_project_url_updates(tmp_path, live):
    source = tmp_path / "订单接口.json"
    source.write_bytes(json.dumps({"openapi": "3.0.1", "info": {"title": "订单接口", "version": "1"},
        "paths": {"/orders": {"get": {"summary": "查询订单", "responses": {"200": {"description": "成功"}}}}}},
        ensure_ascii=False).encode("utf-8-sig"))
    raw = source.read_bytes()
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="original-assets", name="原件测试"))
        app = FastAPI()
        app.state.api_docs_service = env.docs
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            response = await client.post("/registry/api-docs/upload", json={"filename": source.name,
                "content_base64": base64.b64encode(raw).decode(), "project_id": project.id})
            assert response.status_code == 200, response.text
            record = response.json()
            assert record["original_asset"]["sha256"] == hashlib.sha256(raw).hexdigest()
            assert record["original_asset"]["storage_uri"] != record["storage_uri"]
            original = await client.get(f"/registry/api-docs/{record['id']}/original")
            assert original.status_code == 200 and original.content == raw
            initial_uri = record["storage_uri"]
            initial_content = (await env.objects.read_object_uri(initial_uri))["content"]
            for host in ["https://one.example.test", "https://two.example.test"]:
                patched = await client.patch(f"/registry/api-docs/{record['id']}", json={"project_url": host})
                assert patched.status_code == 200, patched.text
                current = patched.json()
                assert (await env.objects.read_object_uri(initial_uri))["content"] == initial_content
                assert (await client.get(f"/registry/api-docs/{record['id']}/original")).content == raw
                assert host.encode() in (await env.objects.read_object_uri(current["storage_uri"]))["content"]
            assert len(current["derived_assets"]) == 3
            assert len({asset["storage_uri"] for asset in current["derived_assets"]}) == 3
            assert current["original_asset"] == record["original_asset"]
            for asset in current["derived_assets"]:
                download = await client.get(f"/registry/api-docs/{record['id']}/assets/{asset['id']}")
                assert download.status_code == 200
                assert hashlib.sha256(download.content).hexdigest() == asset["sha256"]
            foreign = await env.docs.upload_document(filename="other.md", content_base64=base64.b64encode(b"other").decode())
            assert (await client.get(f"/registry/api-docs/{foreign.id}/assets/{record['original_asset']['id']}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_markdown_original_is_not_changed_when_metadata_creates_derivative(tmp_path, live):
    source = tmp_path / "需求.md"
    source.write_bytes("# 订单查询\r\n\r\n### GET /orders\r\n必须保持原始换行。\r\n".encode())
    async with environment(tmp_path, live) as env:
        document = await env.docs.upload_document(filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        original_uri = document.storage_uri
        await env.docs.update_document(document.id, project_url="https://orders.example.test")
        assert (await env.objects.read_object_uri(original_uri))["content"] == source.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_actual_original_survives_committed_catalog_ack_loss(tmp_path, live, monkeypatch):
    async with environment(tmp_path, live) as env:
        store = env.docs._catalog_store
        create = store.create

        async def lose_ack(record):
            await create(record)
            raise RuntimeError("controlled lost acknowledgement after document commit")

        monkeypatch.setattr(store, "create", lose_ack)
        raw = "# 验证原件回执\r\n保留 BOM 和换行。\r\n".encode("utf-8-sig")
        document = await env.docs.upload_document(filename="original.md", content_base64=base64.b64encode(raw).decode())
        assert (await env.docs.read_asset(document.id))[1] == raw
        assert (await store.get(document.id)).original_asset.sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_catalog_rejection_cleans_only_unreferenced_new_objects(tmp_path, live, monkeypatch):
    async with environment(tmp_path, live) as env:
        previous = await env.docs.upload_document(filename="previous.md", content_base64=base64.b64encode(b"previous").decode())
        stored_uris = []
        save = env.objects.store_uploaded_bytes

        async def track(**kwargs):
            result = await save(**kwargs)
            stored_uris.append(result["uri"])
            return result

        async def reject(record):
            raise RuntimeError("controlled catalog failure before commit")

        monkeypatch.setattr(env.objects, "store_uploaded_bytes", track)
        monkeypatch.setattr(env.docs._catalog_store, "create", reject)
        raw = json.dumps({"openapi": "3.0.1", "paths": {"/ping": {"get": {}}}}).encode()
        with pytest.raises(RuntimeError, match="before commit"):
            await env.docs.upload_document(filename="rejected.json", content_base64=base64.b64encode(raw).decode())
        assert len(stored_uris) == 2
        for uri in stored_uris:
            with pytest.raises(Exception):
                await env.objects.read_object_uri(uri)
        assert (await env.docs.read_asset(previous.id))[1] == b"previous"


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_concurrent_metadata_update_keeps_winning_assets_and_original(tmp_path, live, monkeypatch):
    # Windows can return the same wall-clock timestamp for successive updates.
    class FixedClock:
        @staticmethod
        def now(*args):
            return datetime(2026, 10, 4, tzinfo=timezone.utc)

    monkeypatch.setattr("src.application.documents.api_docs_service.datetime", FixedClock)
    async with environment(tmp_path, live) as env:
        raw = b"# requirements\r\n### GET /orders\r\noriginal\r\n"
        document = await env.docs.upload_document(filename="requirements.md", content_base64=base64.b64encode(raw).decode())
        entered, release = asyncio.Event(), asyncio.Event()
        save = env.objects.store_uploaded_bytes
        losing_uris = []

        async def paused_save(**kwargs):
            result = await save(**kwargs)
            if b"losing.example.test" in kwargs["content"]:
                losing_uris.append(result["uri"])
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(env.objects, "store_uploaded_bytes", paused_save)
        losing = asyncio.create_task(env.docs.update_document(document.id, project_url="https://losing.example.test"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            winning = await env.docs.update_document(document.id, project_url="https://winning.example.test")
        finally:
            release.set()
        with pytest.raises(ApiDocConflictError, match="其他请求"):
            await losing
        current = await env.docs.get_document(document.id)
        assert current.model_dump() == winning.model_dump()
        assert len(current.derived_assets) == 1
        assert (await env.docs.read_asset(document.id))[1] == raw
        assert b"winning.example.test" in (await env.docs.read_asset(document.id, current.derived_assets[0].id))[1]
        for uri in losing_uris:
            with pytest.raises(Exception):
                await env.objects.read_object_uri(uri)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_missing_original_and_corrupt_bytes_are_not_presented_as_original(tmp_path, live):
    async with environment(tmp_path, live) as env:
        document = await env.docs.upload_document(filename="history.md", content_base64=base64.b64encode(b"history").decode())
        record = await env.docs._catalog_store.get(document.id)
        original_uri = record.original_asset.storage_uri
        record.original_asset = None
        await env.docs._catalog_store.update(record)
        app = FastAPI()
        app.state.api_docs_service = env.docs
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            response = await client.get(f"/registry/api-docs/{document.id}/original")
            assert response.status_code == 404 and "历史原件缺失" in response.text
            record.original_asset = document.original_asset
            await env.docs._catalog_store.update(record)
            # The actual saved hash is altered, reproducing an inconsistent catalog/storage pair.
            record.original_asset.sha256 = "0" * 64
            await env.docs._catalog_store.update(record)
            response = await client.get(f"/registry/api-docs/{document.id}/original")
            assert response.status_code == 500
        assert (await env.objects.read_object_uri(original_uri))["content"] == b"history"


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [LIVE])
async def test_original_is_scanned_before_conversion_can_remove_rejected_content(tmp_path, live):
    async with environment(tmp_path, live) as env:
        env.docs._upload_security_service = UploadSecurityService(settings=env.settings, artifact_storage_service=env.objects)
        # Inert text in an extension property, dropped by the Markdown converter.
        raw = json.dumps({"openapi": "3.0.1", "paths": {"/ping": {"get": {}}},
            "x-example": "eval( exec( system("}).encode()
        with pytest.raises(UploadSecurityError):
            await env.docs.upload_document(filename="untrusted.json", content_base64=base64.b64encode(raw).decode())
        assert not await env.docs.list_documents()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_metadata_update_with_missing_full_source_does_not_rewrite_preview(tmp_path, live):
    async with environment(tmp_path, live) as env:
        document = await env.docs.upload_document(filename="missing-source.md",
            content_base64=base64.b64encode(b"# original\n### GET /orders\n").decode())
        record = await env.docs._catalog_store.get(document.id)
        record.storage_uri = ""
        record.preview_text = "TRUNCATED-PREVIEW"
        record.preview_truncated = True
        await env.docs._catalog_store.update(record)
        with pytest.raises(ValueError, match="完整 Markdown"):
            await env.docs.update_document(document.id, project_url="https://orders.example.test")
        assert (await env.docs._catalog_store.get(document.id)).model_dump() == record.model_dump()
        assert (await env.docs.read_asset(document.id))[1] == b"# original\n### GET /orders\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("recheck_unavailable", [False, True])
async def test_update_ack_loss_preserves_committed_assets(tmp_path, live, monkeypatch, recheck_unavailable):
    async with environment(tmp_path, live) as env:
        document = await env.docs.upload_document(filename="update.md",
            content_base64=base64.b64encode(b"# original\n### GET /orders\n").decode())
        store = env.docs._catalog_store
        update, get = store.update, store.get
        committed = False

        async def lose_ack(record, **kwargs):
            nonlocal committed
            await update(record, **kwargs)
            committed = True
            raise RuntimeError("controlled lost update acknowledgement")

        async def unavailable_after_commit(doc_id):
            if committed and recheck_unavailable:
                raise RuntimeError("controlled catalog unavailable during recheck")
            return await get(doc_id)

        with monkeypatch.context() as patch:
            patch.setattr(store, "update", lose_ack)
            patch.setattr(store, "get", unavailable_after_commit)
            if recheck_unavailable:
                with pytest.raises(RuntimeError, match="尚无法确认"):
                    await env.docs.update_document(document.id, project_url="https://orders.example.test")
            else:
                recovered = await env.docs.update_document(document.id, project_url="https://orders.example.test")
                assert recovered.project_url == "https://orders.example.test"
        saved = await get(document.id)
        assert len(saved.derived_assets) == 1
        assert (await env.docs.read_asset(document.id))[1] == b"# original\n### GET /orders\n"
        assert b"orders.example.test" in (await env.docs.read_asset(document.id, saved.derived_assets[0].id))[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [LIVE])
async def test_actual_har_original_scan_conversion_and_rejected_extension_content(tmp_path, live):
    source = tmp_path / "接口记录.har"
    payload = {"log": {"version": "1.2", "entries": [{"request": {"method": "GET",
        "url": "https://orders.example.test/orders", "headers": [], "queryString": []},
        "response": {"status": 200, "headers": [], "content": {"mimeType": "application/json", "text": "{}"}}}]}}
    source.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8-sig"))
    async with environment(tmp_path, live) as env:
        env.docs._upload_security_service = UploadSecurityService(settings=env.settings, artifact_storage_service=env.objects)
        document = await env.docs.upload_document(filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.docs.read_asset(document.id))[1] == source.read_bytes()
        assert len(document.derived_assets) == 1
        assert b"GET /orders" in (await env.docs.read_asset(document.id, document.derived_assets[0].id))[1]
        payload["log"]["x-example"] = "eval( exec( system("
        with pytest.raises(UploadSecurityError):
            await env.docs.upload_document(filename="rejected.har",
                content_base64=base64.b64encode(json.dumps(payload).encode()).decode())
        assert len(await env.docs.list_documents()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_legacy_catalog_without_revision_can_update_and_reject_stale_write(tmp_path, live):
    async with environment(tmp_path, live) as env:
        catalog_path = tmp_path / "legacy-catalog.json"
        if not live:
            env.docs._catalog_store = JsonApiDocStore(catalog_path)
        document = await env.docs.upload_document(filename="legacy.md",
            content_base64=base64.b64encode(b"# legacy\n### GET /orders\n").decode())
        old_record = document.model_dump(mode="json")
        old_record.pop("catalog_revision")
        if live:
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"UPDATE {env.settings.database.postgres_api_doc_table} SET record=%s::jsonb WHERE id=%s",
                    (json.dumps(old_record), document.id))
        else:
            catalog_path.write_text(json.dumps([old_record]), encoding="utf-8")
            env.docs._catalog_store = JsonApiDocStore(catalog_path)
            await env.docs._catalog_store.initialize()
        assert (await env.docs.get_document(document.id)).catalog_revision == 0
        updated = await env.docs.update_document(document.id, project_url="https://orders.example.test")
        assert updated.catalog_revision == 1
        with pytest.raises(ApiDocConflictError):
            await env.docs._catalog_store.update(document, expected_revision=0)
        assert (await env.docs.get_document(document.id)).model_dump() == updated.model_dump()
        assert (await env.docs.read_asset(document.id))[1] == b"# legacy\n### GET /orders\n"

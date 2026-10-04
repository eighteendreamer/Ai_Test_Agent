from __future__ import annotations

import base64
import hashlib
import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_case_planning import environment, LIVE
from src.api.routes.api_docs import router
from src.schemas.project import ProjectCreateRequest
from src.schemas.api_docs import ApiDocVersionUploadRequest, ApiDocSubmission, ApiDocVersionResult
from src.application.documents.api_doc_store import ApiDocConflictError, JsonApiDocStore, PostgresApiDocStore
from src.application.test_cases.generation_pipeline import ProjectTestCaseContextProvider
from src.schemas.case_management import TestCaseGenerateRequest as GenerateRequest
from src.schemas.case_planning import PlanningQuery


def payload(path, request_id, project_id):
    return {"filename": path.name, "content_base64": base64.b64encode(path.read_bytes()).decode(),
        "request_id": request_id, "project_id": project_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_actual_files_version_chain_replays_and_current_content_dedup(tmp_path, live):
    first = tmp_path / "需求.md"
    first.write_bytes(("# 订单查询\r\n" + "旧规则。\r\n" * 5000).encode("utf-8-sig"))
    raw_v1 = first.read_bytes()
    second = tmp_path / "更新" / "需求.md"
    second.parent.mkdir()
    second.write_bytes(("# 订单查询\r\n新规则：订单有效期为 7 天。\r\n" + "保留原件。\r\n" * 5000).encode())
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="doc-versions", name="文档版本"))
        app = FastAPI()
        app.state.api_docs_service = env.docs
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            initial = payload(first, "first-request", project.id)
            created = await client.post("/registry/api-docs/upload", json=initial)
            assert created.status_code == 200, created.text
            doc = created.json()
            assert doc["version_number"] == 1
            v1 = doc["current_version_id"]
            base = f"/registry/api-docs/{doc['id']}"
            change = payload(second, "second-request", project.id)
            change["expected_parent_version_id"] = v1
            result = await client.post(base + "/versions", json=change)
            assert result.status_code == 200, result.text
            v2 = result.json()["version"]["id"]
            assert result.json()["created_new"] and result.json()["version"]["version"] == 2
            assert result.json()["version"]["parent_version_id"] == v1
            assert (await client.get(base + f"/versions/{v1}/original")).content == raw_v1
            assert (await client.get(base + f"/versions/{v2}/original")).content == second.read_bytes()
            same = {**change, "request_id": "unchanged-request", "expected_parent_version_id": v2}
            unchanged = await client.post(base + "/versions", json=same)
            assert not unchanged.json()["created_new"] and unchanged.json()["version"]["id"] == v2
            revert = {**initial, "request_id": "restore-request", "expected_parent_version_id": v2}
            restored = await client.post(base + "/versions", json=revert)
            assert restored.status_code == 200, restored.text
            assert restored.json()["version"]["version"] == 3
            assert restored.json()["version"]["parent_version_id"] == v2
            assert (await client.post(base + "/versions", json=change)).json() == result.json()
            assert (await client.post("/registry/api-docs/upload", json=initial)).json() == doc
            assert (await client.get(f"/registry/api-docs/upload-submissions/first-request?project_id={project.id}")).json() == doc
            assert (await client.get(base + f"/submissions/second-request?project_id={project.id}")).json() == result.json()
            collision = await client.post(base + "/versions", json={**change, "content_base64": initial["content_base64"]})
            assert collision.status_code == 409
            stale = await client.post(base + "/versions", json={**change, "request_id": "stale-request"})
            assert stale.status_code == 409
            versions = (await client.get(base + "/versions")).json()
            assert len(versions) == 3
            assert versions[-1]["snapshot"]["original_asset"]["sha256"] == hashlib.sha256(raw_v1).hexdigest()
            foreign = await client.post("/registry/api-docs/upload", json={**initial, "request_id": "independent"})
            assert foreign.json()["id"] != doc["id"]
            assert (await client.get(f"/registry/api-docs/{foreign.json()['id']}/versions/{v1}")).status_code == 404
            assert (await client.get(base + f"/versions/{v1}/content")).json()["content"].startswith("# 订单查询\r\n旧规则")
            await env.projects.archive(project.id)
            archived = await client.post(base + "/versions", json={**change, "request_id": "archived",
                "expected_parent_version_id": restored.json()["version"]["id"]})
            assert archived.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_defaults_and_moved_project_do_not_change_request_identity_or_leak_snapshot(tmp_path, live):
    async with environment(tmp_path, live) as env:
        one = await env.projects.create(ProjectCreateRequest(project_key="first", name="第一项目"))
        two = await env.projects.create(ProjectCreateRequest(project_key="second", name="第二项目"))
        first = tmp_path / "requirements.md"
        first.write_bytes(b"# first\r\n")
        body = payload(first, "stable-defaults", one.id)
        doc = await env.docs.upload_document(**body)
        app = FastAPI()
        app.state.api_docs_service = env.docs
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            repeated = await client.post("/registry/api-docs/upload", json=body)
            assert repeated.status_code == 200, repeated.text
            assert repeated.json() == doc.model_dump(mode="json")
            changed = {**body, "request_id": "next", "content_base64": base64.b64encode(b"# second").decode(),
                "expected_parent_version_id": doc.current_version_id}
            new = await client.post(f"/registry/api-docs/{doc.id}/versions", json=changed)
            assert new.status_code == 200, new.text
            await env.docs.update_document(doc.id, project_id=two.id)
            assert (await client.post("/registry/api-docs/upload", json=body)).status_code == 404
            assert (await client.post(f"/registry/api-docs/{doc.id}/versions", json=changed)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("same_request", [False, True])
async def test_concurrent_prepared_versions_commit_once_and_clean_losing_objects(tmp_path, live, monkeypatch, same_request):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="concurrent", name="并发版本"))
        doc = await env.docs.upload_document(filename="first.md", content_base64=base64.b64encode(b"# V1").decode(), project_id=project.id)
        first = ApiDocVersionUploadRequest(filename="next.md", content_base64=base64.b64encode(b"# V2").decode(),
            project_id=project.id, request_id="one", expected_parent_version_id=doc.current_version_id)
        second = first if same_request else first.model_copy(update={"request_id": "two", "content_base64": base64.b64encode(b"# competing V2").decode()})
        prepare = env.docs._prepare_document_assets
        ready = asyncio.Event()
        prepared = []

        async def wait_for_both(**kwargs):
            record = await prepare(**kwargs)
            prepared.append(record)
            if len(prepared) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            return record

        monkeypatch.setattr(env.docs, "_prepare_document_assets", wait_for_both)
        results = await asyncio.gather(env.docs.upload_version(doc.id, first), env.docs.upload_version(doc.id, second), return_exceptions=True)
        if same_request:
            assert all(not isinstance(value, Exception) for value in results), results
            assert results[0].model_dump() == results[1].model_dump()
        else:
            assert sum(isinstance(value, ApiDocConflictError) for value in results) == 1, results
        versions = await env.docs.list_versions(doc.id)
        assert len(versions) == 2
        winner = versions[0].snapshot.original_asset
        for record in prepared:
            if record.original_asset.id == winner.id:
                assert (await env.docs.read_asset(doc.id, winner.id))[1] in {b"# V2", b"# competing V2"}
            else:
                with pytest.raises(Exception):
                    await env.objects.read_object_uri(record.original_asset.storage_uri)
        assert (await env.docs.read_asset(doc.id, doc.original_asset.id))[1] == b"# V1"


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("recheck_unavailable", [False, True])
async def test_version_commit_ack_loss_recovers_original_submission_and_keeps_objects(tmp_path, live, monkeypatch, recheck_unavailable):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="lost-ack", name="提交恢复"))
        doc = await env.docs.upload_document(filename="first.md", content_base64=base64.b64encode(b"# V1").decode(), project_id=project.id)
        request = ApiDocVersionUploadRequest(filename="second.md", content_base64=base64.b64encode(b"# V2\r\n").decode(),
            project_id=project.id, request_id="stable", expected_parent_version_id=doc.current_version_id)
        store = env.docs._catalog_store
        commit, get_submission = store.commit_version, store.get_submission
        committed = False

        async def lose_ack(*args, **kwargs):
            nonlocal committed
            await commit(*args, **kwargs)
            committed = True
            raise RuntimeError("controlled version acknowledgement lost")

        async def maybe_unavailable(*args):
            if committed and recheck_unavailable:
                raise RuntimeError("controlled unavailable version lookup")
            return await get_submission(*args)

        with monkeypatch.context() as patch:
            patch.setattr(store, "commit_version", lose_ack)
            patch.setattr(store, "get_submission", maybe_unavailable)
            if recheck_unavailable:
                with pytest.raises(RuntimeError, match="尚无法确认"):
                    await env.docs.upload_version(doc.id, request)
            else:
                result = await env.docs.upload_version(doc.id, request)
                assert result.version.version == 2
        recovered = await env.docs.upload_version(doc.id, request)
        assert recovered == (await get_submission(doc.id, request.request_id)).result
        assert len(await env.docs.list_versions(doc.id)) == 2
        assert (await env.docs.read_asset(doc.id))[1] == b"# V2\r\n"


@pytest.mark.asyncio
async def test_json_restart_and_atomic_write_failure_preserve_entire_history(tmp_path, monkeypatch):
    async with environment(tmp_path) as env:
        env.docs._catalog_store = JsonApiDocStore(tmp_path / "catalog.json")
        project = await env.projects.create(ProjectCreateRequest(project_key="json-history", name="文件兼容历史"))
        doc = await env.docs.upload_document(filename="first.md", content_base64=base64.b64encode(b"# V1").decode(), project_id=project.id, request_id="first")
        before = (tmp_path / "catalog.json").read_bytes()
        request = ApiDocVersionUploadRequest(filename="second.md", content_base64=base64.b64encode(b"# V2").decode(),
            project_id=project.id, request_id="next", expected_parent_version_id=doc.current_version_id)

        def fail_replace(*args):
            raise OSError("controlled atomic replacement failure")

        with monkeypatch.context() as patch:
            patch.setattr("src.application.documents.api_doc_store.os.replace", fail_replace)
            with pytest.raises(OSError, match="replacement failure"):
                await env.docs.upload_version(doc.id, request)
        assert (tmp_path / "catalog.json").read_bytes() == before
        assert len(await env.docs.list_versions(doc.id)) == 1
        assert await env.docs._catalog_store.get_submission(doc.id, "next") is None
        assert len(await env.docs._catalog_store.list_assets(doc.id)) == 1
        result = await env.docs.upload_version(doc.id, request)
        restarted = JsonApiDocStore(tmp_path / "catalog.json")
        await restarted.initialize()
        assert await restarted.get_submission(doc.id, "next") == await env.docs._catalog_store.get_submission(doc.id, "next")
        env.docs._catalog_store = restarted
        assert (await env.docs.get_version(doc.id, doc.current_version_id)).snapshot == doc
        assert (await env.docs.read_asset(doc.id, doc.original_asset.id))[1] == b"# V1"
        assert (await env.docs.get_document(doc.id)).current_version_id == result.version.id


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_failure_building_preview_removes_uncommitted_original_and_derivative(tmp_path, live, monkeypatch):
    async with environment(tmp_path, live) as env:
        save = env.objects.store_uploaded_bytes
        staged = []

        async def track(**kwargs):
            result = await save(**kwargs)
            staged.append(result["uri"])
            return result

        def fail_preview(*args):
            raise RuntimeError("controlled preview construction failure")

        monkeypatch.setattr(env.objects, "store_uploaded_bytes", track)
        monkeypatch.setattr(env.docs, "_build_preview", fail_preview)
        with pytest.raises(RuntimeError, match="preview construction failure"):
            await env.docs.upload_document(filename="document.json",
                content_base64=base64.b64encode(b'{"openapi":"3.0.1","paths":{"/orders":{"get":{}}}}').decode())
        assert len(staged) == 2 and not await env.docs.list_documents()
        for uri in staged:
            with pytest.raises(Exception):
                await env.objects.read_object_uri(uri)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_history_identities_cannot_be_replaced_or_attached_to_another_document(tmp_path, live):
    async with environment(tmp_path, live) as env:
        first = await env.docs.upload_document(filename="first.md", content_base64=base64.b64encode(b"first").decode())
        second = await env.docs.upload_document(filename="second.md", content_base64=base64.b64encode(b"second").decode())
        stolen = second.model_copy(deep=True)
        stolen.original_asset = first.original_asset.model_copy(deep=True)
        with pytest.raises(ValueError, match="其他文档"):
            await env.docs._catalog_store.update(stolen)
        assert (await env.docs.read_asset(second.id))[1] == b"second"
        version = await env.docs.get_version(first.id, first.current_version_id)
        changed = version.model_copy(deep=True)
        changed.snapshot = second.model_copy(deep=True)
        changed.document_id = second.id
        changed.version = 2
        changed.parent_version_id = second.current_version_id
        changed.snapshot.current_version_id = version.id
        changed.snapshot.version_number = 2
        changed.snapshot.original_asset.id = str(uuid4())
        changed.snapshot.original_asset.version_id = version.id
        submission = ApiDocSubmission(document_id=second.id, request_id="collision", request_hash="a" * 64,
            result=ApiDocVersionResult(document=changed.snapshot, version=changed, created_new=True))
        with pytest.raises(ValueError):
            await env.docs._catalog_store.commit_version(changed.snapshot, changed, submission, expected_revision=second.catalog_revision)
        assert (await env.docs.get_document(second.id)).current_version_id == second.current_version_id
        assert await env.docs.get_version(first.id, first.current_version_id) == version


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_generation_and_planning_freeze_the_actual_read_version_and_asset(tmp_path, live, monkeypatch):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="sources", name="准确来源"))
        first = await env.docs.upload_document(filename="first.md", content_base64=base64.b64encode(b"# old\nold rule").decode(), project_id=project.id)
        read = env.docs.read_document_content
        new_record = None

        async def change_between_list_and_read(doc_id, **kwargs):
            nonlocal new_record
            if new_record is None:
                result = await env.docs.upload_version(first.id, ApiDocVersionUploadRequest(filename="new.md",
                    content_base64=base64.b64encode(b"# new\nnew rule").decode(), project_id=project.id,
                    expected_parent_version_id=first.current_version_id, request_id="source-change"))
                new_record = await env.docs.update_document(result.document.id, title="new title")
            return await read(doc_id, **kwargs)

        monkeypatch.setattr(env.docs, "read_document_content", change_between_list_and_read)
        provider = ProjectTestCaseContextProvider(api_docs_service=env.docs, knowledge_graph_service=None, session_store=None)
        context = await provider.collect(project=project, request=GenerateRequest(objective="验证文档来源", mode_key="api_testing",
            api_doc_ids=[first.id], include_history=False, include_knowledge_graph=False))
        document = context["api_documents"][0]
        source = context["source_refs"][0]
        assert document["content"] == "# new\nnew rule"
        assert document["title"] == "new title"
        assert source["version"] == new_record.current_version_id
        assert source["uri"] == new_record.storage_uri
        assert source["metadata"]["content_asset_id"] == new_record.original_asset.id
        assert source["metadata"]["content_sha256"] == hashlib.sha256(b"# new\nnew rule").hexdigest()
        evidence, _ = await env.sources.query(project.id, PlanningQuery(kind="document", source_id=first.id), 10)
        assert evidence[0].version == new_record.current_version_id
        assert evidence[0].payload["content_asset_id"] == new_record.original_asset.id
        assert evidence[0].payload["content_sha256"] == source["metadata"]["content_sha256"]
        await env.docs.update_document(first.id, project_url="https://changed.example.test")
        current = await env.docs.get_document(first.id)
        frozen, _ = await env.sources.query(project.id, PlanningQuery(kind="document", source_id=first.id), 10)
        assert frozen[0].version == new_record.current_version_id
        assert frozen[0].payload["content_asset_id"] == current.derived_assets[-1].id
        assert (await env.docs.get_version(first.id, first.current_version_id)).snapshot == first
        await env.docs.upload_version(first.id, ApiDocVersionUploadRequest(filename="third.md",
            content_base64=base64.b64encode(b"# V3").decode(), project_id=project.id,
            expected_parent_version_id=current.current_version_id, request_id="third"))
        assert b"changed.example.test" in (await env.docs.read_asset(first.id, current.derived_assets[-1].id))[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_project_rebinding_during_content_read_is_rejected_by_both_consumers(tmp_path, live, monkeypatch):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="source-owner", name="来源项目"))
        other = await env.projects.create(ProjectCreateRequest(project_key="source-other", name="其他项目"))
        doc = await env.docs.upload_document(filename="source.md", content_base64=base64.b64encode(b"# private project content").decode(), project_id=project.id)
        read = env.docs.read_document_content

        async def move_before_read(doc_id, **kwargs):
            await env.docs.update_document(doc_id, project_id=other.id)
            return await read(doc_id, **kwargs)

        monkeypatch.setattr(env.docs, "read_document_content", move_before_read)
        provider = ProjectTestCaseContextProvider(api_docs_service=env.docs, knowledge_graph_service=None, session_store=None)
        with pytest.raises(ValueError, match="project|项目"):
            await provider.collect(project=project, request=GenerateRequest(objective="验证项目隔离", mode_key="api_testing",
                api_doc_ids=[doc.id], include_history=False, include_knowledge_graph=False))
        await env.docs.update_document(doc.id, project_id=project.id)
        with pytest.raises(ValueError, match="project|项目"):
            await env.sources.query(project.id, PlanningQuery(kind="document", source_id=doc.id), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("known_original", [False, True])
async def test_explicit_legacy_initialization_preserves_id_and_does_not_invent_original(tmp_path, live, known_original):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="legacy-version", name="历史初始化"))
        original = tmp_path / "historical.md"
        original.write_bytes(b"# legacy\r\n### GET /orders\r\nold evidence\r\n")
        created = await env.docs.upload_document(**payload(original, "old-upload", project.id))
        # A genuinely pre-version catalog has no version tables/JSON history entry.
        legacy = created.model_copy(deep=True)
        legacy.current_version_id = None
        legacy.version_number = 0
        if known_original:
            legacy.original_asset.version_id = None
        else:
            legacy.original_asset = None
        if live:
            import json
            from src.infrastructure.postgres_runtime import postgres_connect
            store = env.docs._catalog_store
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in store.history_tables:
                    cursor.execute(f"DELETE FROM {table} WHERE document_id=%s", (created.id,))
                cursor.execute(f"UPDATE {store._table} SET record=%s::jsonb WHERE id=%s", (legacy.model_dump_json(), created.id))
        else:
            env.docs._catalog_store = JsonApiDocStore(tmp_path / "legacy.json")
            await env.docs._catalog_store.create(legacy)
        app = FastAPI()
        app.state.api_docs_service = env.docs
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            body = {"project_id": project.id, "request_id": "initialize", "expected_catalog_revision": legacy.catalog_revision}
            initialized = await client.post(f"/registry/api-docs/{created.id}/history-initialization", json=body)
            assert initialized.status_code == 200, initialized.text
            result = initialized.json()
            assert result["document"]["id"] == created.id
            assert result["version"]["version"] == 1 and result["created_new"]
            assert result["document"]["uploaded_at"] == legacy.model_dump(mode="json")["uploaded_at"]
            assert result["document"]["storage_uri"] == legacy.storage_uri
            history = f"/registry/api-docs/{created.id}/versions/{result['version']['id']}"
            content = (await client.get(history + "/content")).json()
            assert content["content"] == original.read_bytes().decode()
            downloaded = await client.get(history + "/original")
            if known_original:
                assert downloaded.status_code == 200 and downloaded.content == original.read_bytes()
            else:
                assert downloaded.status_code == 404
                assert result["version"]["snapshot"]["original_asset"] is None
                assert result["document"]["derived_assets"][0]["reason"] == "legacy_current_content"
            assert (await client.post(f"/registry/api-docs/{created.id}/history-initialization", json=body)).json() == result
            newer = await env.docs.upload_version(created.id, ApiDocVersionUploadRequest(filename="new.md",
                content_base64=base64.b64encode(b"# updated").decode(), project_id=project.id,
                request_id="new", expected_parent_version_id=result["version"]["id"]))
            assert newer.version.version == 2
            assert (await client.post(f"/registry/api-docs/{created.id}/history-initialization", json=body)).json() == result
            if not live:
                await env.docs._catalog_store.initialize()
                assert len(await env.docs.list_versions(created.id)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [LIVE])
async def test_json_catalog_migration_preserves_versions_submissions_and_historical_assets(tmp_path, live):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="catalog-history", name="目录迁移"))
        catalog = tmp_path / "history.json"
        postgres_store = env.docs._catalog_store
        env.docs._catalog_store = JsonApiDocStore(catalog)
        original = {"filename": "requirements.md", "content_base64": base64.b64encode(b"# V1\n### GET /orders\n").decode(),
            "request_id": "first", "project_id": project.id}
        first = await env.docs.upload_document(**original)
        derived = await env.docs.update_document(first.id, project_url="https://old.example.test")
        second_request = ApiDocVersionUploadRequest(filename="next.md", content_base64=base64.b64encode(b"# V2").decode(),
            project_id=project.id, request_id="next", expected_parent_version_id=first.current_version_id)
        second = await env.docs.upload_version(first.id, second_request)
        env.docs._catalog_store = postgres_store
        cleanup_catalog = JsonApiDocStore(catalog)
        await cleanup_catalog.initialize()
        owned_assets = await cleanup_catalog.list_assets(first.id)
        try:
            migrated = await postgres_store.migrate_legacy_catalog(catalog)
            assert migrated == {"read": 1, "inserted": 1, "skipped": 0, "invalid": 0}
            assert (await env.docs.get_document(first.id)).project_id is None
            assert (await env.docs.get_version(first.id, first.current_version_id)).snapshot == first
            assert len(await env.docs.list_versions(first.id)) == 2
            assert (await env.docs.read_asset(first.id, derived.derived_assets[0].id))[1].find(b"old.example.test") >= 0
            assert await postgres_store.get_submission(first.id, "next") is not None
            assert (await postgres_store.migrate_legacy_catalog(catalog))["skipped"] == 1
            import json
            import copy
            valid_catalog = json.loads(catalog.read_text(encoding="utf-8"))
            corruptions = [
                (["history", "versions"], first.current_version_id, None),
                (["history", "versions", second.version.id], "parent_version_id", str(uuid4())),
                (["history", "versions", first.current_version_id], "id", str(uuid4())),
                (["history", "assets", owned_assets[0].id], "id", str(uuid4())),
                (["history", "assets", owned_assets[0].id], "version_id", str(uuid4())),
                (["history", "assets"], owned_assets[0].id, None),
                (["history", "submissions", "first"], "request_id", "different"),
                (["history", "submissions", "first", "result", "version"], "created_at", "2020-01-01T00:00:00Z"),
                ([], "current_version_id", first.current_version_id),
            ]
            for path, key, replacement in corruptions:
                damaged = copy.deepcopy(valid_catalog)
                target = damaged[0]
                for token in path:
                    target = target[token]
                if replacement is None:
                    target.pop(key)
                else:
                    target[key] = replacement
                catalog.write_text(json.dumps(damaged), encoding="utf-8")
                assert (await postgres_store.migrate_legacy_catalog(catalog))["invalid"] == 1, path
                assert len(await env.docs.list_versions(first.id)) == 2
                assert (await env.docs.get_version(first.id, first.current_version_id)).snapshot == first
            await env.docs.update_document(first.id, project_id=project.id)
            assert await env.docs.upload_document(**original) == first
            assert await env.docs.upload_version(first.id, second_request) == second
        finally:
            for asset in owned_assets:
                await env.objects.delete_object_uri(asset.storage_uri)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [LIVE])
async def test_legacy_catalog_without_version_history_preserves_existing_assets(tmp_path, live):
    import json
    async with environment(tmp_path, live) as env:
        catalog = tmp_path / "legacy-assets.json"
        postgres_store = env.docs._catalog_store
        env.docs._catalog_store = JsonApiDocStore(catalog)
        content = b"# Historical original\r\n"
        created = await env.docs.upload_document(filename="legacy.md", content_base64=base64.b64encode(content).decode())
        legacy = created.model_dump(mode="json", exclude={"current_version_id", "version_number"})
        legacy["original_asset"].pop("version_id")
        catalog.write_text(json.dumps([legacy]), encoding="utf-8")
        env.docs._catalog_store = postgres_store
        try:
            assert await postgres_store.migrate_legacy_catalog(catalog) == {
                "read": 1, "inserted": 1, "skipped": 0, "invalid": 0}
            migrated = await env.docs.get_document(created.id)
            assert migrated.current_version_id is None and migrated.version_number == 0
            assert migrated.uploaded_at == created.uploaded_at
            assert migrated.original_asset.id == created.original_asset.id
            assert migrated.original_asset.version_id is None
            assert (await env.docs.read_asset(created.id, created.original_asset.id))[1] == content
        finally:
            await env.objects.delete_object_uri(created.original_asset.storage_uri)

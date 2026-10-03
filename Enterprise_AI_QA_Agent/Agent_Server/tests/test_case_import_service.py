from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from openpyxl import load_workbook

from src.api.routes.case_imports import router
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore, PostgresProjectStore
from src.application.security.upload_security_service import UploadSecurityService
from src.application.test_cases.case_service import TestCaseService as CaseService
from src.application.test_cases.case_store import InMemoryTestCaseStore, PostgresTestCaseStore
from src.application.test_cases.import_parser import build_import_template
from src.application.test_cases.import_service import CaseImportService
from src.application.test_cases.import_store import InMemoryCaseImportStore, PostgresCaseImportStore
from src.core.config import get_settings
from src.infrastructure.application_config_store import ApplicationConfigRecord, MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.case_import import CaseImportOptions, CaseImportUploadRequest
from src.schemas.project import ProjectCreateRequest


class _Configs:
    def __init__(self): self.records = {}
    def get(self, key, *, scope_key="global"): return deepcopy(self.records.get((scope_key, key)))
    def save(self, key, payload, *, scope_key="global", only_if_missing=False):
        old = self.get(key, scope_key=scope_key)
        if old and only_if_missing: return old
        record = ApplicationConfigRecord(key, scope_key, deepcopy(payload), old.revision + 1 if old else 1)
        self.records[scope_key, key] = record
        return record


class _Objects:
    def __init__(self): self.contents = {}
    async def store_uploaded_bytes(self, **kwargs):
        uri = f"memory://{kwargs.get('bucket_name', 'originals')}/{kwargs['object_prefix']}/{kwargs['filename']}"
        self.contents[uri] = kwargs["content"]
        return {"uri": uri}
    async def move_object_uri(self, uri, *, bucket_name, object_name):
        target = f"memory://{bucket_name}/{object_name}"
        self.contents[target] = self.contents.pop(uri)
        return {"uri": target}
    async def read_object_uri(self, uri): return {"content": self.contents[uri]}
    async def delete_object_uri(self, uri): self.contents.pop(uri, None)
    async def close(self): pass


@asynccontextmanager
async def _environment(tmp_path, live):
    settings = get_settings().model_copy(deep=True)
    settings.data_dir = str(tmp_path)
    suffix = uuid4().hex[:12]
    pg_fields = ["postgres_case_import_batch_table", "postgres_case_import_source_table", "postgres_test_case_version_table", "postgres_test_case_table", "postgres_project_table"]
    pg_tables = [f"up_i{index}_{suffix}" for index in range(len(pg_fields))]
    for field, table in zip(pg_fields, pg_tables): setattr(settings.database, field, table)
    settings.database.application_config_table = f"up_ic_{suffix}"
    if live:
        with postgres_connect(settings) as connection:
            with connection.cursor() as cursor:
                for table in pg_tables:
                    cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                    assert cursor.fetchone()["existing"] is None
        with mysql_raw_connection(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW TABLES LIKE %s", (settings.database.application_config_table,))
                assert cursor.fetchone() is None
        projects = ProjectService(store=PostgresProjectStore(settings))
        case_store = PostgresTestCaseStore(settings)
        configs = MySQLApplicationConfigStore(settings)
        configs.initialize()
        objects = ArtifactStorageService(settings)
        store = PostgresCaseImportStore(settings, case_store)
    else:
        projects = ProjectService(store=InMemoryProjectStore())
        case_store, configs, objects = InMemoryTestCaseStore(), _Configs(), _Objects()
        store = InMemoryCaseImportStore(case_store)
    await projects.initialize()
    cases = CaseService(store=case_store, project_service=projects)
    await cases.initialize()
    service = CaseImportService(settings=settings, store=store, config_store=configs, project_service=projects,
        case_service=cases, case_store=case_store, artifact_storage_service=objects,
        upload_security_service=UploadSecurityService(settings=settings, artifact_storage_service=objects))
    await service.initialize()
    app = FastAPI()
    app.state.case_import_service = service
    app.include_router(router, prefix="/api/v1")
    owned_sources = []
    original_save = store.save_source
    async def save_owned(source):
        owned_sources.append(source.storage_uri)
        return await original_save(source)
    store.save_source = save_owned
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            yield settings, projects, cases, case_store, configs, objects, store, service, client
    finally:
        for uri in set(owned_sources): await objects.delete_object_uri(uri)
        await objects.close()
        if live:
            with postgres_connect(settings) as connection:
                with connection.cursor() as cursor:
                    for table in pg_tables: cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
            with mysql_raw_connection(settings) as connection:
                with connection.cursor() as cursor: cursor.execute(f"DROP TABLE IF EXISTS `{settings.database.application_config_table}`")
                connection.commit()


def _xlsx(tmp_path, layout="rows", keys=("order-one",)):
    workbook = load_workbook(io.BytesIO(build_import_template(layout)))
    for key in keys:
        row = {"用例编号": key, "标题": "保持未登录验证拒绝", "前置条件": "未登录", "步骤": "读取受保护详情",
               "预期结果": "拒绝访问", "用例说明": "保持负向条件", "测试数据JSON": '{"id":0,"flag":false}'}
        workbook["用例"].append([row.get(cell.value) for cell in workbook["用例"][1]])
        if layout == "split":
            workbook["步骤"].append([key, 2, "核对拒绝响应", "验证通过"])
            workbook["步骤"].append([key, 1, "在未登录条件下请求", "拒绝访问"])
    path = tmp_path / "actual-cases.xlsx"
    workbook.save(path); workbook.close()
    return path


LIVE = pytest.param(True, marks=pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_IMPORT_TESTS") != "1", reason="requires isolated real MySQL/PostgreSQL/RustFS"))


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("layout", ["rows", "split"])
async def test_actual_excel_http_upload_preview_commit_repeat_and_restart(tmp_path, live, layout):
    async with _environment(tmp_path, live) as (settings, projects, cases, case_store, configs, objects, store, service, client):
        project = await projects.create(ProjectCreateRequest(project_key="import-one", name="Import one"))
        other = await projects.create(ProjectCreateRequest(project_key="import-two", name="Import two"))
        prefix = f"/projects/{project.id}/test-case-imports"
        original = _xlsx(tmp_path, layout).read_bytes()
        body = {"filename": "actual-cases.xlsx", "content_base64": base64.b64encode(original).decode()}
        upload = await client.post(prefix + "/sources", json=body)
        upload.raise_for_status()
        source = upload.json()
        assert source["security_report"]["decision"] == "allow"
        assert (await client.get(prefix + f"/sources/{source['id']}/original")).content == original
        assert (await client.post(prefix + "/sources", json=body)).json()["id"] == source["id"]
        options = {"mode_key": "api_testing", "layout": layout, "case_sheet": "用例", "step_sheet": "步骤" if layout == "split" else None}
        response = await client.post(prefix + f"/sources/{source['id']}/previews", json=options)
        response.raise_for_status()
        batch = response.json()["batch"]
        assert batch["status"] == "previewed" and batch["case_ids"] == []
        assert (await case_store.count_by_project(project.id)) == 0
        assert not batch["preview"]["issues"]
        assert (await client.post(prefix + f"/sources/{source['id']}/previews", json=options)).json()["batch"]["id"] == batch["id"]
        changed = (await service.get_settings(project.id)).payload
        changed.options = CaseImportOptions(mode_key="ui_automation", layout=layout)
        changed.policy.timeout_seconds = 25
        await service.save_settings(project.id, changed)
        assert (await service.get_settings(other.id)).scope_key == "global"
        assert (await service.get_batch(project.id, batch["id"])).batch.config_snapshot.payload.options is None
        results = await asyncio.gather(*[client.post(prefix + f"/batches/{batch['id']}/commit") for _ in range(2)])
        for result in results: result.raise_for_status()
        assert results[0].json()["batch"]["case_ids"] == results[1].json()["batch"]["case_ids"]
        assert await case_store.count_by_project(project.id) == 1
        case_id = results[0].json()["batch"]["case_ids"][0]
        test_case = await cases.get_case(case_id)
        version = (await cases.list_versions(case_id))[0]
        assert test_case.lifecycle_status == "draft" and test_case.active_version_id is None
        assert version.content_json["preconditions"] == ["未登录"]
        assert version.content_json["test_data"] == {"id": 0, "flag": False}
        assert version.source_refs[0].source_id == source["id"]
        assert version.source_refs[0].metadata["batch_id"] == batch["id"]
        wrong_prefix = f"/projects/{other.id}/test-case-imports"
        assert (await client.get(wrong_prefix + f"/sources/{source['id']}/original")).status_code == 404
        assert (await client.get(wrong_prefix + f"/batches/{batch['id']}")).status_code == 404
        assert await case_store.count_by_project(other.id) == 0
        if live:
            restarted = PostgresCaseImportStore(settings, PostgresTestCaseStore(settings))
            assert (await restarted.get_batch(project.id, batch["id"])).case_ids == [case_id]
            assert (await restarted.get_source(project.id, source["id"])).file_hash == source["file_hash"]
            assert MySQLApplicationConfigStore(settings).get("test_case.import_settings", scope_key=project.id).payload["options"]["mode_key"] == "ui_automation"
        await projects.archive(project.id)
        assert (await client.post(prefix + f"/batches/{batch['id']}/commit")).json()["batch"]["case_ids"] == [case_id]
        assert (await client.put(prefix + "/settings", json=changed.model_dump(mode="json"))).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_late_number_conflict_rolls_back_every_draft_and_keeps_preview(tmp_path, live):
    async with _environment(tmp_path, live) as (_, projects, cases, case_store, _, _, store, service, client):
        project = await projects.create(ProjectCreateRequest(project_key="conflict", name="Conflict"))
        file = _xlsx(tmp_path, keys=("new-before-conflict", "already-exists"))
        source = await service.upload(project.id, CaseImportUploadRequest(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode()))
        view = await service.preview(project.id, source.id, CaseImportOptions(mode_key="api_testing"))
        assert view.conflicting_case_keys == []
        original_prepare = cases.prepare_drafts
        old = None
        async def prepare_with_real_race(**kwargs):
            nonlocal old
            entries = await original_prepare(**kwargs)
            old = await cases.create_draft(project_id=project.id, payload=view.batch.preview.candidates[1].draft)
            return entries
        cases.prepare_drafts = prepare_with_real_race
        response = await client.post(f"/projects/{project.id}/test-case-imports/batches/{view.batch.id}/commit")
        assert response.status_code == 409
        assert await case_store.count_by_project(project.id) == 1
        assert await case_store.find_case_keys(project.id, ["new-before-conflict"]) == set()
        saved = await store.get_batch(project.id, view.batch.id)
        assert saved.status == "previewed" and saved.case_ids == []
        assert (await cases.get_version(old.version.id)).content_hash == old.version.content_hash
        assert (await service.get_batch(project.id, saved.id)).conflicting_case_keys == ["already-exists"]


@pytest.mark.asyncio
async def test_json_custom_content_parser_failure_and_original_integrity(tmp_path):
    async with _environment(tmp_path, False) as (_, projects, cases, _, _, objects, _, service, client):
        project = await projects.create(ProjectCreateRequest(project_key="json", name="JSON"))
        content = [{"case_key": "custom", "title": "Unknown JSON", "content_json": {"nested": [0, False, None, "中文😀"]}}]
        source = await service.upload(project.id, CaseImportUploadRequest(filename="custom.json", content_base64=base64.b64encode(json.dumps(content, ensure_ascii=False).encode()).decode()))
        view = await service.preview(project.id, source.id, CaseImportOptions(mode_key="api_testing", layout="json"))
        result = await service.commit(project.id, view.batch.id)
        version = (await cases.list_versions(result.batch.case_ids[0]))[0]
        assert version.content_json == content[0]["content_json"]
        file = _xlsx(tmp_path)
        source = await service.upload(project.id, CaseImportUploadRequest(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode()))
        bad = await service.preview(project.id, source.id, CaseImportOptions(mode_key="api_testing", columns={"title": "missing"}))
        assert bad.batch.preview.issues[0].code == "parser_failed"
        assert bad.batch.preview.diagnostics["exit_code"] == 2
        with pytest.raises(ValueError, match="预览包含错误"):
            await service.commit(project.id, bad.batch.id)
        objects.contents[source.storage_uri] = b"changed-owned-fixture"
        with pytest.raises(RuntimeError, match="哈希不一致"):
            await service.preview(project.id, source.id, CaseImportOptions(mode_key="api_testing"))


@pytest.mark.asyncio
async def test_source_catalog_failure_cleans_only_unreferenced_upload(tmp_path):
    async with _environment(tmp_path, False) as (_, projects, _, _, _, objects, store, service, _):
        project = await projects.create(ProjectCreateRequest(project_key="failure", name="Failure"))
        async def fail(source): raise RuntimeError("fixture catalog write failed")
        store.save_source = fail
        file = _xlsx(tmp_path)
        with pytest.raises(RuntimeError, match="目录保存失败"):
            await service.upload(project.id, CaseImportUploadRequest(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode()))
        assert objects.contents == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_lost_catalog_acknowledgement_preserves_committed_original(tmp_path, live):
    async with _environment(tmp_path, live) as (_, projects, _, _, _, objects, store, service, _):
        project = await projects.create(ProjectCreateRequest(project_key="ack", name="Ack"))
        save = store.save_source
        async def lose_ack(source):
            await save(source)
            raise RuntimeError("fixture commit acknowledgement lost")
        store.save_source = lose_ack
        file = _xlsx(tmp_path)
        source = await service.upload(project.id, CaseImportUploadRequest(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode()))
        assert await store.get_source(project.id, source.id) is not None
        assert (await objects.read_object_uri(source.storage_uri))["content"] == file.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_concurrent_uploads_resolve_one_source_and_clean_losing_object(tmp_path, live):
    async with _environment(tmp_path, live) as (_, projects, _, _, _, objects, store, service, _):
        project = await projects.create(ProjectCreateRequest(project_key="source-race", name="Source race"))
        save = store.save_source
        ready = asyncio.Event()
        reached = 0
        attempted = []
        async def race(source):
            nonlocal reached
            attempted.append(source.storage_uri)
            reached += 1
            if reached == 2: ready.set()
            await asyncio.wait_for(ready.wait(), timeout=10)
            return await save(source)
        store.save_source = race
        file = _xlsx(tmp_path)
        payload = CaseImportUploadRequest(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode())
        sources = await asyncio.gather(service.upload(project.id, payload), service.upload(project.id, payload))
        assert sources[0].id == sources[1].id and len(set(attempted)) == 2
        assert (await objects.read_object_uri(sources[0].storage_uri))["content"] == file.read_bytes()
        loser = next(uri for uri in attempted if uri != sources[0].storage_uri)
        if live:
            with pytest.raises(Exception): await objects.read_object_uri(loser)
        else:
            assert loser not in objects.contents


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_remembered_header_row_and_changed_size_policy_are_applied(tmp_path, live):
    async with _environment(tmp_path, live) as (_, projects, _, _, _, _, _, service, _):
        project = await projects.create(ProjectCreateRequest(project_key="header", name="Header"))
        config = (await service.get_settings(project.id)).payload
        config.options = CaseImportOptions(mode_key="api_testing", header_row=2)
        await service.save_settings(project.id, config)
        file = _xlsx(tmp_path)
        workbook = load_workbook(file)
        workbook["用例"].insert_rows(1)
        workbook["用例"]["A1"] = "文件说明"
        workbook.save(file); workbook.close()
        source = await service.upload(project.id, CaseImportUploadRequest(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode()))
        assert source.sheets[0].suggested_columns["title"] == "标题"
        valid = await service.preview(project.id, source.id, config.options)
        assert len(valid.batch.preview.candidates) == 1
        config.policy.limits.max_bytes = 1
        await service.save_settings(project.id, config)
        failed = await service.preview(project.id, source.id, config.options)
        assert failed.batch.preview.issues[0].code == "parser_failed"
        assert failed.batch.preview.diagnostics["exit_code"] is None
        assert failed.batch.id != valid.batch.id
        with pytest.raises(ValueError, match="预览包含错误"):
            await service.commit(project.id, failed.batch.id)

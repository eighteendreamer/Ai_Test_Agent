from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.case_management import router
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.documents.api_doc_store import InMemoryApiDocStore, PostgresApiDocStore
from src.application.documents.api_docs_service import ApiDocsService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore, PostgresProjectStore
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.application.test_cases.case_service import TestCaseService as CaseService
from src.application.test_cases.case_store import InMemoryTestCaseStore, PostgresTestCaseStore
from src.application.test_cases.generation_pipeline import ModelTestCaseGenerator, ProjectTestCaseContextProvider
from src.application.test_cases.template_service import TestCaseTemplateService as TemplateService
from src.core.config import get_settings
from src.infrastructure.application_config_store import ApplicationConfigRecord, MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.schemas.case_management import TestCaseDraftCreateRequest as DraftRequest, TestCaseGenerateRequest as GenerateRequest, TestCaseVersionRecord as VersionRecord
from src.schemas.project import ProjectCreateRequest


class _Configs:
    def __init__(self):
        self.records = {}

    def get(self, key, *, scope_key="global"):
        return deepcopy(self.records.get((scope_key, key)))

    def save(self, key, payload, *, scope_key="global", only_if_missing=False):
        previous = self.get(key, scope_key=scope_key)
        if previous and only_if_missing:
            return previous
        record = ApplicationConfigRecord(key, scope_key, deepcopy(payload), previous.revision + 1 if previous else 1)
        self.records[scope_key, key] = record
        return record


class _Objects:
    def __init__(self):
        self.contents = {}

    async def store_uploaded_bytes(self, **kwargs):
        uri = f"memory://{kwargs['object_prefix']}/{kwargs['filename']}"
        self.contents[uri] = kwargs["content"]
        return {"uri": uri, "content_type": kwargs["content_type"]}

    async def read_object_uri(self, uri):
        return {"content": self.contents[uri]}

    async def delete_object_uri(self, uri):
        self.contents.pop(uri, None)


@asynccontextmanager
async def _stores(tmp_path, live):
    if not live:
        yield SimpleNamespace(data_dir=str(tmp_path)), InMemoryProjectStore(), InMemoryTestCaseStore(), _Configs(), InMemoryApiDocStore(), _Objects()
        return
    settings = get_settings().model_copy(deep=True)
    settings.data_dir = str(tmp_path)
    suffix = uuid4().hex[:12]
    # Only fresh UUID-named tables are owned by this acceptance test.
    pg_tables = [f"up_jv_{suffix}", f"up_jc_{suffix}", f"up_jd_{suffix}", f"up_jp_{suffix}"]
    settings.database.postgres_test_case_version_table, settings.database.postgres_test_case_table, settings.database.postgres_api_doc_table, settings.database.postgres_project_table = pg_tables
    pg_tables = [*PostgresApiDocStore(settings).history_tables, *pg_tables]
    mysql_table = f"up_jt_{suffix}"
    settings.database.application_config_table = mysql_table
    with postgres_connect(settings) as connection:
        with connection.cursor() as cursor:
            for table in pg_tables:
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
    with mysql_raw_connection(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE %s", (mysql_table,))
            assert cursor.fetchone() is None
    objects = ArtifactStorageService(settings)
    configs = MySQLApplicationConfigStore(settings)
    try:
        await asyncio.to_thread(configs.initialize)
        yield settings, PostgresProjectStore(settings), PostgresTestCaseStore(settings), configs, PostgresApiDocStore(settings), objects
    finally:
        await objects.close()
        with postgres_connect(settings) as connection:
            with connection.cursor() as cursor:
                for table in pg_tables:
                    cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
        with mysql_raw_connection(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS `{mysql_table}`")
            connection.commit()


def _draft(**changes):
    return {"case_key": "custom-order", "title": "自定义订单用例", "case_type": "json", "mode_key": "api_testing",
            "content_json": {"目标": "核对订单", "业务扩展": {"未知字段": [0, False, None, "中文😀"]}, "steps": [{"操作": "查询", "证据要求": {"自定义": True}}]},
            "execution_notes": "按照完整 JSON 内容理解并执行。", "source_refs": [{"source_type": "manual", "source_id": "fixture-source"}],
            "model_key": "manual", "prompt_version": "manual-json-v1", "skill_versions": {"manual-authoring": "v1"}, **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.getenv("RUN_LIVE_CASE_JSON_TESTS") != "1", reason="requires real MySQL/PostgreSQL isolated catalogs",
))])
async def test_custom_json_http_versions_template_snapshot_and_project_isolation(tmp_path, live):
    async with _stores(tmp_path, live) as (settings, project_store, case_store, configs, _, _):
        projects = ProjectService(store=project_store)
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="json-one", name="JSON one"))
        other = await projects.create(ProjectCreateRequest(project_key="json-two", name="JSON two"))
        templates = TemplateService(store=configs, project_service=projects)
        await templates.initialize()
        cases = CaseService(store=case_store, project_service=projects)
        await cases.initialize()
        app = FastAPI()
        app.state.test_case_service = cases
        app.state.test_case_template_service = templates
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            default = await client.get(f"/projects/{project.id}/test-case-generation-template")
            default.raise_for_status()
            assert default.json()["is_default"] is True
            custom = {**default.json()["payload"], "writing_guidance": "保留自定义业务扩展。", "example_json": {"任意结构": {"deep": [1, 2]}}}
            saved = await client.put(f"/projects/{project.id}/test-case-generation-template", json=custom)
            saved.raise_for_status()
            assert saved.json()["scope_key"] == project.id
            assert (await templates.get(other.id)).is_default is True
            body = _draft(generation_metadata={"template": saved.json()})
            file = tmp_path / "case.json"
            file.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            response = await client.post(f"/projects/{project.id}/test-cases", json=json.loads(file.read_text(encoding="utf-8")))
            response.raise_for_status()
            bundle = response.json()
            first = bundle["version"]
            assert first["content_json"] == body["content_json"]
            assert first["content_format"] == "json-v1"
            assert bundle["case"]["lifecycle_status"] == "draft"
            case_id = bundle["case"]["id"]
            await client.put(f"/projects/{project.id}/test-case-generation-template", json={**custom, "writing_guidance": "下一次生成使用新的指导。"})
            new_body = {key: value for key, value in body.items() if key not in {"case_key", "title", "case_type", "mode_key", "priority"}}
            new_body["content_json"] = {**body["content_json"], "新增": {"array": [False, 0]}}
            response = await client.post(f"/test-cases/{case_id}/versions", json=new_body)
            response.raise_for_status()
            assert response.json()["version"] == 2
            assert response.json()["content_hash"] != first["content_hash"]
            versions = (await client.get(f"/test-cases/{case_id}/versions")).json()
            original = next(item for item in versions if item["id"] == first["id"])
            assert original == first
            assert original["generation_metadata"]["template"]["revision"] == 1
            assert (await templates.get(project.id)).revision == 2
            assert (await cases.list_cases(project_id=other.id, status=None, mode_key=None, priority=None, query=None, limit=50, offset=0)).items == []
            await projects.archive(project.id)
            denied = await client.put(f"/projects/{project.id}/test-case-generation-template", json=custom)
            assert denied.status_code == 409
        if live:
            restarted = CaseService(store=PostgresTestCaseStore(settings), project_service=projects)
            assert (await restarted.get_version(first["id"])).content_json == body["content_json"]
            assert (await TemplateService(store=MySQLApplicationConfigStore(settings), project_service=projects).get(project.id)).revision == 2


def test_json_cases_do_not_require_legacy_mode_parameter_fields():
    request = DraftRequest.model_validate(_draft())
    assert request.steps == [] and request.assertions == []
    assert request.business_content()["业务扩展"]["未知字段"] == [0, False, None, "中文😀"]
    with pytest.raises(ValueError, match="Legacy test cases require"):
        DraftRequest.model_validate(_draft(content_json=None))


def _legacy_draft():
    return DraftRequest(case_key="old-order", title="Legacy order", mode_key="api_testing", case_type="happy_path",
                        steps=[{"order": 1, "action": "GET /orders/42"}], assertions=[{"kind": "status_code", "expected": 200}],
                        source_refs=[{"source_type": "manual", "source_id": "legacy-fixture"}], model_key="manual",
                        prompt_version="legacy-v1", skill_versions={"manual-authoring": "v1"})


def test_historical_record_keeps_legacy_read_contract_without_new_order_validation():
    # The pre-upgrade record schema allowed imported numbering that new draft
    # requests rejected. Do not impose a new write rule when reading history.
    payload = _legacy_draft().model_dump(mode="json", exclude={"case_key", "title", "mode_key", "case_type", "priority", "content_json", "execution_notes", "generation_metadata"})
    payload["steps"][0]["order"] = 2
    record = VersionRecord.model_validate({**payload, "id": "historical", "case_id": "case", "version": 1,
                                          "content_hash": "a" * 64, "created_at": "2026-10-02T00:00:00Z"})
    assert record.business_content()["steps"][0]["order"] == 2
    assert record.content_hash == "a" * 64
    with pytest.raises(ValueError, match="unique and contiguous"):
        DraftRequest.model_validate({**_legacy_draft().model_dump(), "steps": [{"order": 2, "action": "GET /orders/42"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.getenv("RUN_LIVE_CASE_JSON_TESTS") != "1", reason="requires real PostgreSQL isolated catalogs",
))])
async def test_legacy_hash_and_persisted_record_are_not_rewritten(tmp_path, live):
    async with _stores(tmp_path, live) as (settings, project_store, case_store, _, _, _):
        projects = ProjectService(store=project_store)
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="legacy", name="Legacy"))
        cases = CaseService(store=case_store, project_service=projects)
        await cases.initialize()
        bundle = await cases.create_draft(project_id=project.id, payload=_legacy_draft())
        # Captured by executing _build_version from c4005ba, before JSON support.
        assert bundle.version.content_hash == "cee6eb893fc8431fd27c610f389c3934428ab1c60a270ca89175b74ded74e13e"
        if live:
            original = bundle.version.model_dump(mode="json", exclude={"content_json", "content_format", "execution_notes", "generation_metadata"})
            table = settings.database.postgres_test_case_version_table
            with postgres_connect(settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f'UPDATE "{table}" SET record = %s::jsonb WHERE id = %s', (json.dumps(original), bundle.version.id))
            loaded = await PostgresTestCaseStore(settings).get_version(bundle.version.id)
            assert loaded.content_json is None and loaded.content_format == "legacy-v1"
            assert loaded.content_hash == original["content_hash"]
            assert loaded.business_content()["steps"][0]["action"] == "GET /orders/42"
            with postgres_connect(settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT record, content_hash FROM "{table}" WHERE id = %s', (bundle.version.id,))
                    stored = cursor.fetchone()
                    assert stored["record"] == original
                    assert stored["content_hash"] == original["content_hash"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_GENERATION_TESTS") != "1", reason="requires current configured model, actual MySQL/PostgreSQL/RustFS")
async def test_actual_document_model_skill_template_generation_is_frozen_in_postgres(tmp_path):
    async with _stores(tmp_path, True) as (settings, project_store, case_store, configs, doc_store, objects):
        projects = ProjectService(store=project_store)
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="actual-generation", name="订单需求文档"))
        docs = ApiDocsService(settings=settings, artifact_storage_service=objects, catalog_store=doc_store, project_service=projects)
        await docs.initialize()
        file = tmp_path / "requirements.md"
        file.write_text("# 订单详情需求\n用户可按订单编号查看详情。已存在订单返回订单编号和金额；不存在的订单返回 404。不得把不存在条件改为创建订单。\n", encoding="utf-8")
        document = await docs.upload_document(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode(), project_id=project.id)
        try:
            templates = TemplateService(store=configs, project_service=projects)
            await templates.initialize()
            template = await templates.get(project.id)
            runtime = ModelRuntimeService(model_registry=ModelRegistry(MySQLModelConfigStore(settings)), settings=settings)
            generator = ModelTestCaseGenerator(model_runtime_service=runtime, skill_runtime_service=SkillRuntimeService(SkillRegistry()))
            context = ProjectTestCaseContextProvider(api_docs_service=docs, knowledge_graph_service=None, session_store=None)
            cases = CaseService(store=case_store, project_service=projects, context_provider=context, generator=generator, template_service=templates)
            await cases.initialize()
            generated = await cases.generate(project.id, GenerateRequest(objective="根据已上传文档生成两条订单详情用例：存在与不存在。保持不存在的负向条件，只生成草稿，不执行。", mode_key="api_testing", api_doc_ids=[document.id], include_history=False, include_knowledge_graph=False, max_cases=2))
            assert len(generated.items) == 2
            for item in generated.items:
                assert item.case.lifecycle_status == "draft"
                assert item.version.content_json
                assert item.version.execution_notes
                assert item.version.prompt_version == "test-case-generation-json-v3"
                assert item.version.source_refs[0].source_id == document.id
                assert item.version.generation_metadata["template"]["content_hash"] == template.content_hash
                loaded = await PostgresTestCaseStore(settings).get_version(item.version.id)
                assert loaded.content_json == item.version.content_json
            old_hash = generated.items[0].version.content_hash
            await templates.save(project.id, template.payload.model_copy(update={"writing_guidance": "后续生成采用新的写作指导。"}))
            assert (await cases.get_version(generated.items[0].version.id)).content_hash == old_hash
        finally:
            await docs.delete_document(document.id)

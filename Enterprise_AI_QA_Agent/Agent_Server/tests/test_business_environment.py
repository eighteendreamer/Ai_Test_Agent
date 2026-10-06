"""Actual target configurations/captures, document files and project boundaries."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from psycopg.types.json import Jsonb

from src.api.routes.business_environments import router
from src.application.projects.business_environment_service import BusinessEnvironmentService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.runtime.postgres_business_environment_store import PostgresBusinessEnvironmentStore, environment_digest
from src.schemas.business_environment import (
    TARGET_CONFIG_KEY, BusinessEnvironmentCreateRequest, BusinessEnvironmentCaptureRequest, BusinessTargetConfiguration,
)
from src.schemas.project import ProjectCreateRequest
from src.schemas.document_parsing import DocumentParseTaskRequest
from test_document_parse_tasks import parsing_environment
from test_document_differences import write_pair


LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_BUSINESS_ENVIRONMENTS") != "1",
    reason="actual isolated PostgreSQL/MySQL/RustFS and document parsing processes")


def save_evidence(name, **data):
    directory = os.getenv("QA_ENVIRONMENT_EVIDENCE_DIR")
    if directory:
        target = Path(directory) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")


@asynccontextmanager
async def actual_environment(tmp_path):
    async with parsing_environment(tmp_path) as (env, parsing, parse_store, _, _):
        store = PostgresBusinessEnvironmentStore(env.settings)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in store.tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        service = BusinessEnvironmentService(store=store, configs=env.configs, projects=env.projects)
        await service.initialize()
        app = FastAPI()
        app.state.business_environment_service = service
        app.include_router(router, prefix="/api/v1")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://actual-stores/api/v1") as client:
                project = await env.projects.create(ProjectCreateRequest(project_key="target", name="目标依据验收"))
                other = await env.projects.create(ProjectCreateRequest(project_key="other", name="另一项目"))
                yield env, parsing, parse_store, store, service, client, project, other
        finally:
            # Only the two UUID-derived tables proved absent above are owned here.
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in store.tables:
                    cur.execute(f'DROP TABLE "{table}"')


async def configured(client, project, *, key="stage", request_id="register-stage"):
    path = f"/projects/{project.id}/business-environments"
    response = await client.post(path, json={"request_id": request_id, "environment_key": key})
    assert response.status_code == 201, response.text
    registered = response.json()
    selected = path + "/" + registered["id"]
    response = await client.put(selected + "/configuration", json={"display_name": "测试环境",
        "base_url": "http://127.0.0.1:9876/application", "description": "非敏感目标设置"})
    assert response.status_code == 200, response.text
    return path, selected, registered, response.json()


def capture_request(config, request_id="fixed-target"):
    return {"request_id": request_id, "expected_config_revision": config["revision"],
        "expected_config_sha256": config["sha256"]}


@pytest.mark.parametrize("address", ["example.com", "file:///x", "http://user:fixture@example.com",
    "https://example.com?session=fixture", "https://example.com#fragment", " http://example.com",
    "http://example.com:99999", "http://example.com:0", "http://", "https://exam ple.com", "http://example.com\\other"])
def test_unambiguous_nonsecret_target_addresses(address):
    with pytest.raises(ValidationError):
        BusinessTargetConfiguration(display_name="目标", base_url=address)


@LIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_configuration_capture_keeps_original_files_and_survives_change_archive(tmp_path, monkeypatch, extension):
    async with actual_environment(tmp_path) as values:
        env, parsing, parse_store, store, service, client, project, other = values
        paths = write_pair(tmp_path, extension)
        document = await env.docs.upload_document(filename=paths[0].name, project_id=project.id,
            request_id="real-file", content_base64=base64.b64encode(paths[0].read_bytes()).decode())
        task = await parsing.submit(project.id, document.id, DocumentParseTaskRequest(
            creation_key="actual-parser", version_id=document.current_version_id))
        done = await parsing.execute(await parse_store.claim(project.id, task.id, worker_id="actual-env-parser"))
        assert done.status == "completed"
        parsed = await parsing.read_snapshot(project.id, document.id, done.snapshot_id)
        path, selected, registered, config = await configured(client, project)
        assert config["scope_key"] == registered["id"] and config["revision"] == 1
        request = capture_request(config)
        response = await client.post(selected + "/snapshots", json=request)
        assert response.status_code == 201, response.text
        frozen = response.json()
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT scope_key,payload_json,revision FROM `{env.settings.database.application_config_table}` WHERE config_key=%s",
                (TARGET_CONFIG_KEY,))
            mysql_rows = [dict(row) for row in cur.fetchall()]
            assert len(mysql_rows) == 1 and mysql_rows[0]["scope_key"] == registered["id"]
            assert json.loads(mysql_rows[0]["payload_json"]) == config["payload"]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._snapshots}")
            assert cur.fetchone()["record"] == frozen
        changed = await client.put(selected + "/configuration", json={"display_name": "已改址环境",
            "base_url": "https://example.com/another", "description": "后续配置"})
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert (await client.post(selected + "/snapshots", json=capture_request(config, "stale-new-request"))).status_code == 409
        assert (await client.post(selected + "/snapshots", json=capture_request(changed.json(), request["request_id"]))).status_code == 409
        assert (await client.get(selected.replace(project.id, other.id))).status_code == 404
        assert (await client.post(path, json={"request_id": "new-request-same-key", "environment_key": "stage"})).status_code == 409
        same_name = await client.post(path.replace(project.id, other.id), json=registered["request"])
        assert same_name.status_code == 201 and same_name.json()["id"] != registered["id"]
        new_capture = await client.post(selected + "/snapshots", json=capture_request(changed.json(), "target-v2"))
        assert new_capture.status_code == 201 and new_capture.json()["id"] != frozen["id"]
        history = await client.get(selected + "/snapshots", params={"limit": 1})
        assert history.status_code == 200 and history.json()["items"] == [frozen] and history.json()["has_more"]
        assert (await client.get(selected + "/snapshots", params={"limit": 1, "offset": 1})).json()["items"] == [new_capture.json()]
        assert (await client.get(path, params={"status": "active"})).json()["items"] == [registered]
        archived = await client.post(selected + "/archive")
        assert archived.status_code == 200
        assert (await client.put(selected + "/configuration", json=config["payload"])).status_code == 409
        await env.projects.archive(project.id)
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM `{env.settings.database.application_config_table}` WHERE config_key=%s", (TARGET_CONFIG_KEY,))
            conn.commit()
        def forbidden(*args, **kwargs):
            raise AssertionError("immutable target recovery must not read/save current configuration")
        monkeypatch.setattr(env.configs, "get", forbidden)
        monkeypatch.setattr(env.configs, "save", forbidden)
        assert (await client.get(selected + f"/snapshots/{frozen['id']}")).json() == frozen
        assert (await client.get(selected + "/snapshots/by-request", params={"request_id": request["request_id"]})).json() == frozen
        assert (await client.post(selected + "/snapshots", json=request)).json() == frozen
        assert (await client.post(path, json=registered["request"])).json()["id"] == registered["id"]
        assert (await client.post(selected + "/snapshots", json=capture_request(config, "new-after-archive"))).status_code == 409
        assert (await client.get(selected + "/snapshots/by-request", params={"request_id": "absent"})).status_code == 404
        original = (await env.objects.read_object_uri(document.original_asset.storage_uri))["content"]
        assert original == paths[0].read_bytes()
        assert parsed[1].source_sha256 == hashlib.sha256(original).hexdigest()
        assert frozen["deployment_status"] == "unknown" and env.service._models.requests == []
        for source in paths:
            save_evidence(f"{extension}/{source.name}.json", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                source_size_bytes=source.stat().st_size)
            directory = os.getenv("QA_ENVIRONMENT_EVIDENCE_DIR")
            if directory: (Path(directory)/extension/source.name).write_bytes(source.read_bytes())
        save_evidence(f"{extension}/verified.json", registration=registered, configuration=config,
            snapshot=frozen, later_snapshot=new_capture.json(), mysql=mysql_rows,
            document=document.model_dump(mode="json"), parsed_snapshot=parsed[0].model_dump(mode="json"),
            original_sha256=hashlib.sha256(original).hexdigest(), new_model_http_calls=0,
            configuration_current_row_removed=True, recovery_uses_current_config=False,
            environment_applied_to_plan_or_run=False, deployment_status="unknown")


@LIVE
@pytest.mark.asyncio
async def test_actual_lost_commit_replies_restart_and_four_way_capture(tmp_path, monkeypatch):
    async with actual_environment(tmp_path) as values:
        env, _, _, store, service, client, project, other = values
        path = f"/projects/{project.id}/business-environments"
        original_register = store.register
        async def lost_register(*args):
            await original_register(*args)
            raise RuntimeError("controlled actual registration commit reply lost")
        monkeypatch.setattr(store, "register", lost_register)
        registration_request = {"request_id": "lost-register", "environment_key": "recoverable"}
        response = await client.post(path, json=registration_request)
        assert response.status_code == 503
        registered = (await client.get(path + "/by-request", params={"request_id": "lost-register"})).json()
        monkeypatch.setattr(store, "register", original_register)
        assert (await client.post(path, json=registration_request)).json() == registered
        selected = path + "/" + registered["id"]
        config = (await client.put(selected + "/configuration", json={"display_name": "恢复测试", "base_url": "https://example.com"})).json()
        original_capture = store.capture
        async def lost_capture(value):
            await original_capture(value)
            raise RuntimeError("controlled actual snapshot commit reply lost")
        monkeypatch.setattr(store, "capture", lost_capture)
        request = capture_request(config, "lost-capture")
        assert (await client.post(selected + "/snapshots", json=request)).status_code == 503
        fresh_store = PostgresBusinessEnvironmentStore(env.settings)
        fresh = BusinessEnvironmentService(store=fresh_store, configs=MySQLApplicationConfigStore(env.settings), projects=env.projects)
        original = await fresh.get_capture(project.id, registered["id"], request["request_id"])
        assert (await fresh.capture(project.id, registered["id"], BusinessEnvironmentCaptureRequest(**request))) == original
        concurrent_request = BusinessEnvironmentCaptureRequest(**capture_request(config, "four-way"))
        captures = await asyncio.gather(*[fresh.capture(project.id, registered["id"], concurrent_request) for _ in range(4)])
        assert all(item == captures[0] for item in captures)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._snapshots} ORDER BY captured_at,id")
            rows = [dict(row) for row in cur.fetchall()]
            assert len(rows) == 2
        assert (await client.get(selected.replace(project.id, other.id) + "/snapshots/by-request", params={"request_id": request["request_id"]})).status_code == 404
        save_evidence("lost-replies.json", registration=registered, snapshot=original.model_dump(mode="json"),
            concurrent_snapshot=captures[0].model_dump(mode="json"), pg_snapshots=rows,
            actual_four_way_writes=True, new_model_http_calls=0)


@LIVE
@pytest.mark.asyncio
async def test_actual_capture_archive_race_and_selected_configuration_read(tmp_path, monkeypatch):
    async with actual_environment(tmp_path) as values:
        env, _, _, store, service, client, project, _ = values
        _, selected, registered, config = await configured(client, project)
        original = store.capture
        async def config_changed_after_read(value):
            await service.save_configuration(project.id, registered["id"], BusinessTargetConfiguration(
                display_name="之后变化", base_url="https://example.com/after-read"))
            return await original(value)
        monkeypatch.setattr(store, "capture", config_changed_after_read)
        first = await client.post(selected + "/snapshots", json=capture_request(config))
        assert first.status_code == 201 and first.json()["configuration"] == config
        latest = await service.read_configuration(project.id, registered["id"])
        assert latest.revision == 2
        async def archived_after_read(value):
            await store.archive(project.id, registered["id"])
            return await original(value)
        monkeypatch.setattr(store, "capture", archived_after_read)
        denied = await client.post(selected + "/snapshots", json=capture_request(latest.model_dump(mode="json"), "archive-race"))
        assert denied.status_code == 409
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._snapshots}")
            rows = [dict(row) for row in cur.fetchall()]
            assert len(rows) == 1 and rows[0]["record"] == first.json()
        save_evidence("config-and-archive-races.json", selected_read=first.json(), current_after_read=latest.model_dump(mode="json"),
            archive_race_status=denied.status_code, pg_snapshots=rows, cross_database_latest_claim=False)


@LIVE
@pytest.mark.asyncio
async def test_actual_record_corruption_configuration_loss_and_request_injection(tmp_path):
    async with actual_environment(tmp_path) as values:
        env, _, _, store, service, client, project, _ = values
        path, selected, registered, config = await configured(client, project)
        assert (await client.post(path, json={**registered["request"], "deployment_status": "effective"})).status_code == 422
        assert (await client.post(path, json={**registered["request"], "environment_key": "another"})).status_code == 409
        assert (await client.put(selected + "/configuration", json={**config["payload"], "target_release": "declared"})).status_code == 422
        assert (await client.post(selected + "/snapshots", json={**capture_request(config), "deployment_status": "effective"})).status_code == 422
        frozen = (await client.post(selected + "/snapshots", json=capture_request(config))).json()
        altered = deepcopy(frozen)
        altered["configuration"]["payload"]["base_url"] = "https://example.com/corrupt"
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._snapshots} SET record=%s WHERE id=%s", (Jsonb(altered), frozen["id"]))
        assert (await client.get(selected + f"/snapshots/{frozen['id']}")).status_code == 409
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._snapshots} SET record=%s WHERE id=%s", (Jsonb(frozen), frozen["id"]))
        altered = deepcopy(frozen)
        altered["environment_key"] = "wrong-registration"
        altered["registration_request_sha256"] = "f"*64
        altered["sha256"] = environment_digest({key: value for key, value in altered.items() if key != "sha256"})
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._snapshots} SET record=%s WHERE id=%s", (Jsonb(altered), frozen["id"]))
        assert (await client.get(selected + f"/snapshots/{frozen['id']}")).status_code == 409
        assert (await client.get(selected + "/snapshots")).status_code == 409
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._snapshots} SET record=%s WHERE id=%s", (Jsonb(frozen), frozen["id"]))
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE `{env.settings.database.application_config_table}` SET payload_json=%s WHERE config_key=%s AND scope_key=%s",
                (json.dumps({**config["payload"], "deployment_status": "effective"}), TARGET_CONFIG_KEY, registered["id"]))
            conn.commit()
        assert (await client.get(selected + "/configuration")).status_code == 409
        assert (await client.post(selected + "/snapshots", json=capture_request(config, "malformed-current"))).status_code == 409
        assert (await client.get(selected + f"/snapshots/{frozen['id']}")).json() == frozen
        save_evidence("corruption.json", original_snapshot=frozen, altered_configuration_rejected=True,
            injected_release_rejected=True, changed_registration_link_with_recomputed_hash_rejected=True,
            current_malformed_configuration_does_not_replace_history=True)


@LIVE
@pytest.mark.asyncio
async def test_actual_current_mysql_configuration_lost_reply_is_readable_without_fake_idempotence(tmp_path, monkeypatch):
    async with actual_environment(tmp_path) as values:
        env, _, _, store, service, client, project, _ = values
        _, selected, registered, config = await configured(client, project)
        frozen = (await client.post(selected + "/snapshots", json=capture_request(config))).json()
        save = env.configs.save
        def lost_reply(*args, **kwargs):
            save(*args, **kwargs)
            raise RuntimeError("controlled actual MySQL config commit reply lost")
        monkeypatch.setattr(env.configs, "save", lost_reply)
        changed = {"display_name": "实际已保存", "base_url": "https://example.com/new-config"}
        unknown = await client.put(selected + "/configuration", json=changed)
        assert unknown.status_code == 503 and "GET" in unknown.json()["detail"]
        current = (await client.get(selected + "/configuration")).json()
        assert current["revision"] == 2 and current["payload"]["base_url"] == changed["base_url"]
        monkeypatch.setattr(env.configs, "save", save)
        next_config = (await client.put(selected + "/configuration", json=changed)).json()
        assert next_config["revision"] == 3
        assert (await client.get(selected + f"/snapshots/{frozen['id']}")).json() == frozen
        save_evidence("mysql-lost-reply.json", original_configuration=config, original_capture=frozen,
            actual_committed_unknown_configuration=current, new_save_configuration=next_config,
            config_save_claimed_idempotent=False)


@LIVE
@pytest.mark.asyncio
async def test_actual_valid_maximum_project_table_name_still_initializes_environment_store(tmp_path):
    async with actual_environment(tmp_path) as values:
        env = values[0]
        settings = env.settings.model_copy(deep=True)
        project_table = ("up_env_boundary_" + uuid4().hex).ljust(63, "x")
        settings.database.postgres_project_table = project_table
        store = PostgresBusinessEnvironmentStore(settings)
        owned = [*store.tables, project_table]
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            for table in owned:
                assert len(table) <= 63
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        try:
            projects = ProjectService(store=PostgresProjectStore(settings))
            await projects.initialize()
            service = BusinessEnvironmentService(store=store, configs=env.configs, projects=projects)
            await service.initialize()
            project = await projects.create(ProjectCreateRequest(project_key="long-table", name="现有63字节项目表"))
            registered = await service.register(project.id, BusinessEnvironmentCreateRequest(request_id="register", environment_key="stage"))
            config = await service.save_configuration(project.id, registered.id, BusinessTargetConfiguration(display_name="实际目标", base_url="https://example.com"))
            frozen = await service.capture(project.id, registered.id, BusinessEnvironmentCaptureRequest(**capture_request(config.model_dump(mode="json"))))
            assert await service.get_snapshot(project.id, registered.id, frozen.id) == frozen
            save_evidence("maximum-table-name.json", configured_project_table=project_table, derived_tables=store.tables,
                actual_pg_capture=frozen.model_dump(mode="json"), new_project_name_limit_introduced=False)
        finally:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                for table in owned:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}"')

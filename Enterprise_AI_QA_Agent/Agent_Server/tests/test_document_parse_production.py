"""Production lifespan, HTTP document routes and independent parser Worker CLI."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.application.recorder.capture_config_service import CONFIG_KEY as RECORDING_CAPTURE_KEY
from src.application.test_runs.run_store import PostgresTestRunStore
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_case_action_admission import PostgresCaseActionAdmission
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.recording import RecordingCaptureSettings
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_parsers import actual_docx
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_PARSE_PRODUCTION") != "1",
    reason="isolated DB/MySQL/Redis container, actual production API and independent CLI")]


async def test_actual_production_lifespan_docx_parse_survives_api_restart_and_terminal_replay(tmp_path):
    evidence = Path(os.getenv("QA_DOCUMENT_PARSE_PRODUCTION_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        async with controlled_http(tmp_path) as (business, calls, _, _, _, errors):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = DOCUMENT_PARSE_STREAM
                models = MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-parse-lifespan", provider="openai",
                    transport="openai_chat_completions", base_url=business + "/v1", api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                # The API must create these in an empty database, not recover
                # from tables pre-created by the fixture or this assertion.
                runs = PostgresTestRunStore(settings)
                admission = PostgresCaseActionAdmission(runs=runs, jobs=PostgresToolJobStore(settings))
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    for table in (runs._item_table, admission.consumption.table):
                        cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                        assert cur.fetchone()["existing"] is None
                def startup_prerequisites():
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute("SELECT confrelid::regclass::text AS referenced_table, convalidated "
                            "FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype='f' "
                            "ORDER BY confrelid::regclass::text", (admission.consumption.table,))
                        foreign_keys = [dict(row) for row in cur.fetchall()]
                    assert {row["referenced_table"] for row in foreign_keys} == {
                        runs._item_table, settings.database.postgres_tool_job_table}
                    assert all(row["convalidated"] for row in foreign_keys)
                    config = MySQLApplicationConfigStore(settings).get(RECORDING_CAPTURE_KEY)
                    assert config is not None and config.scope_key == "global"
                    policy = RecordingCaptureSettings.model_validate(config.payload)
                    return {"consumption_table": admission.consumption.table, "foreign_keys": foreign_keys,
                        "capture_config": {"scope_key": config.scope_key, "revision": config.revision,
                            "payload": policy.model_dump(mode="json")}}
                api = processes.start("document-api-first", *args)
                queue = RedisTaskQueue(settings.database.redis_url, stream=DOCUMENT_PARSE_STREAM,
                    group=settings.orchestration.redis_task_consumer_group + ":document_parsing")
                await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                        await wait_api(client, api)
                        first_startup = startup_prerequisites()
                        (evidence / "startup-prerequisites.json").write_text(json.dumps({
                            "tables_absent_before_api": True, "first": first_startup},
                            ensure_ascii=False, indent=2), encoding="utf-8")
                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            assert response.status_code == status, (response.status_code, response.text)
                            return response.json()
                        project = await post("/projects", {"project_key": "document-production-" + uuid4().hex, "name": "真实解析生命周期"}, 201)
                        parsers_response = await client.get(f"/projects/{project['id']}/document-parsers")
                        assert parsers_response.status_code == 200
                        available = [parser["parser_id"] for parser in parsers_response.json() if parser["available"]]
                        assert set(available) == {"markdown", "docx", "pdf"}
                        source = tmp_path / "production-requirements.docx"
                        original = actual_docx(source)
                        doc = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": source.name,
                            "request_id": "production-original", "content_base64": base64.b64encode(original).decode()})
                        owned.append(doc["original_asset"]["storage_uri"])
                        assert (await objects.read_object_uri(doc["original_asset"]["storage_uri"]))["content"] == original
                        base = f"/projects/{project['id']}/documents/{doc['id']}"
                        request = {"creation_key": "production-parse", "version_id": doc["current_version_id"]}
                        submitted = await post(base + "/parse-tasks", request, 202)
                        config_path = f"/projects/{project['id']}/document-parsing-settings"
                        config = (await client.get(config_path)).json()
                        fixed_timeout = config["payload"]["policy"]["timeout_seconds"]
                        config["payload"]["policy"]["timeout_seconds"] = 80
                        response = await client.put(config_path, json=config["payload"])
                        assert response.status_code == 200 and response.json()["scope_key"] == project["id"]
                        await processes.stop(api)
                        api = processes.start("document-api-restarted", *args)
                        await wait_api(client, api)
                        restarted_startup = startup_prerequisites()
                        assert restarted_startup == first_startup
                        (evidence / "startup-prerequisites.json").write_text(json.dumps({
                            "tables_absent_before_api": True, "first": first_startup,
                            "restarted": restarted_startup, "restart_preserved_prerequisites": True},
                            ensure_ascii=False, indent=2), encoding="utf-8")
                        replay = await post(base + "/parse-tasks", request, 202)
                        assert replay["id"] == submitted["id"] and replay["status"] == "queued"
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                                ("document-parsing:" + submitted["id"],))
                            assert cur.fetchone()["n"] == 1
                        assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                        worker = processes.start("document-parser-worker", "-m", "src.cli.run_document_parse_worker", "--once", "--worker-id", "production-parser")
                        assert await processes.wait(worker) == 0
                        task = (await client.get(base + f"/parse-tasks/{submitted['id']}")).json()
                        assert task["status"] == "completed" and task["attempt_no"] == 1
                        snapshot = (await client.get(base + f"/parse-snapshots/{task['snapshot_id']}")).json()
                        owned.append(snapshot["snapshot"]["storage_uri"])
                        assert snapshot["snapshot"]["seed"]["config"]["payload"]["policy"]["timeout_seconds"] == fixed_timeout
                        assert "7 天" in snapshot["result"]["text"] and snapshot["result"]["tables"][0]["rows"][1][1]["text"] == "7 天"
                        assert snapshot["snapshot"]["seed"]["version_id"] == doc["current_version_id"]
                        await queue.enqueue({"schema_version": 1, "task_type": "document_parsing", "project_id": project["id"], "task_id": task["id"]})
                        terminal = processes.start("document-parser-terminal", "-m", "src.cli.run_document_parse_worker", "--once", "--worker-id", "terminal-parser")
                        assert await processes.wait(terminal) == 0
                        assert (await client.get(base + f"/parse-tasks/{task['id']}")).json() == task
                        catalog = (await client.get(base + "/parse-snapshots")).json()
                        assert [entry["id"] for entry in catalog["items"]] == [task["snapshot_id"]]
                        content = (await client.get(f"/registry/api-docs/{doc['id']}/versions/{doc['current_version_id']}/content")).json()
                        assert content["parse_snapshot"]["id"] == task["snapshot_id"] and content["content"] == snapshot["result"]["text"]
                        assert not calls and not errors, "document parsing must not call the model"
                        (evidence / "summary.json").write_text(json.dumps({"scope": "full production API lifespan and independent document Worker CLI; actual isolated PostgreSQL/MySQL/RustFS/Redis",
                            "api_restart_before_parse": True, "request_replayed_same_task": True, "outbox_count": 1,
                            "task": task, "snapshot": snapshot, "frozen_timeout": fixed_timeout, "current_timeout": 80,
                            "terminal_replay_no_reparse": True, "model_calls": len(calls), "available_parsers": available}, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    await queue.close()
                    # Include any artifacts committed before a test assertion/response failure.
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        table = settings.database.postgres_api_doc_table + "_parse_tasks"
                        cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                        if cur.fetchone()["existing"]:
                            cur.execute(f"SELECT checkpoint,failure_artifact FROM {table}")
                            for row in cur.fetchall():
                                for field in ("checkpoint", "failure_artifact"):
                                    if row[field] and row[field]["storage_uri"] not in owned:
                                        owned.append(row[field]["storage_uri"])
    finally:
        for path in tmp_path.glob("document-*.std*"):
            (evidence / path.name).write_bytes(path.read_bytes())

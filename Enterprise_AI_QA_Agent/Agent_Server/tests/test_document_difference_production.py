"""Actual production API, parsing CLI and comparison CLI with immutable sources."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.document_differences import DocumentDifferenceResult
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_differences import verify_spans, write_pair
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_DIFFERENCE_PRODUCTION") != "1",
    reason="actual isolated storage, production API, independent parsing/comparison CLIs")]


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_production_comparison_fixed_sources_restart_replay_and_failure(tmp_path, extension):
    evidence = Path(os.getenv("QA_DOCUMENT_DIFFERENCE_PRODUCTION_EVIDENCE_DIR", str(tmp_path / "evidence"))) / extension
    evidence.mkdir(parents=True, exist_ok=True)
    paths = write_pair(tmp_path, extension)
    for path in paths:
        (evidence / path.name).write_bytes(path.read_bytes())
    try:
        async with controlled_http(tmp_path) as (business, calls, _, _, _, errors):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = ",".join([DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM])
                models = MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-document-startup", provider="openai",
                    transport="openai_chat_completions", base_url=business + "/v1", api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("document-difference-api-first", *args)
                parse_queue = RedisTaskQueue(settings.database.redis_url, stream=DOCUMENT_PARSE_STREAM,
                    group=settings.orchestration.redis_task_consumer_group + ":document_parsing")
                diff_queue = RedisTaskQueue(settings.database.redis_url, stream=DOCUMENT_DIFFERENCE_STREAM,
                    group=settings.orchestration.redis_task_consumer_group + ":document_difference")
                await parse_queue.connect()
                await diff_queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                        await wait_api(client, api)

                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            assert response.status_code == status, (response.status_code, response.text)
                            return response.json()

                        async def get(path, **params):
                            response = await client.get(path, params=params)
                            assert response.status_code == 200, (response.status_code, response.text)
                            return response.json()

                        project = await post("/projects", {"project_key": "diff-production-" + uuid4().hex,
                            "name": "真实文档比较"}, 201)
                        other = await post("/projects", {"project_key": "other-" + uuid4().hex, "name": "隔离项目"}, 201)
                        project_path = f"/projects/{project['id']}"
                        config_path = project_path + "/document-difference-settings"
                        initial = await get(config_path)
                        fixed_policy = {**initial["payload"]["policy"], "timeout_seconds": 70, "max_lines": 1000}
                        config_response = await client.put(config_path, json={"policy": fixed_policy})
                        assert config_response.status_code == 200, config_response.text
                        fixed_config = config_response.json()
                        mysql = MySQLApplicationConfigStore(settings)
                        assert mysql.get("document.difference_settings", scope_key=project["id"]).payload == fixed_config["payload"]
                        document = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": paths[0].name,
                            "request_id": "first-source", "content_base64": base64.b64encode(paths[0].read_bytes()).decode()})
                        base = project_path + f"/documents/{document['id']}"
                        versions, views, parse_tasks = [], [], []
                        for number, path in enumerate(paths, 1):
                            if number == 2:
                                uploaded = await post(f"/registry/api-docs/{document['id']}/versions", {
                                    "project_id": project["id"], "filename": path.name, "request_id": "second-source",
                                    "expected_parent_version_id": document["current_version_id"],
                                    "content_base64": base64.b64encode(path.read_bytes()).decode()})
                                document = uploaded["document"]
                            asset = document["original_asset"]
                            owned.append(asset["storage_uri"])
                            assert (await objects.read_object_uri(asset["storage_uri"]))["content"] == path.read_bytes()
                            versions.append(document["current_version_id"])
                            submitted = await post(base + "/parse-tasks", {"creation_key": f"source-{number}",
                                "version_id": versions[-1]}, 202)
                            assert await PostgresTaskOutbox(settings).relay_once(parse_queue, limit=1) == 1
                            worker = processes.start(f"document-source-{number}", "-m", "src.cli.run_document_parse_worker",
                                "--once", "--worker-id", f"source-{number}")
                            assert await processes.wait(worker) == 0
                            assert "document_parse_processed=1" in (tmp_path / f"document-source-{number}.stdout").read_text(encoding="utf-8")
                            parsed = await get(base + f"/parse-tasks/{submitted['id']}")
                            assert parsed["status"] == "completed" and parsed["attempt_no"] == 1
                            parse_tasks.append(parsed)
                            snapshot = await get(base + f"/parse-snapshots/{parsed['snapshot_id']}")
                            views.append(DocumentParseSnapshotView.model_validate(snapshot))
                            owned.append(snapshot["snapshot"]["storage_uri"])
                            (evidence / f"source-{number}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                        request = {"creation_key": "production-comparison", "old_snapshot_id": views[0].snapshot.id,
                            "new_snapshot_id": views[1].snapshot.id}
                        submitted = await post(base + "/difference-tasks", request, 202)
                        assert submitted["version_id"] == versions[1] and submitted["old_snapshot_id"] == request["old_snapshot_id"]
                        live_policy = {**fixed_policy, "timeout_seconds": 80, "max_lines": 2000}
                        assert (await client.put(config_path, json={"policy": live_policy})).status_code == 200
                        await processes.stop(api)
                        api = processes.start("document-difference-api-restarted", *args)
                        await wait_api(client, api)
                        replay = await post(base + "/difference-tasks", request, 202)
                        assert replay == submitted
                        assert (await get(base + "/difference-tasks/by-request", creation_key=request["creation_key"])) == submitted
                        conflict = await client.post(base + "/difference-tasks", json={**request,
                            "old_snapshot_id": request["new_snapshot_id"], "new_snapshot_id": request["old_snapshot_id"]})
                        assert conflict.status_code == 409
                        assert (await client.post(base + "/difference-tasks", json={**request, "template_id": "invalid"})).status_code == 422
                        assert (await client.post(base + "/difference-tasks", json={**request,
                            "creation_key": "missing-source", "new_snapshot_id": str(uuid4())})).status_code == 404
                        assert (await client.get(base + "/difference-tasks/by-request", params={"creation_key": "missing"})).status_code == 404
                        assert (await client.get(base + "/difference-tasks/by-request", params={"creation_key": ""})).status_code == 422
                        assert (await client.put(config_path, json={"policy": {**live_policy, "max_cpu_seconds": 0}})).status_code == 422
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                                ("document-difference:" + submitted["id"],))
                            assert cur.fetchone()["n"] == 1
                        assert await PostgresTaskOutbox(settings).relay_once(diff_queue, limit=1) == 1
                        worker = processes.start("document-difference-worker", "-m", "src.cli.run_document_difference_worker",
                            "--once", "--worker-id", "production-comparison")
                        assert await processes.wait(worker) == 0
                        assert "document_difference_processed=1" in (tmp_path / "document-difference-worker.stdout").read_text(encoding="utf-8")
                        worker_log = (tmp_path / "document-difference-worker.stderr").read_text(encoding="utf-8")
                        assert submitted["id"] in worker_log and "document_difference_worker_runtime_started" in worker_log
                        task = await get(base + f"/difference-tasks/{submitted['id']}")
                        assert task["status"] == "completed" and task["attempt_no"] == 1
                        snapshot = await get(base + f"/difference-snapshots/{task['snapshot_id']}")
                        owned.append(snapshot["snapshot"]["storage_uri"])
                        result = DocumentDifferenceResult.model_validate(snapshot["result"])
                        verify_spans(result, *views)
                        assert result.old.version_id == versions[0] and result.new.version_id == versions[1]
                        assert snapshot["snapshot"]["seed"]["config"] == fixed_config and result.policy.max_lines == 1000
                        assert 0 < result.policy.timeout_seconds <= 70 and not result.parsing_basis_changed
                        assert result.raw.changes and not result.originals_equal
                        assert any("7" in change.old.text and "9" in change.new.text for change in result.raw.changes)
                        stored = (await objects.read_object_uri(snapshot["snapshot"]["storage_uri"]))["content"]
                        assert hashlib.sha256(stored).hexdigest() == snapshot["snapshot"]["sha256"]
                        assert json.loads(stored) == snapshot["result"]
                        (evidence / "comparison.json").write_bytes(stored)
                        await diff_queue.enqueue({"schema_version": 1, "task_type": "document_difference",
                            "project_id": project["id"], "task_id": task["id"]})
                        terminal = processes.start("document-difference-terminal", "-m", "src.cli.run_document_difference_worker",
                            "--once", "--worker-id", "terminal-comparison")
                        assert await processes.wait(terminal) == 0
                        assert await get(base + f"/difference-tasks/{task['id']}") == task
                        assert await get(base + f"/difference-snapshots/{task['snapshot_id']}") == snapshot
                        assert [entry["id"] for entry in (await get(base + "/difference-snapshots", version_id=versions[1]))["items"]] == [task["snapshot_id"]]
                        assert not (await get(base + "/difference-snapshots", version_id=versions[0]))["items"]
                        for suffix in ["/difference-tasks", "/difference-snapshots", f"/difference-tasks/{task['id']}",
                            f"/difference-snapshots/{task['snapshot_id']}"]:
                            assert (await client.get(base.replace(project["id"], other["id"]) + suffix)).status_code == 404
                        assert (await client.get(base + "/difference-tasks", params={"limit": 0})).status_code == 422
                        assert (await client.get(base + "/difference-snapshots", params={"offset": -1})).status_code == 422
                        assert (await client.get(base + "/difference-snapshots", params={"version_id": str(uuid4())})).status_code == 404
                        assert (await client.get(base + f"/difference-tasks/{task['id']}/failure")).status_code == 409
                        # Tiny CPU budget exercises the actual comparison process and its persisted trace.
                        failed_policy = {**live_policy, "max_cpu_seconds": 0.000001}
                        assert (await client.put(config_path, json={"policy": failed_policy})).status_code == 200
                        limited = await post(base + "/difference-tasks", {**request, "creation_key": "cpu-failure"}, 202)
                        assert await PostgresTaskOutbox(settings).relay_once(diff_queue, limit=1) == 1
                        failed_worker = processes.start("document-difference-failure", "-m", "src.cli.run_document_difference_worker",
                            "--once", "--worker-id", "limited-comparison")
                        assert await processes.wait(failed_worker) == 0
                        failed = await get(base + f"/difference-tasks/{limited['id']}")
                        assert failed["status"] == "failed" and failed["error_code"] == "document_difference_process_failed"
                        trace = await get(base + f"/difference-tasks/{limited['id']}/failure")
                        assert trace["exit_code"] is not None and "CPU" in trace["error"]
                        raw_trace = (await objects.read_object_uri(trace["artifact"]["storage_uri"]))["content"]
                        assert hashlib.sha256(raw_trace).hexdigest() == trace["artifact"]["sha256"]
                        (evidence / "failure.json").write_bytes(raw_trace)
                        page = await get(base + "/difference-tasks", limit=1)
                        assert page["has_more"] and [entry["id"] for entry in page["items"]] == [failed["id"]]
                        assert [entry["id"] for entry in (await get(base + "/difference-tasks", limit=1, offset=1))["items"]] == [task["id"]]
                        # A production restart with dispatch disabled must preserve exact history/replay.
                        await processes.stop(api)
                        disabled = settings.model_copy(deep=True)
                        disabled.orchestration.redis_task_dispatch_enabled = False
                        processes.env = child_environment(disabled)
                        api = processes.start("document-difference-api-disabled", *args)
                        await wait_api(client, api)
                        assert await post(base + "/difference-tasks", request, 202) == task
                        assert await get(base + f"/difference-snapshots/{task['snapshot_id']}") == snapshot
                        assert await get(base + f"/difference-tasks/{limited['id']}/failure") == trace
                        unavailable = await client.post(base + "/difference-tasks", json={**request, "creation_key": "disabled-dispatch"})
                        assert unavailable.status_code == 503 and "按原请求身份查询或重试" in unavailable.json()["detail"]
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_api_doc_table}_diff_tasks")
                            assert cur.fetchone()["n"] == 2
                        assert not calls and not errors, "parsing/comparison must not call the model"
                        for side, view in enumerate(views):
                            assert await get(base + f"/parse-snapshots/{view.snapshot.id}") == view.model_dump(mode="json")
                            original = (await objects.read_object_uri(view.snapshot.seed.original_asset.storage_uri))["content"]
                            assert original == paths[side].read_bytes()
                        (evidence / "summary.json").write_text(json.dumps({"scope": "production API + independent parsing/comparison CLIs + actual MySQL/PG/RustFS/Redis",
                            "extension": extension, "api_restarted": True, "same_request_task": True, "outbox_count": 1,
                            "parse_tasks": parse_tasks, "task": task, "snapshot": snapshot, "failed_task": failed,
                            "model_calls": len(calls), "source_reconstruction": True, "originals_unchanged": True,
                            "disabled_dispatch_history_readable": True, "disabled_dispatch_new_submit_status": 503,
                            "terminal_replay_unchanged": True}, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    await parse_queue.close()
                    await diff_queue.close()
                    # Only this fixture's independent database is queried/cleaned.
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        for suffix in ("parse_tasks", "diff_tasks", "assets"):
                            table = settings.database.postgres_api_doc_table + "_" + suffix
                            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                            if not cur.fetchone()["existing"]:
                                continue
                            if suffix == "assets":
                                cur.execute(f"SELECT record FROM {table}")
                                artifacts = [row["record"] for row in cur.fetchall()]
                            else:
                                cur.execute(f"SELECT checkpoint,failure_artifact FROM {table}")
                                artifacts = [row[field] for row in cur.fetchall() for field in ("checkpoint", "failure_artifact") if row[field]]
                            for artifact in artifacts:
                                if artifact["storage_uri"] not in owned:
                                    owned.append(artifact["storage_uri"])
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                (evidence / path.name).write_bytes(path.read_bytes())

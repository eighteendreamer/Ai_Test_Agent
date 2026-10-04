"""Actual App submission and dropped-response recovery, without model comparison."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
from playwright.async_api import expect
import pytest

from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_differences import write_pair
from test_document_full_product import product_browser, open_documents
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_DIFFERENCE_SUBMISSION_PRODUCT") != "1",
    reason="actual App + production API + independent CLIs + isolated MySQL/PG/RustFS/Redis + Chromium")]


async def test_product_select_settings_dropped_reply_recover_restart_and_exact_result(tmp_path):
    evidence = Path(os.getenv("QA_DOCUMENT_DIFFERENCE_SUBMISSION_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    paths = write_pair(tmp_path, "docx")
    try:
        async with controlled_http(tmp_path) as (business, calls, _, _, _, model_errors):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = ",".join([DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM])
                models = MySQLModelConfigStore(settings); models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-comparison-submission", provider="openai",
                    transport="openai_chat_completions", base_url=business + "/v1", api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                api_args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api_process = processes.start("product-submission-api", *api_args)
                queues = {kind: RedisTaskQueue(settings.database.redis_url, stream=stream,
                    group=settings.orchestration.redis_task_consumer_group + ":" + suffix)
                    for kind, stream, suffix in [("parse", DOCUMENT_PARSE_STREAM, "document_parsing"),
                        ("difference", DOCUMENT_DIFFERENCE_STREAM, "document_difference")]}
                for queue in queues.values(): await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                        await wait_api(client, api_process)
                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            assert response.status_code == status, response.text
                            return response.json()
                        async def get(path):
                            response = await client.get(path)
                            assert response.status_code == 200, response.text
                            return response.json()
                        async def worker(kind, name):
                            assert await PostgresTaskOutbox(settings).relay_once(queues[kind], limit=1) == 1
                            module = "run_document_parse_worker" if kind == "parse" else "run_document_difference_worker"
                            process = processes.start(name, "-m", "src.cli." + module, "--once", "--worker-id", name)
                            assert await processes.wait(process) == 0
                            assert "_processed=1" in (tmp_path / (name + ".stdout")).read_text(encoding="utf-8")

                        project = await post("/projects", {"project_key": "submission-" + uuid4().hex, "name": "完整产品比较提交"}, 201)
                        document = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": paths[0].name,
                            "request_id": "submission-source-1", "content_base64": base64.b64encode(paths[0].read_bytes()).decode()})
                        base = f"/projects/{project['id']}/documents/{document['id']}"
                        sources = []
                        for number, path in enumerate(paths, 1):
                            if number == 2:
                                document = (await post(f"/registry/api-docs/{document['id']}/versions", {
                                    "project_id": project["id"], "expected_parent_version_id": document["current_version_id"],
                                    "request_id": "submission-source-2", "filename": path.name,
                                    "content_base64": base64.b64encode(path.read_bytes()).decode()}))["document"]
                            owned.append(document["original_asset"]["storage_uri"])
                            assert (await objects.read_object_uri(owned[-1]))["content"] == path.read_bytes()
                            parsed = await post(base + "/parse-tasks", {"creation_key": f"submission-parse-{number}",
                                "version_id": document["current_version_id"]}, 202)
                            await worker("parse", f"product-submission-parser-{number}")
                            parsed = await get(base + "/parse-tasks/" + parsed["id"])
                            assert parsed["status"] == "completed"
                            source = await get(base + "/parse-snapshots/" + parsed["snapshot_id"])
                            sources.append(source); owned.append(source["snapshot"]["storage_uri"])
                            (evidence / f"source-{number}.json").write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")

                        async with product_browser(tmp_path, port, evidence) as (page, errors, network):
                            await open_documents(page)
                            async def open_preview():
                                await page.locator(".api-doc-card .api-doc-actions button").first.click()
                                await expect(page.get_by_label("文档版本比较", exact=True)).to_be_visible()
                            await open_preview()
                            panel = page.get_by_label("文档版本比较", exact=True)
                            await expect(panel.get_by_label("旧侧解析快照", exact=True)).to_have_value(sources[0]["snapshot"]["id"])
                            await expect(panel.get_by_label("新侧解析快照", exact=True)).to_have_value(sources[1]["snapshot"]["id"])
                            await panel.locator(".document-difference-settings > summary").click()
                            async def save_settings(seconds):
                                await panel.get_by_label("比较超时（秒）", exact=True).fill(str(seconds))
                                async with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/document-difference-settings")) as saved:
                                    await panel.get_by_role("button", name="保存项目比较设置", exact=True).click()
                                response = await saved.value
                                assert response.status == 200, await response.text()
                                config = await response.json()
                                persisted = MySQLApplicationConfigStore(settings).get("document.difference_settings", scope_key=project["id"])
                                assert persisted.payload == config["payload"] and persisted.revision == config["revision"]
                                await expect(panel.get_by_role("status")).to_contain_text("项目比较设置已保存")
                                return config
                            fixed_config = await save_settings(70)
                            dropped, route_errors = [], []
                            async def drop_committed_response(route):
                                if route.request.method != "POST":
                                    await route.continue_(); return
                                try:
                                    response = await route.fetch()
                                    assert response.status == 202, await response.text()
                                    dropped.append({"request": route.request.post_data_json, "task": await response.json()})
                                except Exception as exc:
                                    route_errors.append(str(exc))
                                finally:
                                    await route.abort("failed")
                            pattern = "**/documents/" + document["id"] + "/difference-tasks"
                            await page.route(pattern, drop_committed_response)
                            await panel.get_by_role("button", name="提交文档比较", exact=True).click()
                            await expect(panel.get_by_role("alert")).to_be_visible()
                            assert len(dropped) == 1 and not route_errors, (dropped, route_errors)
                            await page.unroute(pattern, drop_committed_response)
                            request, submitted = dropped[0]["request"], dropped[0]["task"]
                            assert request["old_snapshot_id"] == sources[0]["snapshot"]["id"]
                            assert request["new_snapshot_id"] == sources[1]["snapshot"]["id"]
                            await expect(panel.locator(".comparison-submission")).to_contain_text(request["creation_key"])
                            await expect(panel.get_by_role("button", name="提交文档比较", exact=True)).to_be_disabled()
                            live_config = await save_settings(80)
                            await panel.get_by_label("旧侧原件版本", exact=True).select_option(sources[1]["snapshot"]["seed"]["version_id"])
                            await expect(panel.get_by_label("旧侧解析快照", exact=True)).to_have_value(sources[1]["snapshot"]["id"])
                            await page.locator(".api-doc-preview-modal .tools-modal-head .icon-btn").click()
                            await open_preview()
                            await expect(panel.locator(".difference-task-status")).to_contain_text("等待比较")
                            await expect(panel.locator(".comparison-submission")).to_contain_text(request["creation_key"])
                            assert await get(base + "/difference-tasks/by-request?creation_key=" + request["creation_key"]) == submitted
                            await panel.get_by_label("旧侧原件版本", exact=True).select_option(sources[1]["snapshot"]["seed"]["version_id"])
                            await expect(panel.get_by_label("旧侧解析快照", exact=True)).to_have_value(sources[1]["snapshot"]["id"])
                            await panel.get_by_role("button", name="查询上次比较提交", exact=True).click()
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                table = settings.database.postgres_api_doc_table + "_diff_tasks"
                                cur.execute(f"SELECT count(*) AS n FROM {table} WHERE project_id=%s", (project["id"],))
                                assert cur.fetchone()["n"] == 1
                                cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_task_outbox_table} WHERE stream=%s", (DOCUMENT_DIFFERENCE_STREAM,))
                                assert cur.fetchone()["n"] == 1
                            await worker("difference", "product-submission-comparison-worker")
                            await expect(panel.locator(".difference-task-status")).to_contain_text("比较结果已保存", timeout=15000)
                            completed = await get(base + "/difference-tasks/" + submitted["id"])
                            result = await get(base + "/difference-snapshots/" + completed["snapshot_id"])
                            owned.append(result["snapshot"]["storage_uri"])
                            assert result["snapshot"]["seed"]["config"] == fixed_config
                            # The remaining task deadline bounds the subprocess budget; seed config stays exactly frozen.
                            assert 0 < result["result"]["policy"]["timeout_seconds"] <= 70
                            assert result["result"]["policy"] == result["snapshot"]["effective_policy"]
                            assert result["snapshot"]["seed"]["old_snapshot"]["id"] == request["old_snapshot_id"]
                            await expect(panel.get_by_label("旧侧固定来源")).to_contain_text(request["old_snapshot_id"])
                            await expect(panel.locator(".difference-change")).to_contain_text("7 天")
                            await expect(panel.locator(".difference-change")).to_contain_text("9 天")
                            async with page.expect_download() as download:
                                await panel.get_by_role("button", name="下载完整比较结果 JSON", exact=True).click()
                            await (await download.value).save_as(evidence / "downloaded-comparison.json")
                            assert json.loads((evidence / "downloaded-comparison.json").read_text(encoding="utf-8")) == result
                            await page.screenshot(path=str(evidence / "submitted-result.png"), full_page=True)
                            await expect(panel.get_by_role("button", name="提交文档比较", exact=True)).to_be_enabled()

                            # A stopped dispatcher must not prevent recovery of the exact finished submission.
                            await processes.stop(api_process)
                            settings.orchestration.redis_task_outbox_streams = DOCUMENT_PARSE_STREAM
                            processes.env = child_environment(settings)
                            api_process = processes.start("product-submission-api-restarted", *api_args)
                            await wait_api(client, api_process)
                            await page.reload(); await open_documents(page); await open_preview()
                            await expect(panel.get_by_label("比较结果详情")).to_contain_text(completed["snapshot_id"])
                            assert await get(base + "/difference-tasks/" + completed["id"]) == completed
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/difference-tasks")) as rejected:
                                await panel.get_by_role("button", name="提交文档比较", exact=True).click()
                            assert (await rejected.value).status == 503
                            await expect(panel.get_by_role("alert")).to_be_visible()
                            await panel.locator("details").filter(has_text="比较任务历史").locator("summary").click()
                            await panel.get_by_role("button", name="查看历史比较任务 " + completed["id"], exact=True).click()
                            await expect(panel.get_by_label("比较结果详情")).to_contain_text(completed["snapshot_id"])
                            await expect(panel.get_by_role("button", name="提交文档比较", exact=True)).to_be_disabled()
                            await page.screenshot(path=str(evidence / "disabled-dispatch-history.png"), full_page=True)
                            archived = await post(f"/projects/{project['id']}/archive", {})
                            assert archived["status"] == "archived"
                            await page.reload(); await open_documents(page); await open_preview()
                            await expect(panel.get_by_role("button", name="提交文档比较", exact=True)).to_have_count(0)
                            await expect(panel.get_by_role("button", name="保存项目比较设置", exact=True)).to_have_count(0)
                            await panel.locator("details").filter(has_text="比较任务历史").locator("summary").click()
                            await panel.get_by_role("button", name="查看历史比较任务 " + completed["id"], exact=True).click()
                            await expect(panel.get_by_label("比较结果详情")).to_contain_text(completed["snapshot_id"])
                            await page.screenshot(path=str(evidence / "archived-history.png"), full_page=True)
                            assert not errors and not calls and not model_errors, (errors, calls, model_errors)
                            lookups = [event for event in network if "/difference-tasks/by-request" in event["url"]]
                            assert any(request["creation_key"] in event["url"] and event["status"] == 200 for event in lookups)
                            (evidence / "summary.json").write_text(json.dumps({"scope": "actual App submission + production API/independent CLIs + real MySQL/PG/RustFS/Redis",
                                "request": request, "submitted": submitted, "completed": completed, "result": result,
                                "fixed_config": fixed_config, "current_config": live_config, "dropped_post_recovered": True,
                                "task_count": 1, "outbox_count": 1, "api_restarted": True, "disabled_new_status": 503,
                                "exact_download": True, "archived_readonly_history": True,
                                "request_lookups": lookups, "model_calls": len(calls)}, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    for queue in queues.values(): await queue.close()
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        for suffix in ("parse", "diff"):
                            table = settings.database.postgres_api_doc_table + "_" + suffix + "_tasks"
                            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                            if cur.fetchone()["existing"]:
                                cur.execute(f"SELECT checkpoint,failure_artifact FROM {table}")
                                for row in cur.fetchall():
                                    for field in ("checkpoint", "failure_artifact"):
                                        if row[field] and row[field]["storage_uri"] not in owned: owned.append(row[field]["storage_uri"])
                        cur.execute("SELECT to_regclass(%s) AS existing", (settings.database.postgres_api_doc_table + "_assets",))
                        if cur.fetchone()["existing"]:
                            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_assets")
                            for row in cur.fetchall():
                                if row["record"]["storage_uri"] not in owned: owned.append(row["record"]["storage_uri"])
    finally:
        for path in tmp_path.glob("product-*.std*"): shutil.copyfile(path, evidence / path.name)
        for path in paths:
            if path.exists(): shutil.copyfile(path, evidence / path.name)

"""Built product App and production document API with actual isolated stores."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from docx import Document
import httpx
from playwright.async_api import async_playwright, expect
import pytest

from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.api_docs import ApiDocVersionResult
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_parsers import actual_docx
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DOCUMENT_FULL_PRODUCT") != "1",
    reason="built App/router and actual production API/CLI; isolated DB, MySQL, RustFS, Redis container")]


@asynccontextmanager
async def product_browser(tmp_path, api_port, evidence):
    web_root = Path(__file__).resolve().parents[2] / "agent_web"
    npm, node = shutil.which("npm.cmd") or shutil.which("npm"), shutil.which("node")
    assert npm and node
    with (tmp_path / "product-build.stdout").open("wb") as out, (tmp_path / "product-build.stderr").open("wb") as err:
        built = await asyncio.to_thread(subprocess.run, [npm, "run", "build"], cwd=web_root,
            stdout=out, stderr=err, timeout=120)
    assert built.returncode == 0, "actual product build failed; preserved stdout/stderr"
    port = free_port()
    errors, requests = [], []
    with (tmp_path / "product-preview.stdout").open("wb") as out, (tmp_path / "product-preview.stderr").open("wb") as err:
        preview = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "preview",
            "--host", "127.0.0.1", "--port", str(port), "--strictPort"], cwd=web_root,
            env={**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{api_port}", "API_AUTH_TOKEN": ""},
            stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            origin = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient() as probe:
                async with asyncio.timeout(30):
                    while True:
                        assert preview.poll() is None, "preview exited; preserved stdout/stderr"
                        try:
                            if (await probe.get(origin)).status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(.1)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page(viewport={"width": 1440, "height": 1100})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("response", lambda response: requests.append({"url": response.url,
                        "method": response.request.method, "status": response.status}))
                    await page.goto(origin)
                    yield page, errors, requests
                finally:
                    try:
                        if browser.contexts and browser.contexts[0].pages:
                            await page.screenshot(path=str(evidence / "last-page.png"), full_page=True)
                            (evidence / "last-page.txt").write_text(await page.locator("body").inner_text(), encoding="utf-8")
                            (evidence / "last-page-aria.txt").write_text(await page.locator("body").aria_snapshot(), encoding="utf-8")
                    finally:
                        await browser.close()
        finally:
            if preview.poll() is None:
                preview.terminate()
            try:
                await asyncio.to_thread(preview.wait, timeout=10)
            except subprocess.TimeoutExpired:
                preview.kill()
                await asyncio.to_thread(preview.wait, timeout=10)
            (evidence / "network.json").write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8")
            (evidence / "page-errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")


async def open_documents(page):
    await page.locator('.left-nav a[href="/tools"]').click()
    await page.locator(".tools-secondary-nav button").filter(has_text="API 接口文档").click()
    await expect(page.get_by_role("heading", name="API 接口文档", exact=True)).to_be_visible()


async def test_built_product_project_upload_parse_versions_navigation_and_restart(tmp_path):
    evidence = Path(os.getenv("QA_DOCUMENT_FULL_PRODUCT_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        async with controlled_http(tmp_path) as (business, calls, _, _, _, model_errors):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = DOCUMENT_PARSE_STREAM
                models = MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-document-product", provider="openai",
                    transport="openai_chat_completions", base_url=business + "/v1", api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False,
                    capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                # Processes uses the actual settings; it was created before this model-independent stream override.
                processes.env = child_environment(settings)
                api_port = free_port()
                api_args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
                api_process = processes.start("product-api-first", *api_args)
                queue = RedisTaskQueue(settings.database.redis_url, stream=DOCUMENT_PARSE_STREAM,
                    group=settings.orchestration.redis_task_consumer_group + ":document_parsing")
                await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                        await wait_api(client, api_process)
                        original_path = tmp_path / "product-requirements.docx"
                        original = actual_docx(original_path)
                        updated_path = tmp_path / "product-requirements-v2.docx"
                        updated = Document(original_path)
                        updated.tables[0].cell(1, 1).text = "9 天"
                        updated.save(updated_path)
                        async with product_browser(tmp_path, api_port, evidence) as (page, errors, network):
                            await expect(page.locator(".left-nav")).to_be_visible()
                            await page.locator('.left-nav a[href="/projects"]').click()
                            await page.locator(".projects-page .page-head button").filter(has_text="新建项目").click()
                            await page.get_by_label("项目标识", exact=True).fill("full-product-doc-" + uuid4().hex[:10])
                            project_name = "完整产品文档验收"
                            await page.get_by_label("项目名称", exact=True).fill(project_name)
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/projects")) as created:
                                await page.locator(".editor").get_by_role("button", name="保存", exact=True).click()
                            response = await created.value
                            assert response.status == 201, await response.text()
                            project = await response.json()
                            await expect(page.locator(".editor")).not_to_be_visible()
                            await expect(page.locator(".project-card")).to_contain_text(project_name)
                            await open_documents(page)
                            await page.locator(".tools-page .page-head-actions button").filter(has_text="添加文档源").click()
                            await page.locator('.upload-modal input[type="file"]').set_input_files(original_path)
                            await page.locator(".upload-form-grid label").filter(has_text="所属项目（建议填写）").locator(".n-base-selection").click()
                            await page.get_by_text(project_name, exact=True).last.click()
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api-docs/upload")) as uploaded:
                                await page.get_by_role("button", name="开始导入", exact=True).click()
                            response = await uploaded.value
                            assert response.status == 200, await response.text()
                            doc = await response.json()
                            owned.append(doc["original_asset"]["storage_uri"])
                            assert doc["project_id"] == project["id"]
                            assert (await objects.read_object_uri(owned[-1]))["content"] == original
                            await expect(page.locator(".upload-modal")).not_to_be_visible()
                            await page.locator(".api-doc-card .api-doc-actions button").first.click()
                            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()

                            # Real settings persist through the shipped form, not a test-only API setter.
                            await page.locator(".document-parse-settings > summary").click()
                            await page.get_by_label("解析超时（秒）", exact=True).fill("70")
                            async with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/document-parsing-settings")) as saved:
                                await page.get_by_role("button", name="保存项目解析设置", exact=True).click()
                            response = await saved.value
                            assert response.status == 200, await response.text()
                            configuration = await response.json()
                            assert configuration["scope_key"] == project["id"]
                            mysql_configuration = MySQLApplicationConfigStore(settings).get(
                                "document.parsing_settings", scope_key=project["id"])
                            assert mysql_configuration.payload == configuration["payload"]
                            assert mysql_configuration.revision == configuration["revision"]

                            async def parse(worker_name):
                                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/parse-tasks")) as submitted:
                                    await page.get_by_role("button", name="提交解析", exact=True).click()
                                response = await submitted.value
                                assert response.status == 202, await response.text()
                                task = await response.json()
                                assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                                worker = processes.start(worker_name, "-m", "src.cli.run_document_parse_worker",
                                    "--once", "--worker-id", worker_name)
                                assert await processes.wait(worker) == 0
                                await expect(page.locator(".parse-task-status")).to_contain_text("解析任务已完成", timeout=20000)
                                result = (await client.get(f"/projects/{project['id']}/documents/{doc['id']}/parse-tasks/{task['id']}")).json()
                                snapshot = (await client.get(f"/projects/{project['id']}/documents/{doc['id']}/parse-snapshots/{result['snapshot_id']}")).json()
                                owned.append(snapshot["snapshot"]["storage_uri"])
                                return result, snapshot

                            first_task, first_snapshot = await parse("product-parser-v1")
                            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 天")
                            assert first_snapshot["snapshot"]["seed"]["config"]["payload"]["policy"]["timeout_seconds"] == 70
                            await page.locator('.document-versions input[type="file"]').set_input_files(updated_path)
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/versions")) as version:
                                await page.get_by_role("button", name="上传新版本", exact=True).click()
                            response = await version.value
                            assert response.status == 200, await response.text()
                            version_result = ApiDocVersionResult.model_validate(await response.json())
                            assert version_result.created_new
                            v2 = version_result.document.model_dump(mode="json")
                            owned.append(v2["original_asset"]["storage_uri"])
                            assert v2["version_number"] == 2
                            await expect(page.get_by_label("解析原件版本", exact=True)).to_have_value(v2["current_version_id"])
                            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()
                            second_task, second_snapshot = await parse("product-parser-v2")
                            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("9 天")

                            # Navigate out of the mounted plugin, then reload the real App after an API restart.
                            await page.locator(".api-doc-preview-modal .tools-modal-head .icon-btn").click()
                            await page.locator('.left-nav a[href="/projects"]').click()
                            await expect(page.locator(".project-card")).to_contain_text(project_name)
                            restart_start = len(network)
                            await processes.stop(api_process)
                            api_process = processes.start("product-api-restarted", *api_args)
                            await wait_api(client, api_process)
                            restart_end = len(network)
                            await page.reload()
                            await open_documents(page)
                            await page.locator(".api-doc-card .api-doc-actions button").first.click()
                            await expect(page.locator(".parse-task-status")).to_contain_text("解析任务已完成")
                            await page.get_by_label("解析原件版本", exact=True).select_option(doc["current_version_id"])
                            await page.get_by_role("button", name="查看解析快照 " + first_task["snapshot_id"], exact=True).click()
                            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 天")
                            async with page.expect_download() as download:
                                await page.get_by_role("button", name="下载完整解析结果 JSON", exact=True).click()
                            content = json.loads(Path(await (await download.value).path()).read_text(encoding="utf-8"))
                            assert content == first_snapshot
                            assert (await client.get(f"/projects/{project['id']}/documents/{doc['id']}/parse-tasks")).json()["items"] == [second_task, first_task]
                            assert not errors and not calls and not model_errors, (errors, model_errors)
                            failures = [entry for index, entry in enumerate(network) if "/api/" in entry["url"]
                                and entry["status"] >= 400 and not restart_start <= index < restart_end]
                            assert not failures, failures
                            assets = [entry["url"] for entry in network if "/assets/" in entry["url"] and entry["status"] == 200]
                            assert assets, "must load built assets, not a probe host"
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(f"SELECT name FROM {settings.database.postgres_project_table} WHERE id=%s", (project["id"],))
                                assert cur.fetchone()["name"] == project_name
                                cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_api_doc_table}_versions WHERE document_id=%s", (doc["id"],))
                                assert cur.fetchone()["n"] == 2
                                cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_api_doc_table}_parse_snapshots WHERE document_id=%s", (doc["id"],))
                                assert cur.fetchone()["n"] == 2
                            (evidence / "summary.json").write_text(json.dumps({
                                "scope": "built product index/main/App/Pinia/router/sidebar with actual production API and independent parsing Worker CLI",
                                "project_created_in_browser": project, "document": doc["id"],
                                "versions": [doc["current_version_id"], v2["current_version_id"]],
                                "first_task": first_task, "second_task": second_task,
                                "old_table_7_new_table_9": True, "mysql_project_configuration": configuration,
                                "navigation_and_api_restart": True, "old_json_download_exact": True,
                                "source_sha256": hashlib.sha256(original).hexdigest(),
                                "model_calls": len(calls), "api_errors": failures, "page_errors": errors,
                                "planned_restart_response_failures": [entry for entry in network[restart_start:restart_end] if entry["status"] >= 400],
                                "built_assets": assets,
                            }, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    await queue.close()
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        # The whole database belongs to this test; include versions saved before response/assertion failures.
                        cur.execute("SELECT to_regclass(%s) AS existing", (settings.database.postgres_api_doc_table + "_assets",))
                        if cur.fetchone()["existing"]:
                            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_assets")
                            for row in cur.fetchall():
                                uri = row["record"]["storage_uri"]
                                if uri not in owned:
                                    owned.append(uri)
                        cur.execute("SELECT to_regclass(%s) AS existing", (settings.database.postgres_api_doc_table + "_parse_tasks",))
                        if cur.fetchone()["existing"]:
                            cur.execute(f"SELECT checkpoint,failure_artifact FROM {settings.database.postgres_api_doc_table}_parse_tasks")
                            for row in cur.fetchall():
                                for field in ("checkpoint", "failure_artifact"):
                                    if row[field] and row[field]["storage_uri"] not in owned:
                                        owned.append(row[field]["storage_uri"])
    finally:
        for path in tmp_path.glob("product-*.std*"):
            shutil.copyfile(path, evidence / path.name)
        for path in tmp_path.glob("product-requirements*.docx"):
            shutil.copyfile(path, evidence / path.name)
        (evidence / ".gitattributes").write_text('*.docx -text -diff -merge\n', encoding="utf-8")

"""Full shipped Vue App and independent production main, with isolated stores."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import async_playwright, expect

from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.test_cases.template_service import TEMPLATE_CONFIG_KEY
from test_case_import_service import _xlsx
from test_execution_worker_cli import isolated_runtime, free_port, wait_api


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PROJECT_AUTHORING_APP") != "1",
                    reason="requires real main, Chromium/Vite and MySQL/PG/RustFS/Docker")
@pytest.mark.parametrize("layout", ["rows", "split", "json"])
async def test_full_app_project_template_import_review_and_restart(tmp_path, layout):
    root = Path(__file__).resolve().parents[2]
    evidence = Path(os.environ["PROJECT_AUTHORING_APP_EVIDENCE"]) / layout
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned_objects):
            port = free_port()
            args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("authoring-api-first", *args)
            backend_origin = f"http://127.0.0.1:{port}"
            web_root = root / "agent_web"
            node = shutil.which("node")
            assert node and (web_root / "node_modules/vite/bin/vite.js").is_file()
            web_port = free_port()
            vite = None
            errors, traces = [], []
            with (evidence / "vite.log").open("wb") as log:
                try:
                    async with httpx.AsyncClient(base_url=backend_origin + "/api/v1", timeout=50) as client:
                        await wait_api(client, api)
                        vite = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port)],
                            cwd=web_root, env={**os.environ, "VITE_API_PROXY_TARGET": backend_origin, "API_AUTH_TOKEN": ""}, stdout=log, stderr=log)
                        url = f"http://127.0.0.1:{web_port}/projects"
                        async with asyncio.timeout(20):
                            while True:
                                assert vite.poll() is None, (evidence / "vite.log").read_text(encoding="utf-8", errors="replace")
                                try:
                                    if (await client.get(url)).status_code == 200: break
                                except httpx.ConnectError:
                                    pass
                                await asyncio.sleep(.1)
                        async with async_playwright() as playwright:
                            browser = await playwright.chromium.launch()
                            try:
                                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                                page.on("pageerror", lambda error: errors.append(str(error)))
                                page.on("response", lambda response: traces.append({"path": response.url.split("/api/v1", 1)[1].split("?", 1)[0],
                                    "method": response.request.method, "status": response.status}) if "/api/v1" in response.url else None)
                                await page.goto(url)
                                await expect(page.get_by_role("button", name=re.compile("新建项目$"))).to_be_visible()
                                key = "full-authoring-" + uuid4().hex[:12]
                                name = "完整项目页面 " + layout
                                await page.get_by_role("button", name=re.compile("新建项目$")).click()
                                await page.get_by_label("项目标识", exact=True).fill(key)
                                await page.get_by_label("项目名称", exact=True).fill(name)
                                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/projects")) as creating:
                                    await page.get_by_role("button", name="保存", exact=True).click()
                                created = await (await creating.value).json()
                                project_id = created["id"]
                                save("project.json", created)
                                await page.get_by_role("button").filter(has=page.get_by_text(name, exact=True)).click()
                                await expect(page.get_by_role("button", name="用例生成模板", exact=True)).to_be_enabled()
                                await page.get_by_role("button", name="用例生成模板", exact=True).click()
                                template = page.get_by_role("dialog", name="用例生成模板")
                                await expect(template.get_by_text("系统默认模板", exact=False)).to_be_visible()
                                default = await client.get(f"/projects/{project_id}/test-case-generation-template")
                                assert default.status_code == 200 and default.json()["is_default"] is True
                                await template.get_by_label("AI 写作指导", exact=True).fill("完整页面验证：保持未登录负向条件，保留原来源。")
                                async with page.expect_response(lambda r: r.request.method == "PUT" and "/test-case-generation-template" in r.url) as saving:
                                    await template.get_by_role("button", name="保存项目模板", exact=True).click()
                                saved_template = await (await saving.value).json()
                                assert saved_template["is_default"] is False
                                configs = MySQLApplicationConfigStore(settings)
                                config = configs.get(TEMPLATE_CONFIG_KEY, scope_key=project_id)
                                assert config.payload["writing_guidance"] == saved_template["payload"]["writing_guidance"]
                                save("template-default.json", default.json()); save("template-project.json", saved_template)
                                await template.get_by_role("button", name="关闭", exact=True).click()
                                await page.get_by_role("button", name="导入 Excel / JSON", exact=True).click()
                                importing = page.get_by_role("dialog", name="导入测试用例")
                                await expect(importing.get_by_label("Excel / JSON 文件")).to_be_enabled()
                                file = _xlsx(tmp_path, layout) if layout != "json" else tmp_path / "actual-cases.json"
                                if layout == "json":
                                    file.write_text(json.dumps([{"case_key": "full-json", "title": "完整App任意JSON", "content_json": {"嵌套": [0, False, None, "中文😀"], "未知字段": {"保留": True}}}], ensure_ascii=False), encoding="utf-8")
                                shutil.copyfile(file, evidence / file.name)
                                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/sources")) as uploading:
                                    await importing.get_by_label("Excel / JSON 文件").set_input_files(file)
                                source = await (await uploading.value).json()
                                owned_objects.append(source["storage_uri"])
                                save("import-source.json", source)
                                async with page.expect_download() as downloading:
                                    await importing.get_by_role("link", name="下载原件", exact=True).click()
                                downloaded = evidence / "downloaded-original"
                                await (await downloading.value).save_as(downloaded)
                                assert downloaded.read_bytes() == file.read_bytes()
                                if layout == "split": await importing.get_by_label("导入布局").select_option("split")
                                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/previews")) as previewing:
                                    await importing.get_by_role("button", name="生成导入预览", exact=True).click()
                                preview = await (await previewing.value).json()
                                save("import-preview.json", preview)
                                await expect(importing.get_by_text("预览：1 条有效候选", exact=True)).to_be_visible()
                                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/commit")) as committing:
                                    await importing.get_by_role("button", name="提交草稿", exact=True).click()
                                committed = await (await committing.value).json()
                                await expect(importing.get_by_text("导入完成，草稿等待评审", exact=True)).to_be_visible()
                                case_id = committed["batch"]["case_ids"][0]
                                save("import-committed.json", committed)
                                await importing.get_by_role("button", name="关闭", exact=True).click()
                                row = page.locator("tr").filter(has_text=preview["batch"]["preview"]["candidates"][0]["draft"]["title"])
                                await row.get_by_role("button", name="查看内容", exact=True).click()
                                dialog = page.get_by_role("dialog", name="测试用例内容")
                                await dialog.get_by_role("button", name="JSON 原文", exact=True).click()
                                raw = json.loads(await dialog.locator("pre").inner_text())
                                assert raw == preview["batch"]["preview"]["candidates"][0]["draft"]["content_json"]
                                await dialog.get_by_role("button", name="提交当前版本评审", exact=True).click()
                                await expect(dialog.get_by_role("button", name="启用当前版本", exact=True)).to_be_enabled()
                                await dialog.get_by_role("button", name="启用当前版本", exact=True).click()
                                await expect(dialog.get_by_text("当前版本：已确认 · 当前启用", exact=True)).to_be_visible()
                                record = (await client.get(f"/test-cases/{case_id}")).json()
                                versions = (await client.get(f"/test-cases/{case_id}/versions")).json()
                                assert len(versions) == 1 and record["active_version_id"] == versions[0]["id"]
                                assert versions[0]["content_json"] == raw
                                with postgres_connect(settings) as connection, connection.cursor() as cursor:
                                    cursor.execute(f"SELECT record FROM {settings.database.postgres_test_case_table} WHERE project_id=%s", (project_id,))
                                    stored_cases = [row["record"] for row in cursor.fetchall()]
                                    cursor.execute(f"SELECT record FROM {settings.database.postgres_test_case_version_table} WHERE case_id=%s", (case_id,))
                                    stored_versions = [row["record"] for row in cursor.fetchall()]
                                assert len(stored_cases) == len(stored_versions) == 1
                                assert stored_cases[0] == record and stored_versions == versions
                                save("postgres-case-version.json", {"cases": stored_cases, "versions": stored_versions})
                                save("case-active.json", record); save("case-versions.json", versions)
                                await page.screenshot(path=str(evidence / "full-app-case.png"), full_page=True)
                                await dialog.get_by_role("button", name="关闭", exact=True).click()
                                await page.get_by_role("button", name="用例生成模板", exact=True).click()
                                template = page.get_by_role("dialog", name="用例生成模板")
                                await expect(template.get_by_label("AI 写作指导", exact=True)).to_have_value(saved_template["payload"]["writing_guidance"])
                                updated_guidance = "后续生成改为新写作指导；已有用例不变。"
                                await template.get_by_label("AI 写作指导", exact=True).fill(updated_guidance)
                                async with page.expect_response(lambda r: r.request.method == "PUT" and "/test-case-generation-template" in r.url) as resaving:
                                    await template.get_by_role("button", name="保存项目模板", exact=True).click()
                                updated_template = await (await resaving.value).json()
                                assert updated_template["revision"] > saved_template["revision"]
                                assert (await client.get(f"/test-cases/{case_id}/versions")).json() == versions
                                save("template-after-import.json", updated_template)
                                await template.get_by_role("button", name="关闭", exact=True).click()
                                await processes.stop(api)
                                api = processes.start("authoring-api-restarted", *args)
                                await wait_api(client, api)
                                await page.reload()
                                await expect(page.get_by_role("button", name="用例生成模板", exact=True)).to_be_enabled()
                                restored = (await client.get(f"/test-cases/{case_id}/versions")).json()
                                assert restored == versions
                                await page.get_by_role("button", name="用例生成模板", exact=True).click()
                                await expect(page.get_by_role("dialog", name="用例生成模板").get_by_label("AI 写作指导", exact=True)).to_have_value(updated_guidance)
                                save("summary.json", {"layout": layout, "project_id": project_id, "case_id": case_id,
                                    "mysql_project_template": True, "pg_fixed_version": True, "rustfs_original_bytes_equal": True,
                                    "review_activate": True, "independent_main_restart_equal": True, "page_errors": errors,
                                    "template_change_keeps_fixed_version": True, "postgres_case_version_count": [1, 1],
                                    "original_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                                    "scope": "full shipped App/production main; no model generation or Agent execution"})
                                assert not errors, errors
                            finally:
                                save("browser-http.json", traces); save("page-errors.json", errors)
                                save("final-page.json", {"snapshot": await page.locator("body").aria_snapshot()})
                                await page.screenshot(path=str(evidence / "final-page.png"), full_page=True)
                                await browser.close()
                finally:
                    if vite is not None:
                        if vite.poll() is None: vite.terminate()
                        try: await asyncio.to_thread(vite.wait, timeout=5)
                        except subprocess.TimeoutExpired: vite.kill(); await asyncio.to_thread(vite.wait, timeout=5)
    finally:
        # Keep failed production startup/cleanup evidence too, excluding env/secrets.
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

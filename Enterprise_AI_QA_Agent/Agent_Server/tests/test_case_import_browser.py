"""Real import component + HTTP + isolated MySQL/PostgreSQL/RustFS.

The host page mounts the shipped component, not the entire production app.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.async_api import async_playwright, expect

from src.api.routes.case_imports import router
from src.schemas.project import ProjectCreateRequest
from test_case_import_service import _environment, _xlsx


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_IMPORT_BROWSER") != "1", reason="real browser and isolated MySQL/PostgreSQL/RustFS")
@pytest.mark.parametrize("layout", ["rows", "split", "json"])
async def test_browser_actual_file_preview_and_commit(tmp_path, layout):
    async with _environment(tmp_path, True) as (_, projects, cases, case_store, configs, _, _, service, _):
        project = await projects.create(ProjectCreateRequest(project_key="browser-import", name="浏览器导入验收"))
        app = FastAPI()
        app.state.case_import_service = service
        app.include_router(router, prefix="/api/v1")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        backend_port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        web_root = Path(__file__).resolve().parents[2] / "agent_web"
        host = web_root / f"__case_import_probe_{uuid4().hex}.html"
        assert not host.exists()
        host.write_text('''<html><head><meta charset="utf-8"><style>:root{--surface:#fff;--surface-muted:#f5f7fa;--text:#18212f;--border:#cbd5e1}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp } from 'vue';
import CaseImportDialog from '/src/features/projects/CaseImportDialog.vue';
createApp(CaseImportDialog, { projectId: ''' + json.dumps(project.id) + ''', modes: [{key:'api_testing',name:'API 测试'}] }).mount('#app');
</script></body></html>''', encoding="utf-8")
        port_socket = socket.socket(); port_socket.bind(("127.0.0.1", 0))
        web_port = port_socket.getsockname()[1]; port_socket.close()
        environment = {**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{backend_port}", "API_AUTH_TOKEN": ""}
        node = shutil.which("node")
        assert node and (web_root / "node_modules/vite/bin/vite.js").is_file()
        vite = None
        try:
            with (tmp_path / "vite.log").open("wb") as log:
                vite = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port)],
                    cwd=web_root, env=environment, stdout=log, stderr=log)
                url = f"http://127.0.0.1:{web_port}/{host.name}"
                async with httpx.AsyncClient() as client:
                    async with asyncio.timeout(20):
                        while True:
                            if vite.poll() is not None:
                                raise RuntimeError((tmp_path / "vite.log").read_text(encoding="utf-8", errors="replace"))
                            try:
                                if server.started and (await client.get(url)).status_code == 200: break
                            except httpx.ConnectError:
                                pass
                            await asyncio.sleep(0.1)
                file = _xlsx(tmp_path, layout=layout) if layout != "json" else tmp_path / "actual.json"
                if layout == "json":
                    file.write_text(json.dumps([{"case_key": "custom-browser", "title": "任意 JSON", "content_json": {"扩展": [0, False, None, "中文😀"]}}], ensure_ascii=False), encoding="utf-8")
                errors = []
                console_errors = []
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        page = await browser.new_page(viewport={"width": 1280, "height": 900})
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("console", lambda message: console_errors.append(message.text) if message.type in {"error", "warning"} else None)
                        await page.goto(url)
                        try:
                            await expect(page.get_by_label("Excel / JSON 文件")).to_be_enabled()
                        except AssertionError:
                            raise AssertionError({"snapshot": await page.locator("body").aria_snapshot(), "errors": errors, "console": console_errors}) from None
                        snapshot_before = await page.locator("body").aria_snapshot()
                        await page.get_by_label("Excel / JSON 文件").set_input_files(file)
                        await expect(page.get_by_role("button", name="生成导入预览")).to_be_enabled()
                        if layout == "split": await page.get_by_label("导入布局").select_option("split")
                        async with page.expect_download() as downloading:
                            await page.get_by_role("link", name="下载原件").click()
                        downloaded = await downloading.value
                        downloaded_path = tmp_path / "downloaded-original"
                        await downloaded.save_as(downloaded_path)
                        assert downloaded_path.read_bytes() == file.read_bytes()
                        async with page.expect_response(lambda response: response.url.endswith("/previews") and response.request.method == "POST") as previewing:
                            await page.get_by_role("button", name="生成导入预览").click()
                        response = await previewing.value
                        assert response.status == 200
                        view = await response.json()
                        await expect(page.get_by_text("预览：1 条有效候选")).to_be_visible()
                        assert view["batch"]["preview"]["options"]["layout"] == layout
                        await page.get_by_role("button", name="记住项目导入格式").click()
                        await expect(page.get_by_text("项目导入格式已保存")).to_be_visible()
                        assert configs.get("test_case.import_settings", scope_key=project.id).payload["options"]["layout"] == layout
                        async with page.expect_response(lambda response: response.url.endswith("/commit")) as committing:
                            await page.get_by_role("button", name="提交草稿", exact=True).click()
                        committed = await (await committing.value).json()
                        await expect(page.get_by_text("导入完成，草稿等待评审")).to_be_visible()
                        case_id = committed["batch"]["case_ids"][0]
                        assert await case_store.count_by_project(project.id) == 1
                        case = await cases.get_case(case_id)
                        version = (await cases.list_versions(case_id))[0]
                        assert case.lifecycle_status == "draft" and case.active_version_id is None
                        assert version.content_json == view["batch"]["preview"]["candidates"][0]["draft"]["content_json"]
                        if layout == "split": assert [step["order"] for step in version.content_json["steps"]] == [1, 2]
                        if layout == "json": assert version.content_json == {"扩展": [0, False, None, "中文😀"]}
                        repeat = await page.request.post(f"http://127.0.0.1:{web_port}/api/v1/projects/{project.id}/test-case-imports/batches/{view['batch']['id']}/commit")
                        assert (await repeat.json())["batch"]["case_ids"] == [case_id]
                        assert await case_store.count_by_project(project.id) == 1
                        assert not errors, errors
                        evidence = web_root.parent / "docs/upgrade_2026-10-03"
                        (evidence / f"p1-import-browser-{layout}.json").write_text(json.dumps({
                            "layout": layout, "file": file.name, "original_bytes_equal": True,
                            "configuration_in_mysql": True, "draft_in_postgres": True, "idempotent_commit": True,
                            "snapshot_before": snapshot_before, "snapshot_after": await page.locator("body").aria_snapshot(),
                            "page_errors": errors, "scope": "real component mounted in isolated host; production startup not covered",
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        if layout == "rows": await page.screenshot(path=str(evidence / "p1-import-browser.png"), full_page=True)
                    finally:
                        await browser.close()
        finally:
            if vite is not None:
                if vite.poll() is None: vite.terminate()
                try: vite.wait(timeout=5)
                except subprocess.TimeoutExpired: vite.kill(); vite.wait(timeout=5)
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
            listener.close()
            assert host.parent == web_root
            host.unlink(missing_ok=True)

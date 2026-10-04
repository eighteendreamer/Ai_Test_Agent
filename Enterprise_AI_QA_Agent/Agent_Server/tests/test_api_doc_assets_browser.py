"""Shipped document plugin + real HTTP/file downloads + isolated PG/RustFS.

The host mounts the actual plugin; it does not claim complete application startup.
"""
from __future__ import annotations

import asyncio
import json
import os
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

from src.api.routes.api_docs import router
from src.api.routes.projects import router as projects_router
from src.api.routes.integrations import router as integrations_router
from src.application.integrations.integration_catalog_service import IntegrationCatalogService
from test_case_planning import environment


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_DOC_BROWSER") != "1", reason="real Chromium and isolated PG/MySQL/RustFS")
async def test_actual_upload_detail_download_and_immutable_metadata_derivatives(tmp_path):
    async with environment(tmp_path, True) as env:
        source = tmp_path / "订单接口.json"
        source.write_bytes(json.dumps({"openapi": "3.0.1", "info": {"title": "浏览器原件验收", "version": "1"},
            "paths": {"/orders": {"get": {"summary": "查询订单", "responses": {"200": {"description": "成功"}}}}}},
            ensure_ascii=False).encode("utf-8-sig"))
        app = FastAPI()
        app.state.api_docs_service = env.docs
        app.state.project_service = env.projects
        # Actual existing service with an empty catalog inside this test's data_dir.
        app.state.integration_catalog_service = IntegrationCatalogService(settings=env.settings)
        for route in (router, projects_router, integrations_router):
            app.include_router(route, prefix="/api/v1")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        backend_port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        web_root = Path(__file__).resolve().parents[2] / "agent_web"
        evidence = web_root.parent / "docs/upgrade_2026-10-03"
        host = web_root / f"__doc_assets_probe_{uuid4().hex}.html"
        assert not host.exists()
        host.write_text('''<html><head><meta charset="utf-8"></head><body>
<button id="open">打开导入</button><div id="app"></div><script type="module">
import {createApp,h} from 'vue';
import {NMessageProvider} from 'naive-ui';
import ApiDocsPlugin from '/src/features/tools/plugins/ApiDocsPlugin.vue';
import {setLocale,registerLocale} from '/src/services/i18n.ts';
import zhCN from '/src/locales/zh-CN.ts';
registerLocale('zh-CN',zhCN);
setLocale('zh-CN');
createApp({render:()=>h(NMessageProvider,{}, {default:()=>h(ApiDocsPlugin)})}).mount('#app');
document.getElementById('open').onclick=()=>window.dispatchEvent(new Event('qa-agent:open-api-doc-upload'));
</script></body></html>''', encoding="utf-8")
        port_socket = socket.socket()
        port_socket.bind(("127.0.0.1", 0))
        web_port = port_socket.getsockname()[1]
        port_socket.close()
        node = shutil.which("node")
        assert node and (web_root / "node_modules/vite/bin/vite.js").is_file()
        vite = None
        errors = []
        try:
            with (evidence / "p3-original-assets-browser-vite.log").open("wb") as log:
                vite = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port), "--strictPort"],
                    cwd=web_root, env={**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{backend_port}", "API_AUTH_TOKEN": ""},
                    stdout=log, stderr=log)
                url = f"http://127.0.0.1:{web_port}/{host.name}"
                async with httpx.AsyncClient() as client:
                    async with asyncio.timeout(20):
                        while True:
                            if vite.poll() is not None:
                                raise RuntimeError((evidence / "p3-original-assets-browser-vite.log").read_text(encoding="utf-8", errors="replace"))
                            try:
                                if server.started and (await client.get(url)).status_code == 200:
                                    break
                            except httpx.ConnectError:
                                pass
                            await asyncio.sleep(0.1)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        page = await browser.new_page(viewport={"width": 1280, "height": 1000})
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        await page.goto(url)
                        await expect(page.get_by_text("还没有导入任何 API 文档", exact=True)).to_be_visible()
                        await page.get_by_role("button", name="打开导入").click()
                        await page.locator('input[type="file"]').set_input_files(source)
                        async with page.expect_response(lambda response: response.url.endswith("/api-docs/upload")) as uploading:
                            await page.get_by_role("button", name="开始导入", exact=True).click()
                        uploaded = await (await uploading.value).json()
                        await expect(page.locator(".upload-modal")).not_to_be_visible()
                        await page.locator(".api-doc-card .api-doc-actions button").first.click()
                        await expect(page.get_by_role("heading", name="上传原件", exact=True)).to_be_visible()
                        downloads = []

                        async def save_download(index):
                            async with page.expect_download() as downloading:
                                await page.locator(".document-assets").get_by_role("button", name="下载", exact=True).nth(index).click()
                            download = await downloading.value
                            target = tmp_path / f"download-{len(downloads)}"
                            await download.save_as(target)
                            downloads.append((download.suggested_filename, target.read_bytes()))
                            return target.read_bytes()

                        assert await save_download(0) == source.read_bytes()
                        first_markdown = await save_download(1)
                        assert b"GET /orders" in first_markdown
                        for host_url in ("https://first.example.test", "https://second.example.test"):
                            await page.locator(".preview-form-grid label").filter(has_text="项目 URL / Base URL").locator("input").fill(host_url)
                            async with page.expect_response(lambda response: response.request.method == "PATCH") as updating:
                                await page.get_by_role("button", name="保存文档信息", exact=True).click()
                            response = await updating.value
                            assert response.status == 200
                            current = await response.json()
                            await expect(page.locator(".document-assets").get_by_role("button", name="下载", exact=True)).to_have_count(1 + len(current["derived_assets"]))
                        assert await save_download(0) == source.read_bytes()
                        assert await save_download(1) == first_markdown
                        assert b"second.example.test" in await save_download(3)
                        persisted = await env.docs.get_document(uploaded["id"])
                        assert len(persisted.derived_assets) == 3
                        assert persisted.original_asset == (await env.docs._catalog_store.get(uploaded["id"])).original_asset
                        assert not errors, errors
                        (evidence / "p3-original-assets-browser.json").write_text(json.dumps({
                            "source_file": source.name, "original_bytes_equal": True, "old_derived_bytes_equal": True,
                            "derived_assets": len(persisted.derived_assets), "downloads": [name for name, _ in downloads],
                            "page_errors": errors, "aria_snapshot": await page.locator("body").aria_snapshot(),
                            "scope": "actual ApiDocsPlugin in isolated host; real routes/PG/RustFS; full product startup not covered",
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        await page.screenshot(path=str(evidence / "p3-original-assets-browser.png"), full_page=True)
                    finally:
                        await browser.close()
        finally:
            if vite is not None:
                if vite.poll() is None:
                    vite.terminate()
                try:
                    vite.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    vite.kill()
                    vite.wait(timeout=5)
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
            listener.close()
            assert host.parent == web_root
            host.unlink(missing_ok=True)

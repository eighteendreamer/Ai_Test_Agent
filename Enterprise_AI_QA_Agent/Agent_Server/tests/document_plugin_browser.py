"""Actual shipped document plugin, production routes and isolated test catalogs."""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from playwright.async_api import async_playwright

from src.api.routes.api_docs import router
from src.api.routes.projects import router as projects_router
from src.api.routes.integrations import router as integrations_router
from src.application.integrations.integration_catalog_service import IntegrationCatalogService


@asynccontextmanager
async def document_plugin_browser(env, evidence_prefix, *, setup_app=None):
    app = FastAPI()
    app.state.api_docs_service = env.docs
    app.state.project_service = env.projects
    app.state.integration_catalog_service = IntegrationCatalogService(settings=env.settings)
    for route in (router, projects_router, integrations_router):
        app.include_router(route, prefix="/api/v1")
    if setup_app:
        setup_app(app)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    backend_port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    web_root = Path(__file__).resolve().parents[2] / "agent_web"
    evidence = web_root.parent / "docs/upgrade_2026-10-03"
    host = web_root / f"__doc_probe_{uuid4().hex}.html"
    assert not host.exists()
    host.write_text('''<html><head><meta charset="utf-8"></head><body>
<button id="open">打开导入</button><div id="app"></div><script type="module">
import {createApp,h} from 'vue';
import {NMessageProvider} from 'naive-ui';
import ApiDocsPlugin from '/src/features/tools/plugins/ApiDocsPlugin.vue';
import {setLocale,registerLocale} from '/src/services/i18n.ts';
import zhCN from '/src/locales/zh-CN.ts';
import '/src/styles.css';
registerLocale('zh-CN',zhCN);setLocale('zh-CN');
createApp({render:()=>h(NMessageProvider,{}, {default:()=>h(ApiDocsPlugin)})}).mount('#app');
document.getElementById('open').onclick=()=>window.dispatchEvent(new Event('qa-agent:open-api-doc-upload'));
</script></body></html>''', encoding="utf-8")
    port_socket = socket.socket()
    port_socket.bind(("127.0.0.1", 0))
    web_port = port_socket.getsockname()[1]
    port_socket.close()
    node = shutil.which("node")
    assert node and (web_root / "node_modules/vite/bin/vite.js").is_file()
    log_path = evidence / f"{evidence_prefix}-vite.log"
    vite = None
    errors = []
    try:
        with log_path.open("wb") as log:
            vite = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port), "--strictPort"],
                cwd=web_root, env={**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{backend_port}", "API_AUTH_TOKEN": ""},
                stdout=log, stderr=log)
            url = f"http://127.0.0.1:{web_port}/{host.name}"
            async with httpx.AsyncClient() as client:
                async with asyncio.timeout(20):
                    while True:
                        if vite.poll() is not None:
                            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
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
                    yield page, evidence, errors
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

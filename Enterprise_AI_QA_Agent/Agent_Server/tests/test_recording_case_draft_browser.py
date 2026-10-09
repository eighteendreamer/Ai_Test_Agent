"""Shipped Vue components + physical HTTP + real PG, using captured browser events.

The host mounts the real timeline/dialog/Pinia, not the entire production App.
Recorded-event acquisition is covered by test_recording_draft_evidence.py.
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
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright, expect

from src.api.routes.case_management import router as cases_router
from src.api.routes.recordings import router as recordings_router
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.application.recorder.recording_case_draft_service import RecordingCaseDraftService
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.project import ProjectCreateRequest
from src.schemas.recording import RecorderEvent, RecordingSession, RecordingStatus
from test_case_planning import environment


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_CASE_BROWSER") != "1",
                    reason="requires Chromium/Vite and isolated MySQL/PG/RustFS")
async def test_actual_timeline_lost_reply_review_activate_and_reload(tmp_path):
    root = Path(__file__).resolve().parents[2]
    captured = root / "docs/upgrade_2026-10-03/p0-recording-draft-route-http-verified"
    evidence = Path(os.environ["RECORDING_CASE_BROWSER_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="recording-ui-" + uuid4().hex[:12], name="录制草稿真实界面"))
        suffix = uuid4().hex[:12]
        tables = ["up_rcbe_" + suffix, "up_rcbs_" + suffix]
        env.settings.database.postgres_recording_event_table, env.settings.database.postgres_recording_table = tables
        with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
            for table in tables:
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
        store = PostgresRecordingStore(env.settings)
        await store.initialize()
        original = RecordingSession.model_validate_json((captured / "pg-session.json").read_text(encoding="utf-8"))
        session = original.model_copy(update={"id": str(uuid4()), "project_id": project.id,
            "session_id": str(uuid4()), "status": RecordingStatus.active, "step_count": 0})
        await store.create_session(session)
        events = [RecorderEvent.model_validate(value) for value in json.loads((captured / "pg-events.json").read_text(encoding="utf-8"))]
        await store.append_events(session.id, events)
        await store.update_status(session.id, RecordingStatus.completed, finalize_metrics=original.finalize_metrics)
        graph = RecordingGraphStore(env.settings)
        recorder = RecorderSessionService(env.settings, store, graph)
        app = FastAPI()
        app.state.recorder_service = recorder
        app.state.test_case_service = env.cases
        app.state.recording_case_draft_service = RecordingCaseDraftService(recorder_service=recorder, test_case_service=env.cases)
        app.include_router(recordings_router, prefix="/api/v1")
        app.include_router(cases_router, prefix="/api/v1")
        traces = []
        app.state.lose_reply = True

        @app.middleware("http")
        async def trace_and_lose_committed_reply(request, call_next):
            response = await call_next(request)
            if request.method == "POST" and request.url.path.endswith("/case-draft") and response.status_code == 201 and app.state.lose_reply:
                app.state.lose_reply = False
                response = JSONResponse({"detail": "test-owned committed reply lost"}, status_code=503)
            traces.append({"method": request.method, "path": request.url.path, "status": response.status_code})
            return response

        listener = socket.socket(); listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        backend_origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        web_root = root / "agent_web"
        host = web_root / f"__recording_case_probe_{uuid4().hex}.html"
        assert not host.exists()
        host.write_text('''<!doctype html><meta charset="utf-8"><style>body{font-family:sans-serif;padding:24px}#app{width:900px}:root{--surface:#fff;--text:#18212f;--border:#cbd5e1}</style><div id="app"></div><script type="module">
import {createApp} from 'vue'; import {createPinia} from 'pinia';
import {createRouter,createMemoryHistory} from 'vue-router';
import RecordingTimelinePanel from '/src/components/chat/RecordingTimelinePanel.vue';
import {useSessionStore} from '/src/stores/session.ts';
import {registerLocale} from '/src/services/i18n.ts'; import zh from '/src/locales/zh-CN.ts';
registerLocale('zh-CN',zh);
const pinia=createPinia(); const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:{template:'<div></div>'}}]});
const app=createApp(RecordingTimelinePanel);app.use(pinia);app.use(router);
useSessionStore(pinia).session=''' + json.dumps({"id": session.session_id, "project_id": project.id}) + ''';
app.mount('#app');</script>''', encoding="utf-8")
        reserved = socket.socket(); reserved.bind(("127.0.0.1", 0)); web_port = reserved.getsockname()[1]; reserved.close()
        node = shutil.which("node")
        assert node and (web_root / "node_modules/vite/bin/vite.js").is_file()
        vite = None
        browser = None
        page_errors = []
        try:
            with (evidence / "vite.log").open("wb") as log:
                vite = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port)],
                    cwd=web_root, env={**os.environ, "VITE_API_PROXY_TARGET": backend_origin, "API_AUTH_TOKEN": ""}, stdout=log, stderr=log)
                url = f"http://127.0.0.1:{web_port}/{host.name}"
                async with httpx.AsyncClient() as client, asyncio.timeout(20):
                    while True:
                        assert vite.poll() is None, (evidence / "vite.log").read_text(encoding="utf-8", errors="replace")
                        try:
                            if server.started and (await client.get(url)).status_code == 200: break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(.1)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    page = await browser.new_page(viewport={"width": 1280, "height": 900})
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    await page.goto(url)
                    button = page.get_by_test_id("recording-create-case-draft")
                    await expect(button).to_be_enabled()
                    async with page.expect_response(lambda value: value.request.method == "POST" and "/case-draft" in value.url) as lost:
                        await button.click()
                    assert (await lost.value).status == 503
                    await expect(page.get_by_test_id("recording-case-draft-error")).to_contain_text("test-owned committed reply lost")
                    await button.click()
                    link = page.get_by_test_id("recording-case-draft-id")
                    await expect(link).to_be_visible()
                    await expect(button).to_be_disabled()
                    converted = await app.state.recording_case_draft_service.get_created_draft(session.id, project_id=project.id)
                    await expect(link).to_contain_text(converted["case"].id)
                    await link.click()
                    dialog = page.get_by_role("dialog", name="测试用例内容")
                    await expect(dialog).to_be_visible()
                    await dialog.get_by_role("button", name="JSON 原文", exact=True).click()
                    raw = json.loads(await dialog.locator("pre").inner_text())
                    version = converted["version"].model_dump(mode="json")
                    save("case-version.json", version)
                    save("browser-raw.json", raw)
                    # Existing dialog projects historical structured versions
                    # when content_json is absent; do not rewrite the version.
                    expected = version["content_json"] if version["content_json"] is not None else {
                        key: version[key] for key in ("preconditions", "steps", "assertions", "test_data", "cleanup")}
                    assert raw == expected
                    assert any(step["data"]["event"].get("value") == "完整输入😀" * 80 for step in raw["steps"])
                    await dialog.get_by_role("button", name="提交当前版本评审", exact=True).click()
                    await expect(dialog.get_by_role("button", name="启用当前版本", exact=True)).to_be_enabled()
                    async with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/activate")) as activating:
                        await dialog.get_by_role("button", name="启用当前版本", exact=True).click()
                    assert (await activating.value).status == 200
                    await expect(dialog.get_by_text("当前版本：已确认 · 当前启用", exact=True)).to_be_visible()
                    await expect(dialog.get_by_role("button", name="启用当前版本", exact=True)).to_be_disabled()
                    fresh = await env.cases.get_case(converted["case"].id)
                    assert fresh.active_version_id == converted["version"].id and fresh.lifecycle_status == "active"
                    save("case-version.json", converted["version"].model_dump(mode="json"))
                    save("case-reviewed.json", fresh.model_dump(mode="json"))
                    save("browser-before-reload.json", {"snapshot": await page.locator("body").aria_snapshot(), "raw": raw})
                    await page.screenshot(path=str(evidence / "dialog.png"), full_page=True)
                    await page.reload()
                    await expect(page.get_by_test_id("recording-case-draft-id")).to_contain_text(fresh.id)
                    await expect(page.get_by_test_id("recording-create-case-draft")).to_be_disabled()
                    await page.get_by_test_id("recording-case-draft-id").click()
                    await expect(page.get_by_role("dialog", name="测试用例内容").get_by_role("button", name="启用当前版本", exact=True)).to_be_disabled()
                    await page.get_by_role("dialog", name="测试用例内容").get_by_role("button", name="关闭", exact=True).click()
                    await page.screenshot(path=str(evidence / "timeline.png"), full_page=True)
                    cases = await env.cases.list_cases(project_id=project.id, status=None, mode_key=None, priority=None, query=None, limit=50, offset=0)
                    assert len(cases.items) == 1 and len(await env.cases.list_versions(fresh.id)) == 1
                    posts = [trace for trace in traces if trace["method"] == "POST" and trace["path"].endswith("/case-draft")]
                    assert len(posts) == 1 and posts[0]["status"] == 503
                    assert not page_errors, page_errors
                    save("browser-summary.json", {"case_id": fresh.id, "version_id": fresh.active_version_id,
                        "cases": 1, "versions": 1, "conversion_post_count": 1, "lost_reply_recovered": True,
                        "review_activate_actual_http": True, "reload_no_post": True, "page_errors": page_errors,
                        "scope": "real timeline/dialog/Pinia in isolated host; captured browser events loaded to owned PG; full App/main/recording acquisition not repeated"})
                    await browser.close(); browser = None
        finally:
            save("server-http.json", traces)
            save("page-errors.json", page_errors)
            if browser is not None: await browser.close()
            if vite is not None:
                if vite.poll() is None: vite.terminate()
                try: await asyncio.to_thread(vite.wait, timeout=5)
                except subprocess.TimeoutExpired: vite.kill(); await asyncio.to_thread(vite.wait, timeout=5)
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
            listener.close()
            assert host.parent == web_root
            host.unlink(missing_ok=True)
            await recorder.close(); await graph.close()
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in tables: cursor.execute(f'DROP TABLE "{table}"')

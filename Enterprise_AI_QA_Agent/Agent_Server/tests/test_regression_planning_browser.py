"""Actual regression dialog, HTTP routes and isolated storage; controlled model."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from uuid import uuid4
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright, expect

from test_regression_planning_tasks import regression_environment
from test_case_planning_tasks import delivery
from test_case_planning import finish
from src.api.routes.case_planning_tasks import router as task_router
from src.api.routes.case_plans import router as plan_router
from src.api.routes.run_management import router as run_router
from src.registry.modes import ModeRegistry
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.schemas.case_planning_task import CasePlanningTaskRecord
from src.api.routes.projects import router as projects_router
from src.api.routes.case_management import router as cases_router
from src.application.projects.project_overview_service import ProjectOverviewService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_session_store import PostgresSessionStore


@asynccontextmanager
async def project_routes(app, env):
    fields = ["postgres_session_table", "postgres_message_table", "postgres_event_table", "postgres_snapshot_table", "postgres_approval_table"]
    tables = ["up_gr_browser_" + uuid4().hex[:12] for _ in fields]
    for field, table in zip(fields, tables):
        setattr(env.settings.database, field, table)
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            assert cur.fetchone()["existing"] is None
    sessions = PostgresSessionStore(env.settings)
    try:
        await sessions.initialize()
        app.state.project_service, app.state.test_case_service = env.projects, env.cases
        app.state.project_overview_service = ProjectOverviewService(project_service=env.projects,
            api_doc_store=env.docs._catalog_store, session_store=sessions,
            knowledge_graph_service=None, test_case_store=env.cases._store,
            test_suite_store=env.creator._suites, test_run_store=env.run_store)
        for router in (projects_router, cases_router): app.include_router(router, prefix="/api/v1")
        @app.get("/api/v1/registry/modes")
        async def modes(): return ModeRegistry().list()
        @app.get("/api/v1/projects/{project_id}/resource-quotas")
        @app.get("/api/v1/resources/occupancy")
        async def optional_monitor(): raise HTTPException(503, "Optional monitor not configured in regression fixture")
        yield
    finally:
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in reversed(tables): cur.execute(f'DROP TABLE IF EXISTS "{table}"')


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["dialog", "project"])
@pytest.mark.skipif(os.getenv("RUN_LIVE_REGRESSION_BROWSER") != "1", reason="requires actual Chromium and isolated MySQL/PostgreSQL/RustFS")
async def test_browser_regression_version_selection_lost_response_refresh_and_launch(tmp_path, entry):
    async with regression_environment(tmp_path) as env, AsyncExitStack() as stack:
        def decide(context):
            assert context["constraints"]["required_case_versions"] == {env.failure.case_id: env.version.id}
            doc = next(entry for entry in context["evidence"] if entry["kind"] == "document")
            case = next(entry for entry in context["evidence"] if entry["kind"] == "case")
            return finish({"key": "prepare-new", "kind": "prepare", "reason": "依据实际原文补全",
                "evidence_ids": [doc["id"]], "instructions": {"action": "POST /fixtures", "output": "body.new_id"}},
                {"key": "target", "kind": "target", "case_id": env.failure.case_id, "case_version_id": env.version.id,
                    "reason": "固定原失败的新版本", "evidence_ids": [case["id"]], "depends_on": ["prepare-new"],
                    "input_bindings": {"id": {"source_item": "prepare-new", "output_path_tokens": ["body", "new_id"]}}})
        env.service._models.replies = [{"action": "query", "summary": "读取变更依据",
            "queries": [{"kind": "document", "source_id": env.document.id}]}, decide]
        app = FastAPI()
        app.state.test_run_service, app.state.case_planning_task_service = env.runs, env.tasks
        app.state.case_planning_service, app.state.case_plan_run_service = env.service, env.creator
        for router in (run_router, task_router, plan_router): app.include_router(router, prefix="/api/v1")
        if entry == "project": await stack.enter_async_context(project_routes(app, env))
        listener = socket.socket(); listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        web_root = Path(__file__).resolve().parents[2] / "agent_web"
        host = web_root / f"__regression_probe_{uuid4().hex}.html"
        selection = {"parentRunId": env.parent.run.id, "resultIds": [env.failure.id], "versionOptions": [{
            "caseId": env.failure.case_id, "title": "变更后查询", "originalVersionId": env.failure.case_version_id,
            "activeVersionId": env.version.id}]}
        props = {"projectId": env.parent.run.project_id, "candidateCaseIds": [], "regression": selection,
            "modes": [mode.model_dump(mode="json") for mode in ModeRegistry().list()]}
        assert not host.exists()
        host.write_text('''<html><head><meta charset="utf-8"><style>:root{--surface:#fff;--surface-muted:#f5f7fa;--text:#18212f;--border:#cbd5e1}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp } from 'vue';
import CasePlanDialog from '/src/features/projects/CasePlanDialog.vue';
createApp(CasePlanDialog, { ...''' + json.dumps(props, ensure_ascii=False) + ''', onStarted: detail => { document.body.dataset.openedRun = detail.run.id; } }).mount('#app');
</script></body></html>''', encoding="utf-8")
        if entry == "project":
            host.write_text('''<html><head><meta charset="utf-8"><style>:root{--bg:#fff;--surface:#fff;--surface-muted:#f5f7fa;--text:#18212f;--border:#cbd5e1}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp, h } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { NMessageProvider } from 'naive-ui';
import ProjectsView from '/src/views/ProjectsView.vue';
import { registerLocale } from '/src/services/i18n';
import zh from '/src/locales/zh-CN';
registerLocale('zh-CN',zh);
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/projects',name:'projects',component:ProjectsView},
{path:'/projects/:projectId',name:'project-detail',component:ProjectsView}]});
await router.push('/projects/''' + env.parent.run.project_id + '''');
createApp({render:()=>h(NMessageProvider,null,{default:()=>h(RouterView)})}).use(router).mount('#app');
</script></body></html>''', encoding="utf-8")
        web_socket = socket.socket(); web_socket.bind(("127.0.0.1", 0)); web_port = web_socket.getsockname()[1]; web_socket.close()
        node = shutil.which("node"); assert node
        process = None
        errors = []
        evidence_dir = web_root.parent / "docs/upgrade_2026-10-03"
        base = os.getenv("QA_REGRESSION_BROWSER_EVIDENCE", "p2-regression-planning-browser-entries") + "-" + entry
        try:
            with (tmp_path / "vite.log").open("wb") as log:
                process = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port)],
                    cwd=web_root, env={**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{listener.getsockname()[1]}", "API_AUTH_TOKEN": "", "VITE_PROJECT_MONITOR_POLL_MS": "0"},
                    stdout=log, stderr=log)
                url = f"http://127.0.0.1:{web_port}/{host.name}"
                async with httpx.AsyncClient() as client, asyncio.timeout(20):
                    while True:
                        if process.poll() is not None: raise RuntimeError((tmp_path / "vite.log").read_text(encoding="utf-8", errors="replace"))
                        try:
                            if server.started and (await client.get(url)).status_code == 200: break
                        except httpx.ConnectError: pass
                        await asyncio.sleep(.1)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    page = await browser.new_page(viewport={"width": 1280, "height": 900})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    captured = []
                    try:
                        await page.goto(url)
                        if entry == "project":
                            await page.get_by_role("button", name="测试历史", exact=True).click()
                            await page.get_by_role("button", name="失败项回归", exact=True).click()
                            await expect(page.get_by_role("dialog", name="用例执行规划")).to_be_visible()
                            await expect(page.get_by_role("checkbox")).to_have_count(1)
                            assert env.service._models.requests == []
                            await page.get_by_role("dialog").get_by_role("button", name="关闭", exact=True).click()
                            await page.get_by_role("button", name="回归中心", exact=True).click()
                            await page.get_by_role("checkbox").check()
                            await page.get_by_role("button", name="所选失败项回归（1）", exact=True).click()
                        await expect(page.get_by_role("button", name="提交回归规划", exact=True)).to_be_enabled()
                        await page.get_by_role("dialog", name="用例执行规划").get_by_role("checkbox").check()
                        async def lose_response(route):
                            response = await route.fetch(); assert response.status == 202
                            captured.append(await response.json()); await route.abort("failed")
                        await page.route("**/regression-planning-tasks", lose_response, times=1)
                        await page.get_by_role("button", name="提交回归规划", exact=True).click()
                        await expect(page.get_by_role("button", name="重试规划提交", exact=True)).to_be_visible()
                        assert len(captured) == 1
                        task = CasePlanningTaskRecord.model_validate(captured[0])
                        await page.get_by_role("button", name="查询规划任务", exact=True).click()
                        await expect(page.get_by_role("status").filter(has_text="规划已排队")).to_be_visible()
                        await PlanningTaskHandler(env.service, env.jobs, worker_id="browser-regression")(delivery(task))
                        await expect(page.get_by_role("heading", name="计划已生成", exact=True)).to_be_visible()
                        await page.get_by_text("完整固定用例", exact=True).click()
                        await expect(page.get_by_text("末尾验证点😀")).to_be_visible()
                        if entry == "dialog":
                            await page.reload()
                            await expect(page.get_by_role("heading", name="计划已生成", exact=True)).to_be_visible()
                            await page.get_by_text("完整固定用例", exact=True).click()
                            await expect(page.get_by_text("末尾验证点😀")).to_be_visible()
                        await expect(page.get_by_label("执行测试模式")).to_have_value("api_testing")
                        await expect(page.get_by_label("执行测试模式")).to_be_disabled()
                        async with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/runs")) as launching:
                            await page.get_by_role("button", name="启动测试运行", exact=True).click()
                        launch = await launching.value; assert launch.status == 201
                        detail = await launch.json()
                        assert detail["run"]["run_kind"] == "regression" and detail["run"]["parent_run_id"] == env.parent.run.id
                        assert detail["items"][0]["case_version_id"] == env.version.id
                        assert detail["items"][0]["regression_source_result_id"] == env.failure.id
                        if entry == "dialog":
                            await expect(page.locator("body")).to_have_attribute("data-opened-run", detail["run"]["id"])
                        else:
                            await expect(page.get_by_role("dialog", name="用例执行规划")).not_to_be_visible()
                            await expect(page.get_by_text(detail["run"]["id"][:8], exact=False).first).to_be_visible()
                        assert await env.run_store.get_run(env.parent.run.id) == env.original
                        assert len(env.service._models.requests) == 2 and not errors
                        (evidence_dir / f"{base}.json").write_text(json.dumps({"scope": "actual dialog/ProjectsView/HTTP/MySQL/PG/RustFS, controlled model and in-process PlanningTaskHandler; optional resource monitors unavailable, not full production lifespan or target Agent execution",
                            "entry": entry, "lost_response_same_task": task.id, "refresh_kept_replacement": entry == "dialog",
                            "run_id": detail["run"]["id"], "parent_run_id": env.parent.run.id,
                            "source_result_id": env.failure.id, "version_id": env.version.id,
                            "original_failure_unchanged": True, "page_errors": errors,
                            "snapshot": await page.locator("body").aria_snapshot()}, ensure_ascii=False, indent=2), encoding="utf-8")
                        await page.screenshot(path=str(evidence_dir / f"{base}.png"), full_page=True)
                    except BaseException:
                        await page.screenshot(path=str(evidence_dir / f"{base}.png"), full_page=True)
                        (evidence_dir / f"{base}.json").write_text(json.dumps({"page_errors": errors, "captured_tasks": captured,
                            "snapshot": await page.locator("body").aria_snapshot()}, ensure_ascii=False, indent=2), encoding="utf-8")
                        raise
                    finally: await browser.close()
        finally:
            if process is not None:
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            server.should_exit = True; await asyncio.wait_for(serving, timeout=10); listener.close()
            assert host.parent == web_root
            host.unlink(missing_ok=True)

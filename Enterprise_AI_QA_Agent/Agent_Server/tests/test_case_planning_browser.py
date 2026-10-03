"""Shipped planning dialog + real HTTP and isolated MySQL/PG/RustFS.

Model decisions are controlled here; real semantic model validation is separate.
This component host does not validate the complete production lifespan.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.async_api import async_playwright, expect

from test_case_planning import SequenceModel, active_case, item, finish
from src.api.routes.case_plans import router
from src.schemas.project import ProjectCreateRequest
from test_run_item_dependencies import setup
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.registry.modes import ModeRegistry
from src.api.routes.run_management import router as run_router
from src.application.test_runs.run_service import TestRunService as RunService


@asynccontextmanager
async def launch_service(env, run_store):
    table = "up_browser_launch_" + uuid4().hex[:12]
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS existing", (table,))
        assert cur.fetchone()["existing"] is None
    env.settings.database.postgres_task_outbox_table = table
    try:
        await PostgresTaskOutbox(env.settings).initialize()
        yield CasePlanRunService(run_store=run_store, suite_store=PostgresTestSuiteStore(env.settings),
            plan_store=env.store, case_service=env.cases, project_service=env.projects,
            mode_registry=ModeRegistry(), outbox_table=table, dispatch_stream="isolated:browser-plan")
    finally:
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS "{table}"')


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_PLANNING_BROWSER") != "1", reason="requires installed Chromium and real isolated MySQL/PostgreSQL/RustFS")
async def test_browser_existing_data_preparation_policy_and_fixed_plan_reopen(tmp_path):
    def decide(context):
        if context["runtime_context"].get("id"):
            return finish(item(context, "positive", input_bindings={"id": {"input_key": "id"}}), item(context, "negative"))
        if not any(e["kind"] == "document" for e in context["evidence"]):
            return {"action": "query", "summary": "读取实际准备方法", "queries": [{"kind": "document", "source_id": context["catalog"]["documents"][0]["id"]}]}
        doc = next(e for e in context["evidence"] if e["kind"] == "document")
        return finish(item(context, "positive", depends_on=["prepare"], input_bindings={"id": {"source_item": "prepare", "output_path": "body.id"}}),
            item(context, "negative"), {"key": "prepare", "kind": "prepare", "reason": "按项目原文补全，非正式用例",
                "evidence_ids": [doc["id"]], "instructions": {"action": "POST /fixtures", "output": "body.id", "expected": 201}})
    model = SequenceModel(decide, decide, decide)
    async with setup(tmp_path, live=True) as (env, run_store, _, _, _), launch_service(env, run_store) as creator:
        env.service._models = model
        project = await env.projects.create(ProjectCreateRequest(project_key="browser-plan", name="浏览器规划验收"))
        other = await env.projects.create(ProjectCreateRequest(project_key="other-plan", name="隔离项目"))
        positive = await active_case(env, project, "positive", {"objective": "验证已有数据", "steps": [{"order": i, "action": f"步骤{i}"} for i in range(1, 7)] + [{"order": 7, "action": "末尾关键步骤😀", "requires": "id"}]})
        negative = await active_case(env, project, "negative", {"objective": "未登录拒绝", "precondition": "未登录", "expected": 401})
        document = tmp_path / "requirements.md"
        document.write_text("# 实际准备方法\nPOST /fixtures 创建测试数据，响应 body.id。\n", encoding="utf-8")
        await env.docs.upload_document(filename=document.name, content_base64=base64.b64encode(document.read_bytes()).decode(), project_id=project.id)
        app = FastAPI(); app.state.case_planning_service = env.service
        app.state.case_plan_run_service = creator; app.include_router(router, prefix="/api/v1")
        app.state.test_run_service = RunService(store=run_store, project_service=env.projects,
            suite_service=None, test_case_service=env.cases)
        app.include_router(run_router, prefix="/api/v1")
        listener = socket.socket(); listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        web_root = Path(__file__).resolve().parents[2] / "agent_web"
        host = web_root / f"__case_plan_probe_{uuid4().hex}.html"
        assert not host.exists()
        host.write_text('''<html><head><meta charset="utf-8"><style>:root{--surface:#fff;--surface-muted:#f5f7fa;--text:#18212f;--border:#cbd5e1}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp } from 'vue';
import CasePlanDialog from '/src/features/projects/CasePlanDialog.vue';
createApp(CasePlanDialog, { projectId: ''' + json.dumps(project.id) + ''', candidateCaseIds: ''' + json.dumps([positive.case.id, negative.case.id]) + ''', modes: ''' + json.dumps([mode.model_dump(mode="json") for mode in ModeRegistry().list()]) + ''', onStarted: detail => { document.body.dataset.openedRun = detail.run.id; } }).mount('#app');
</script></body></html>''', encoding="utf-8")
        port_socket = socket.socket(); port_socket.bind(("127.0.0.1", 0)); web_port = port_socket.getsockname()[1]; port_socket.close()
        node = shutil.which("node"); assert node
        process = None
        try:
            with (tmp_path / "vite.log").open("wb") as log:
                process = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--port", str(web_port)], cwd=web_root,
                    env={**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{listener.getsockname()[1]}", "API_AUTH_TOKEN": ""}, stdout=log, stderr=log)
                url = f"http://127.0.0.1:{web_port}/{host.name}"
                async with httpx.AsyncClient() as client:
                    async with asyncio.timeout(20):
                        while True:
                            if process.poll() is not None: raise RuntimeError((tmp_path / "vite.log").read_text(encoding="utf-8", errors="replace"))
                            try:
                                if server.started and (await client.get(url)).status_code == 200: break
                            except httpx.ConnectError: pass
                            await asyncio.sleep(.1)
                errors = []
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        page = await browser.new_page(viewport={"width": 1280, "height": 900})
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        await page.goto(url)
                        before = await page.locator("body").aria_snapshot()
                        await expect(page.get_by_label("测试目标")).to_be_enabled()
                        await page.get_by_label("测试目标").fill("验证已有数据与未登录拒绝")
                        await page.get_by_label("已有运行信息（JSON）").fill('{"id":"owned-existing"}')
                        async with page.expect_response(lambda r: r.url.endswith("/case-plans") and r.request.method == "POST") as creating:
                            await page.get_by_role("button", name="生成执行计划", exact=True).click()
                        first = await (await creating.value).json()
                        await expect(page.get_by_role("heading", name="计划已生成", exact=True)).to_be_visible()
                        assert [i["kind"] for i in first["decision"]["items"]] == ["target", "target"]
                        await page.get_by_text("完整固定用例", exact=True).first.click()
                        await expect(page.locator(".json-leaf").filter(has_text="末尾关键步骤😀")).to_be_visible()
                        await page.get_by_text("规划设置", exact=True).click()
                        await page.get_by_label("最多执行项").fill("12")
                        await page.get_by_role("button", name="保存项目规划设置").click()
                        await expect(page.get_by_text("项目规划设置已保存", exact=True)).to_be_visible()
                        assert env.configs.get("test_case.planning_policy", scope_key=project.id).payload["max_items"] == 12
                        await page.get_by_label("已有运行信息（JSON）").fill("{}")
                        async with page.expect_response(lambda r: r.url.endswith("/case-plans") and r.request.method == "POST") as preparing:
                            await page.get_by_role("button", name="生成执行计划", exact=True).click()
                        prepared = await (await preparing.value).json()
                        await expect(page.get_by_role("heading", name="运行内准备 · prepare")).to_be_visible()
                        ordered = [item["key"] for item in prepared["decision"]["items"]]
                        assert ordered.index("prepare") < ordered.index("positive")
                        assert next(i for i in prepared["decision"]["items"] if i["key"] == "negative")["depends_on"] == []
                        assert (await env.service.get(project.id, prepared["id"])).config_snapshot.policy.max_items == 12
                        await page.get_by_label("已保存的计划编号").fill(first["id"])
                        await page.get_by_role("button", name="查看已保存计划").click()
                        await expect(page.get_by_role("heading", name="运行内准备 · prepare")).to_have_count(0)
                        assert (await env.service.get(project.id, first["id"])).config_snapshot.policy.max_items == 50
                        denied = await page.request.get(f"http://127.0.0.1:{web_port}/api/v1/projects/{other.id}/case-plans/{first['id']}")
                        assert denied.status == 404
                        await page.get_by_label("执行测试模式").select_option("code_review")
                        lost_request, committed = [], []
                        async def lose_reply(route):
                            lost_request.append(route.request.post_data_json)
                            response = await route.fetch()
                            assert response.status == 201
                            committed.append(await response.json())
                            await route.abort("failed")
                        await page.route("**/case-plans/*/runs", lose_reply)
                        await page.get_by_role("button", name="启动测试运行", exact=True).click()
                        await expect(page.get_by_text("启动结果尚未确认。重试将保留相同请求和执行选项，不另建运行。", exact=True)).to_be_visible()
                        await page.unroute("**/case-plans/*/runs", lose_reply)
                        assert len(committed) == 1
                        stored_run_id = committed[0]["run"]["id"]
                        assert (await run_store.get_run(stored_run_id)).run.status == "queued"
                        # Reload the real browser page, then reopen the saved plan.
                        await page.reload()
                        await page.get_by_label("已保存的计划编号").fill(first["id"])
                        await page.get_by_role("button", name="查看已保存计划", exact=True).click()
                        await expect(page.get_by_label("执行测试模式")).to_be_disabled()
                        async with page.expect_response(lambda r: r.url.endswith("/runs") and r.request.method == "POST") as retrying:
                            await page.get_by_role("button", name="重试启动请求", exact=True).click()
                        replay_response = await retrying.value
                        replay = await replay_response.json()
                        assert replay_response.request.post_data_json == lost_request[0]
                        assert replay["run"]["id"] == stored_run_id
                        assert replay["run"]["mode_key"] == "code_review"
                        assert replay["run"]["plan_snapshot"]["plan"]["id"] == first["id"]
                        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {creator._outbox_table}")
                            assert cur.fetchone()["n"] == 1
                        await page.get_by_role("button", name="另建一次运行", exact=True).click()
                        async with page.expect_response(lambda r: r.url.endswith("/runs") and r.request.method == "POST") as again:
                            await page.get_by_role("button", name="启动测试运行", exact=True).click()
                        fresh = await (await again.value).json()
                        assert fresh["run"]["id"] != stored_run_id
                        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {creator._outbox_table}")
                            assert cur.fetchone()["n"] == 2
                        await page.reload()
                        await page.get_by_label("已保存的计划编号").fill(first["id"])
                        await page.get_by_role("button", name="查看已保存计划", exact=True).click()
                        async with page.expect_response(lambda r: r.url.endswith("/runs/" + fresh["run"]["id"]) and r.request.method == "GET") as viewing:
                            await page.get_by_role("button", name="查看运行", exact=True).click()
                        viewed = await (await viewing.value).json()
                        assert viewed["run"]["id"] == fresh["run"]["id"]
                        await expect(page.locator("body")).to_have_attribute("data-opened-run", fresh["run"]["id"])
                        assert not errors, errors
                        evidence = web_root.parent / "docs/upgrade_2026-10-03"
                        (evidence / "p2-public-launch-browser.json").write_text(json.dumps({
                            "scope": "actual dialog/HTTP/MySQL/PG/RustFS, lost committed response and browser reload; controlled planning model; no Worker or production lifespan",
                            "snapshot_before": before, "snapshot_after": await page.locator("body").aria_snapshot(),
                            "existing_data_no_extra_preparation": True, "run_local_preparation": True, "negative_condition_preserved": True,
                            "full_seventh_step_visible": True, "project_policy_in_mysql": True, "frozen_plan_reopened": True,
                            "foreign_project_denied": True, "page_errors": errors,
                            "lost_response_run_id": stored_run_id, "retry_same_request": True,
                            "outbox_count_after_retry": 1, "explicit_new_run_id": fresh["run"]["id"], "outbox_count_after_new_run": 2,
                            "persisted_run_reopened_via_real_http": True,
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        await page.screenshot(path=str(evidence / "p2-public-launch-browser.png"), full_page=True)
                    finally:
                        await browser.close()
        finally:
            if process is not None:
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            server.should_exit = True; await asyncio.wait_for(serving, timeout=10); listener.close()
            assert host.parent == web_root
            host.unlink(missing_ok=True)

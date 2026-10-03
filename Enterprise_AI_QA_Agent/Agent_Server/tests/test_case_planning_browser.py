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

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.async_api import async_playwright, expect

from test_case_planning import environment, SequenceModel, active_case, item, finish
from src.api.routes.case_plans import router
from src.schemas.project import ProjectCreateRequest


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
    async with environment(tmp_path, live=True, model=model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="browser-plan", name="浏览器规划验收"))
        other = await env.projects.create(ProjectCreateRequest(project_key="other-plan", name="隔离项目"))
        positive = await active_case(env, project, "positive", {"objective": "验证已有数据", "steps": [{"order": i, "action": f"步骤{i}"} for i in range(1, 7)] + [{"order": 7, "action": "末尾关键步骤😀", "requires": "id"}]})
        negative = await active_case(env, project, "negative", {"objective": "未登录拒绝", "precondition": "未登录", "expected": 401})
        document = tmp_path / "requirements.md"
        document.write_text("# 实际准备方法\nPOST /fixtures 创建测试数据，响应 body.id。\n", encoding="utf-8")
        await env.docs.upload_document(filename=document.name, content_base64=base64.b64encode(document.read_bytes()).decode(), project_id=project.id)
        app = FastAPI(); app.state.case_planning_service = env.service; app.include_router(router, prefix="/api/v1")
        listener = socket.socket(); listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        web_root = Path(__file__).resolve().parents[2] / "agent_web"
        host = web_root / f"__case_plan_probe_{uuid4().hex}.html"
        assert not host.exists()
        host.write_text('''<html><head><meta charset="utf-8"><style>:root{--surface:#fff;--surface-muted:#f5f7fa;--text:#18212f;--border:#cbd5e1}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp } from 'vue';
import CasePlanDialog from '/src/features/projects/CasePlanDialog.vue';
createApp(CasePlanDialog, { projectId: ''' + json.dumps(project.id) + ''', candidateCaseIds: ''' + json.dumps([positive.case.id, negative.case.id]) + ''' }).mount('#app');
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
                        assert not errors, errors
                        evidence = web_root.parent / "docs/upgrade_2026-10-03"
                        (evidence / "p2-planning-browser.json").write_text(json.dumps({
                            "scope": "actual dialog/HTTP/MySQL/PG/RustFS with controlled model; not production lifespan or Agent execution",
                            "snapshot_before": before, "snapshot_after": await page.locator("body").aria_snapshot(),
                            "existing_data_no_extra_preparation": True, "run_local_preparation": True, "negative_condition_preserved": True,
                            "full_seventh_step_visible": True, "project_policy_in_mysql": True, "frozen_plan_reopened": True,
                            "foreign_project_denied": True, "page_errors": errors,
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        await page.screenshot(path=str(evidence / "p2-planning-browser.png"), full_page=True)
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

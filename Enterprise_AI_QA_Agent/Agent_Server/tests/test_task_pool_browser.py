"""Actual TaskPoolView -> ProjectsView -> exact persisted task/plan, on real storage.

The API host exposes production routes with isolated providers. It does not run
the entire API lifespan or validate optional resource monitors/external models.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright, expect

from test_task_pool_entries import pool_environment, session
from test_case_planning_tasks import active_case, item, finish, delivery
from src.api.routes.task_pool import router as pool_router
from src.api.routes.projects import router as projects_router
from src.api.routes.case_management import router as cases_router
from src.api.routes.case_plans import router as plans_router
from src.api.routes.case_planning_tasks import router as tasks_router
from src.application.projects.project_overview_service import ProjectOverviewService
from src.registry.modes import ModeRegistry
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.project import ProjectCreateRequest
from uuid import uuid4


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_TASK_POOL_BROWSER') != '1',
    reason='requires installed Chromium and isolated real PostgreSQL/MySQL/RustFS')
async def test_browser_pool_pages_progress_failure_and_exact_historical_plan(tmp_path):
    async with pool_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='pool-browser', name='任务池浏览器项目'))
        source = tmp_path / 'browser-requirements.md'
        source.write_text('# 需求\n未登录查询订单应拒绝。\n最后一步必须保留中文😀证据。\n', encoding='utf-8')
        document = await env.docs.upload_document(project_id=project.id, filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        case = await active_case(env, project, 'browser-pool', {'objective': '历史计划目标😀',
            'steps': [{'action': '未登录查询订单', 'expected': '拒绝'}, {'action': '末尾验证😀', 'expected': '保存真实证据'}]})
        chosen = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key='chosen-history',
            objective='核对历史目标', candidate_case_ids=[case.case.id]))
        latest = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key='latest-request', objective='另一目标'))
        for index in range(25):
            await session(env, project, f'browser-review-{index:02}', updated_at=latest.updated_at + timedelta(seconds=index + 1))
        app = FastAPI()
        app.state.task_pool_service = env.pool; app.state.project_service = env.projects
        app.state.case_planning_service = env.service; app.state.case_planning_task_service = env.tasks
        app.state.test_case_service = env.cases
        app.state.project_overview_service = ProjectOverviewService(project_service=env.projects,
            api_doc_store=env.docs._catalog_store, session_store=env.sessions,
            knowledge_graph_service=None, test_case_store=env.cases._store)
        for router in [pool_router, projects_router, cases_router, plans_router, tasks_router]:
            app.include_router(router, prefix='/api/v1')
        @app.get('/api/v1/registry/modes')
        async def modes(): return ModeRegistry().list()
        # These monitors are deliberately unavailable in this scoped task-pool host.
        # Do not populate invented resource/run counters to disguise missing providers.
        @app.get('/api/v1/projects/{project_id}/execution-summary')
        @app.get('/api/v1/projects/{project_id}/resource-quotas')
        @app.get('/api/v1/resources/occupancy')
        async def optional_monitor(): raise HTTPException(503, 'Optional monitor not configured in task-pool fixture')

        listener = socket.socket(); listener.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', timeout_graceful_shutdown=5))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        web_root = Path(__file__).resolve().parents[2] / 'agent_web'
        host = web_root / f'__task_pool_probe_{uuid4().hex}.html'
        assert not host.exists()
        host.write_text('''<html><head><meta charset="utf-8"><style>:root{--bg:#fff;--surface:#fff;--surface-muted:#f5f7fa;--text:#18212f;--border:#cbd5e1}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp, h } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { NMessageProvider } from 'naive-ui';
import TaskPoolView from '/src/views/TaskPoolView.vue';
import ProjectsView from '/src/views/ProjectsView.vue';
import { registerLocale } from '/src/services/i18n';
import zh from '/src/locales/zh-CN';
registerLocale('zh-CN',zh);
const router = createRouter({history:createMemoryHistory(),routes:[
{path:'/taskpool',component:TaskPoolView},{path:'/projects',name:'projects',component:ProjectsView},
{path:'/projects/:projectId',name:'project-detail',component:ProjectsView}]});
sessionStorage.setItem('case-planning-task:''' + project.id + '''',JSON.stringify({creationKey:'latest-request',taskId:''' + json.dumps(latest.id) + '''}));
await router.push('/taskpool');
window.taskProbe={router};
createApp({render:()=>h(NMessageProvider,null,{default:()=>h(RouterView)})}).use(router).mount('#app');
</script></body></html>''', encoding='utf-8')
        port_socket = socket.socket(); port_socket.bind(('127.0.0.1', 0)); web_port = port_socket.getsockname()[1]; port_socket.close()
        node = shutil.which('node'); assert node
        process = None
        try:
            with (tmp_path / 'vite.log').open('wb') as log:
                process = subprocess.Popen([node, str(web_root / 'node_modules/vite/bin/vite.js'), '--port', str(web_port)], cwd=web_root,
                    env={**os.environ, 'VITE_API_PROXY_TARGET': f'http://127.0.0.1:{listener.getsockname()[1]}',
                         'API_AUTH_TOKEN': '', 'VITE_PROJECT_MONITOR_POLL_MS': '0'}, stdout=log, stderr=log)
                url = f'http://127.0.0.1:{web_port}/{host.name}'
                async with httpx.AsyncClient() as client:
                    async with asyncio.timeout(20):
                        while True:
                            if process.poll() is not None: raise RuntimeError((tmp_path / 'vite.log').read_text(encoding='utf-8', errors='replace'))
                            try:
                                if server.started and (await client.get(url)).status_code == 200: break
                            except httpx.ConnectError: pass
                            await asyncio.sleep(.1)
                errors, requests = [], []
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        page = await browser.new_page(viewport={'width': 1400, 'height': 1000})
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.on('request', lambda request: requests.append((request.method, request.url)))
                        await page.goto(url)
                        await expect(page.locator('tbody tr')).to_have_count(24)
                        await page.get_by_role('button', name='下一页', exact=True).click()
                        await expect(page.locator('tbody tr')).to_have_count(3)
                        await expect(page.locator(f'[data-task-id="{chosen.id}"]')).to_contain_text('已排队')
                        await page.get_by_label('任务类型', exact=True).select_option('case_planning')
                        await expect(page.locator('tbody tr')).to_have_count(2)

                        env.model.replies = [ {'action': 'query', 'summary': '读取原需求',
                            'queries': [{'kind': 'document', 'source_id': document.id}]},
                            lambda context: finish(item(context, 'browser-pool')) ]
                        await PlanningTaskHandler(env.service, env.jobs, worker_id='pool-browser')(delivery(chosen))
                        await expect(page.locator(f'[data-task-id="{chosen.id}"]')).to_contain_text('规划完成', timeout=12000)
                        await expect(page.locator(f'[data-task-id="{chosen.id}"]')).to_contain_text('已调用回合 2')
                        env.model.replies = [RuntimeError('controlled browser planning failure')]
                        await PlanningTaskHandler(env.service, env.jobs, worker_id='pool-browser')(delivery(latest))
                        await page.get_by_role('button', name='失败', exact=True).click()
                        await expect(page.locator('tbody tr')).to_have_count(1)
                        await expect(page.locator(f'[data-task-id="{latest.id}"]')).to_contain_text('失败')
                        await page.get_by_role('button', name='全部', exact=True).click()
                        await expect(page.locator('tbody tr')).to_have_count(2)
                        await page.locator(f'[data-task-id="{chosen.id}"]').get_by_role('button', name='查看规划任务').click()
                        await expect(page.get_by_role('dialog', name='用例执行规划')).to_be_visible()
                        await expect(page.get_by_role('heading', name='计划已生成', exact=True)).to_be_visible()
                        await expect(page.get_by_role('heading', name='目标用例 · 历史计划目标😀', exact=True)).to_be_visible()
                        chosen_record = await env.tasks.get(project.id, chosen.id)
                        assert await page.get_by_label('已保存的计划编号').input_value() == chosen_record.plan_id
                        assert await page.evaluate('window.taskProbe.router.currentRoute.value.query.planning_task_id') == chosen.id
                        await page.get_by_text('完整固定用例', exact=True).click()
                        await expect(page.locator('.json-leaf').filter(has_text='末尾验证😀')).to_be_visible()
                        saved = await env.service.get(project.id, chosen_record.plan_id)
                        assert any(e.kind == 'document' and e.source_id == document.id for e in saved.evidence)
                        assert not any(method == 'POST' for method, _ in requests)
                        assert not errors, errors
                        evidence = Path(__file__).resolve().parents[2] / 'docs/upgrade_2026-10-03'
                        await page.screenshot(path=str(evidence / 'p2-task-pool-browser.png'), full_page=True)
                        (evidence / 'p2-task-pool-browser.json').write_text(json.dumps({
                            'project_id': project.id, 'chosen_task_id': chosen.id, 'latest_task_id': latest.id,
                            'chosen_plan_id': chosen_record.plan_id, 'model_rounds': len(env.model.requests),
                            'document_sha256': __import__('hashlib').sha256(source.read_bytes()).hexdigest(),
                            'page_errors': errors, 'browser_post_requests': [],
                            'scope': 'production TaskPoolView and ProjectsView in isolated route host; optional resource monitors unavailable'
                        }, ensure_ascii=False, indent=2), encoding='utf-8')
                    finally: await browser.close()
        finally:
            if process:
                process.terminate()
                try: await asyncio.to_thread(process.wait, timeout=10)
                except subprocess.TimeoutExpired: process.kill(); await asyncio.to_thread(process.wait, timeout=10)
            host.unlink(missing_ok=True)
            server.should_exit = True; await asyncio.wait_for(serving, 10)

"""Focused actual-store/browser diagnosis before rerunning the full product chain."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from uuid import uuid4

import httpx
from playwright.async_api import async_playwright, expect
import pytest
import uvicorn

from src.api.routes.projects import router as projects_router
from src.api.routes.case_management import router as cases_router
from src.application.projects.project_overview_service import ProjectOverviewService
from src.schemas.case_generation_tasks import GenerationRetryRequest, GenerationTaskPolicy
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_generation_tasks import task_request
from test_incremental_generation_sources import accepted, responder
from test_task_pool_generations import catalog_environment, catalog_evidence


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_TASK_POOL_BROWSER")!="1",reason="real stores/Chromium/Vite scoped catalog host")
async def test_actual_browser_catalog_response_dom_identity_and_detail_diagnosis(tmp_path):
    destination=Path(os.environ["QA_GENERATION_POOL_BROWSER_EVIDENCE_DIR"])
    destination.mkdir(parents=True,exist_ok=True)
    async with catalog_environment(tmp_path,"docx") as ((values,service,store,config),app):
        env,project,doc,_,case,*_=values
        def respond(body):
            value=json.loads(responder(body))
            for item in value["cases"]:
                item["content_json"]["steps"]=[{"操作":f"完整步骤{i}"} for i in range(12)]
                item["content_json"]["深层扩展"]=dict(case.version.content_json["深层扩展"])
            return json.dumps(value,ensure_ascii=False)
        with provider(responder=respond) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            review=await accepted(values)
            normal=await service.submit(project.id,doc.id,task_request(review,case,include_new_cases=True,new_case_mode_key="api_testing"))
            _,modules=await service.read_group(project.id,doc.id,normal.id)
            completed=[]
            for task in modules: completed.append(await service.execute(await store.claim(project.id,task.id,worker_id="browser-normal")))
            await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15,max_input_bytes=1))
            failure_group=await service.submit(project.id,doc.id,task_request(review,case).model_copy(update={"creation_key":"browser-parent"}))
            _,modules=await service.read_group(project.id,doc.id,failure_group.id)
            parent=await service.execute(await store.claim(project.id,modules[0].id,worker_id="browser-parent"))
            await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15))
            child=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="browser-user-key"))
            child_task=await service.execute(await store.claim(project.id,child.task.id,worker_id="browser-retry"))
            assert all(task.status=="completed" for task in [*completed,child_task]) and parent.status=="failed"
            additional=[]
            for name in ("browser-queued","browser-running"):
                pending_group=await service.submit(project.id,doc.id,task_request(review,case,include_new_cases=True,new_case_mode_key="api_testing").model_copy(update={"creation_key":name}))
                _,pending=await service.read_group(project.id,doc.id,pending_group.id)
                additional.extend(pending)
                if name=="browser-running": await store.claim(project.id,pending[0].id,worker_id="browser-running")
            app.state.project_service=env.projects;app.state.test_case_service=env.cases
            app.state.project_overview_service=ProjectOverviewService(project_service=env.projects,api_doc_store=env.docs._catalog_store,
                session_store=env.sessions,knowledge_graph_service=None,test_case_store=env.cases._store)
            app.include_router(projects_router,prefix="/api/v1");app.include_router(cases_router,prefix="/api/v1")
            listener=socket.socket();listener.bind(("127.0.0.1",0))
            api_port=listener.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,log_level="warning",timeout_graceful_shutdown=5))
            serving=asyncio.create_task(server.serve(sockets=[listener]))
            web=Path(__file__).resolve().parents[2]/"agent_web"
            host=web/("__generation_pool_probe_"+uuid4().hex+".html")
            host.write_text('''<!doctype html><html><head><style>:root{--bg:#f6f7fa;--surface:#fff;--text:#18212f;--muted:#666;--border:#ccc;--red:#b22}body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import {createApp} from 'vue';import {createRouter,createMemoryHistory,RouterView} from 'vue-router';
import TaskPoolView from '/src/views/TaskPoolView.vue';import CaseGenerationTaskView from '/src/views/CaseGenerationTaskView.vue';
import {registerLocale,setLocale} from '/src/services/i18n';import zh from '/src/locales/zh-CN';registerLocale('zh-CN',zh);setLocale('zh-CN');
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/taskpool',name:'taskpool',component:TaskPoolView},
{path:'/projects/:projectId/documents/:documentId/generations/:groupId/tasks/:taskId',name:'case-generation-task',component:CaseGenerationTaskView}]});
await router.push('/taskpool');window.generationPoolProbe={router};createApp(RouterView).use(router).mount('#app');
</script></body></html>''',encoding="utf-8")
            port_socket=socket.socket();port_socket.bind(("127.0.0.1",0));web_port=port_socket.getsockname()[1];port_socket.close()
            process=None
            try:
                node=shutil.which("node");assert node
                with (destination/"vite.log").open("wb") as log:
                    process=subprocess.Popen([node,str(web/"node_modules/vite/bin/vite.js"),"--port",str(web_port)],cwd=web,
                        env={**os.environ,"VITE_API_PROXY_TARGET":f"http://127.0.0.1:{api_port}","API_AUTH_TOKEN":""},stdout=log,stderr=log)
                    url=f"http://127.0.0.1:{web_port}/{host.name}"
                    async with httpx.AsyncClient() as client:
                        for _ in range(100):
                            try:
                                if (await client.get(url)).status_code==200:break
                            except httpx.TransportError:pass
                            await asyncio.sleep(.1)
                        else:raise AssertionError("scoped browser host did not start")
                    async with async_playwright() as pw:
                        browser=await pw.chromium.launch(headless=True)
                        page=await browser.new_page(viewport={"width":1440,"height":900})
                        responses=[];errors=[]
                        page.on("pageerror",lambda error:errors.append(str(error)))
                        async def observe(response):
                            if "/task-pool/entries" in response.url:
                                responses.append({"url":response.url,"status":response.status,"body":await response.json()})
                        page.on("response",observe)
                        try:
                            await page.goto(url)
                            await page.get_by_label("任务类型",exact=True).select_option("case_generation")
                            await expect(page.locator(".data-table tbody tr")).to_have_count(8)
                            assert set(await page.locator(".data-table tbody tr").evaluate_all("rows=>rows.map(row=>row.dataset.taskId)"))=={task.id for task in [*completed,parent,child_task,*additional]}
                            assert any(item["body"]["items"] and all(entry["kind"]=="case_generation" for entry in item["body"]["items"]) for item in responses)
                            await page.locator(f'[data-task-id="{child_task.id}"]').get_by_role("button",name="查看生成任务",exact=True).click()
                            await expect(page.get_by_label("固定重试来源",exact=True)).to_contain_text("browser-user-key")
                            reference=child_task.checkpoint.receipt["versions"][0]
                            await page.get_by_role("button",name="读取完整任务草稿 "+reference["version_id"],exact=True).click()
                            await expect(page.get_by_label("完整任务草稿 "+reference["version_id"],exact=True)).to_contain_text("完整步骤11")
                            assert len(sends)==3 and not errors,errors
                            await catalog_evidence("browser-scope",values,service,failure_group,child_task=child_task.model_dump(mode="json"),
                                retry=child.retry.model_dump(mode="json"),physical_requests=sends,actual_scoped_browser=True)
                        finally:
                            (destination/"browser-diagnostics.json").write_text(json.dumps({"responses":responses,"page_errors":errors,
                                "physical_posts":len(sends),"dom":await page.content(),"url":page.url},ensure_ascii=False,indent=2),encoding="utf-8")
                            await page.screenshot(path=str(destination/"browser-diagnostic.png"),full_page=True)
                            await browser.close()
            finally:
                if process:
                    process.terminate()
                    try:await asyncio.to_thread(process.wait,timeout=10)
                    except subprocess.TimeoutExpired:process.kill();await asyncio.to_thread(process.wait,timeout=10)
                host.unlink(missing_ok=True)
                server.should_exit=True;await asyncio.wait_for(serving,10)

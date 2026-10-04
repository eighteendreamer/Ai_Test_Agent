from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
from contextlib import AsyncExitStack
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import expect

from src.application.documents.api_doc_store import PostgresApiDocStore
from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.git_service import CONFIG_KEY
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_git import body, git, repository, state
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api
from test_document_full_product import product_browser, open_documents

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_GIT_PRODUCTION")!="1",
    reason="production API + independent CLIs + isolated MySQL/PG/RustFS/Redis")]


@pytest.mark.parametrize("extension",["md","docx"])
async def test_production_fixed_git_import_lost_reply_parse_compare_and_restart(tmp_path,repository,extension):
    root,first,second,raw,new,_=repository
    path="需求 文档/规则[1].md" if extension=="md" else "需求 文档/订单.docx"
    if extension=="docx":
        raw=git(root,"cat-file","blob",first+":"+path)
        new=git(root,"cat-file","blob",second+":"+path)
    before=state(root)
    folder=Path(os.environ["QA_DOCUMENT_GIT_PRODUCTION_EVIDENCE_DIR"])/extension
    folder.mkdir(parents=True,exist_ok=True)
    report={}
    try:
        async with controlled_http(tmp_path) as (business,calls,*_):
            async with isolated_runtime(tmp_path) as (settings,processes,_,objects,owned):
                settings.orchestration.redis_task_outbox_streams=",".join([DOCUMENT_PARSE_STREAM,DOCUMENT_DIFFERENCE_STREAM])
                models=MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-git-production",provider="openai",
                    transport="openai_chat_completions",base_url=business+"/v1",api_key=uuid4().hex,
                    is_active=True,use_provider_defaults=False,capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env=child_environment(settings)
                port=free_port()
                arguments=("-m","uvicorn","src.main:app","--host","127.0.0.1","--port",str(port))
                api=processes.start("git-api-first",*arguments)
                queues={kind:RedisTaskQueue(settings.database.redis_url,stream=stream,
                    group=settings.orchestration.redis_task_consumer_group+":"+group)
                    for kind,stream,group in (("parse",DOCUMENT_PARSE_STREAM,"document_parsing"),
                        ("difference",DOCUMENT_DIFFERENCE_STREAM,"document_difference"))}
                for queue in queues.values():
                    await queue.connect()
                browser_stack=AsyncExitStack()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1",timeout=50) as client:
                        await wait_api(client,api)
                        async def post(path,payload,status=200):
                            response=await client.post(path,json=payload)
                            assert response.status_code==status,response.text
                            return response.json()
                        async def get(path,**kwargs):
                            response=await client.get(path,**kwargs)
                            assert response.status_code==200,response.text
                            return response.json()
                        async def worker(kind,name):
                            assert await PostgresTaskOutbox(settings).relay_once(queues[kind],limit=1)==1
                            module="run_document_parse_worker" if kind=="parse" else "run_document_difference_worker"
                            child=processes.start(name,"-m","src.cli."+module,"--once","--worker-id",name)
                            assert await processes.wait(child)==0
                            assert "_processed=1" in (tmp_path/(name+".stdout")).read_text(encoding="utf-8")

                        project=await post("/projects",{"project_key":"git-production-"+uuid4().hex,"name":"生产 Git 来源"},201)
                        base=f"/projects/{project['id']}"
                        page,errors,network=await browser_stack.enter_async_context(product_browser(tmp_path,port,folder))
                        await open_documents(page)
                        async def open_git():
                            await page.get_by_role("button",name="从 Git 导入文档",exact=True).click()
                            modal=page.locator(".upload-modal").filter(has_text="Git 文档来源")
                            await modal.get_by_label("Git 文档所属项目",exact=True).select_option(project["id"])
                            panel=modal.get_by_label("Git 文档导入",exact=True)
                            await expect(panel).to_be_visible()
                            return panel
                        panel=await open_git()
                        await panel.locator(".git-source-settings > summary").click()
                        await panel.get_by_role("button",name="添加仓库",exact=True).click()
                        await panel.get_by_label("仓库编号",exact=True).fill("repo")
                        await panel.get_by_label("仓库名称",exact=True).fill("真实本地仓库")
                        await panel.get_by_label("服务器本地仓库路径",exact=True).fill(str(root))
                        async with page.expect_response(lambda r:r.request.method=="PUT" and r.url.endswith("/document-git-settings")) as saved_settings:
                            await panel.get_by_role("button",name="保存项目 Git 设置",exact=True).click()
                        settings_response=await saved_settings.value
                        assert settings_response.status==200,await settings_response.text()
                        stored=MySQLApplicationConfigStore(settings).get(CONFIG_KEY,scope_key=project["id"])
                        assert stored.payload==(await settings_response.json())["payload"]
                        query={"repository_id":"repo","reference":first,"path":path}
                        await panel.get_by_label("Git 引用",exact=True).fill(first)
                        await panel.get_by_label("仓库内文件路径",exact=True).fill(path)
                        await panel.get_by_role("button",name="解析固定来源",exact=True).click()
                        await expect(panel.locator(".git-fixed-source")).to_contain_text(first)
                        lost={}
                        async def drop(route):
                            lost["request"]=route.request.post_data_json
                            response=await route.fetch()
                            assert response.status==200,await response.text()
                            lost["result"]=await response.json()
                            await route.abort("failed")
                        await page.route("**/document-git/import",drop)
                        await panel.get_by_role("button",name="导入为新文档",exact=True).click()
                        await expect(panel.get_by_role("alert")).to_contain_text("保留原请求")
                        await page.unroute("**/document-git/import",drop)
                        initial=lost["request"]
                        await panel.get_by_role("button",name="查询原 Git 提交",exact=True).click()
                        await expect(panel.get_by_role("status")).to_contain_text("已保存文档版本 V1")
                        original=await get(base+"/document-git/submissions",params={"request_id":initial["request_id"]})
                        assert original==lost["result"]
                        await page.get_by_role("button",name="关闭 Git 导入",exact=True).click()
                        doc=original["document"]
                        docbase=base+f"/documents/{doc['id']}"
                        catalog=PostgresApiDocStore(settings)
                        saved=await catalog.get_submission(doc["id"],initial["request_id"])
                        assert saved.result.model_dump(mode="json")==original
                        snapshots=[]
                        for ordinal,(reference,expected) in enumerate(((first,raw),(second,new)),1):
                            if ordinal==2:
                                card=page.locator(".api-doc-card").filter(has_text=doc["filename"])
                                await card.locator(".api-doc-actions button").first.click()
                                version_panel=page.get_by_label("Git 文档导入",exact=True)
                                await version_panel.get_by_label("Git 引用",exact=True).fill(reference)
                                await version_panel.get_by_label("仓库内文件路径",exact=True).fill(path)
                                await version_panel.get_by_role("button",name="解析固定来源",exact=True).click()
                                await expect(version_panel.locator(".git-fixed-source")).to_contain_text(reference)
                                async with page.expect_response(lambda r:r.request.method=="POST" and r.url.endswith("/document-git/import")) as version_saved:
                                    await version_panel.get_by_role("button",name="导入为当前文档的新版本",exact=True).click()
                                version_response=await version_saved.value
                                assert version_response.status==200,await version_response.text()
                                updated=await version_response.json()
                                doc=updated["document"]
                                await expect(version_panel.get_by_role("status")).to_contain_text("已保存文档版本 V2")
                                await page.locator(".api-doc-preview-modal .tools-modal-head-actions .icon-btn").last.click()
                            owned.append(doc["original_asset"]["storage_uri"])
                            assert (await objects.read_object_uri(owned[-1]))["content"]==expected
                            assert (await catalog.get_version(doc["id"],doc["current_version_id"])).snapshot.source_origin.commit==reference
                            task=await post(docbase+"/parse-tasks",{"creation_key":f"git-parse-{ordinal}",
                                "version_id":doc["current_version_id"]},202)
                            await worker("parse",f"git-parser-{ordinal}")
                            parsed=await get(docbase+f"/parse-tasks/{task['id']}")
                            snapshot=await get(docbase+f"/parse-snapshots/{parsed['snapshot_id']}")
                            owned.append(snapshot["snapshot"]["storage_uri"])
                            assert snapshot["result"]["text"].find(str(5+2*ordinal))>=0
                            snapshots.append(snapshot)
                        compared=await post(docbase+"/difference-tasks",{"creation_key":"git-comparison",
                            "old_snapshot_id":snapshots[0]["snapshot"]["id"],"new_snapshot_id":snapshots[1]["snapshot"]["id"]},202)
                        await worker("difference","git-comparison-worker")
                        done=await get(docbase+f"/difference-tasks/{compared['id']}")
                        difference=await get(docbase+f"/difference-snapshots/{done['snapshot_id']}")
                        owned.append(difference["snapshot"]["storage_uri"])
                        assert any(detail["old"]["text"]=="7" and detail["new"]["text"]=="9"
                            for change in difference["result"]["normalized"]["changes"]
                            for detail in change["characters"])
                        assert difference["snapshot"]["seed"]["old_snapshot"]["id"]==snapshots[0]["snapshot"]["id"]
                        assert difference["snapshot"]["seed"]["new_snapshot"]["id"]==snapshots[1]["snapshot"]["id"]
                        assert state(root)==before
                        removed=await client.put(base+"/document-git-settings",json={"repositories":[]})
                        assert removed.status_code==200
                        moved=root.with_name(root.name+"-removed")
                        root.rename(moved)
                        try:
                            await processes.stop(api)
                            api=processes.start("git-api-restarted",*arguments)
                            await wait_api(client,api)
                            replay=await post(base+"/document-git/import",initial)
                            assert replay==original
                            assert len(await catalog.list_versions(doc["id"]))==2
                            history=await get(docbase+f"/difference-snapshots/{done['snapshot_id']}")
                            assert history==difference
                            plain=await post("/registry/api-docs/upload",{"filename":"ordinary.md","project_id":project["id"],
                                "request_id":"ordinary-without-git","content_base64":base64.b64encode(b"# ordinary upload").decode()})
                            owned.append(plain["original_asset"]["storage_uri"])
                            assert plain["source_origin"] is None
                            assert (await objects.read_object_uri(owned[-1]))["content"]==b"# ordinary upload"
                            await page.reload()
                            await open_documents(page)
                            panel=await open_git()
                            await expect(panel.get_by_role("status")).to_contain_text("已保存文档版本 V1")
                            # Recovery returns V1; current projection still belongs to V2.
                            await page.get_by_role("button",name="关闭 Git 导入",exact=True).click()
                            card=page.locator(".api-doc-card").filter(has_text=doc["filename"])
                            await card.locator(".api-doc-actions button").first.click()
                            await expect(page.get_by_label("解析原件版本",exact=True)).to_have_value(doc["current_version_id"])
                            await page.screenshot(path=str(folder/"recovered-current-version.png"),full_page=True)
                            assert not errors
                        finally:
                            moved.rename(root)
                        assert not calls
                        report={"project":project,"settings_revision":stored.revision,"initial":original,
                            "replay":replay,"snapshots":snapshots,"difference":difference,
                            "ordinary_upload":plain,"model_calls":len(calls),"repository_unchanged":state(root)==before,
                            "browser_errors":errors,"network":network}
                finally:
                    await browser_stack.aclose()
                    for queue in queues.values():
                        await queue.close()
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout",".stderr"} or path.name=="docker.json":
                shutil.copyfile(path,folder/path.name)
        (folder/"verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        (folder/("v1."+extension)).write_bytes(raw)
        (folder/("v2."+extension)).write_bytes(new)

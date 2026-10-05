"""Navigate the built task pool into real fixed generation and retry histories."""
import asyncio
import json
import re
from urllib.parse import urlsplit, urlencode

from playwright.async_api import expect

from src.infrastructure.postgres_runtime import postgres_connect


async def generation_pool_from_product(page, *, base, generation, retries, get, evidence, settings, sends, archived=False):
    origin=urlsplit(page.url)
    origin=f"{origin.scheme}://{origin.netloc}"
    group=generation["normal"]["group"]
    count=len(sends)
    output={"archived":archived,"physical_posts_before":count,"items":[]}
    browser_handle=page.context.browser
    assert browser_handle is not None
    probe_context=await browser_handle.new_context(viewport=page.viewport_size)
    browser=await probe_context.new_page()
    errors=[]
    responses=[]
    browser.on("pageerror",lambda error:errors.append(str(error)))
    async def observe(response):
        if "/task-pool/entries" in response.url:
            try: responses.append({"url":response.url,"status":response.status,"body":await response.json()})
            except Exception as exc: responses.append({"url":response.url,"status":response.status,"read_error":type(exc).__name__})
    browser.on("response",observe)
    try:
        await browser.goto(origin+"/taskpool")
        await browser.get_by_label("任务类型",exact=True).select_option("case_generation")
        actual=await get("/task-pool/entries?"+urlencode({"kind":"case_generation","project_id":group["project_id"],"limit":100}))
        expected=[*generation["normal"]["modules"],*generation["failed"]["modules"],*generation["retired"]["modules"]]
        for item in retries["items"]: expected.extend([*item["parent_report"]["modules"],item["view"]["task"]])
        tasks={}
        for task in expected:
            # The frozen-request retry reuses generation['failed']; one task has two provenance roles.
            assert tasks.get(task["id"],task)==task
            tasks[task["id"]]=task
        assert {entry["task"]["id"] for entry in actual["items"]}==set(tasks)
        assert not actual["has_more"]
        await expect(browser.locator(".data-table tbody tr")).to_have_count(len(tasks))
        output["provenance_references"]=len(expected)
        output["unique_tasks"]=len(tasks)
        for entry in actual["items"]:
            task=entry["task"]
            assert task["status"]==tasks[task["id"]]["status"]
            for private in ("checkpoint","seed","storage_uri","config","budget","response"):
                assert private not in task
        chosen=[*generation["normal"]["modules"],generation["failed"]["modules"][0],generation["retired"]["modules"][0],
                *[item["view"]["task"] for item in retries["items"]]]
        by_id={item["task"]["id"]:item for item in actual["items"]}
        for task in chosen:
            await browser.goto(origin+"/taskpool")
            await browser.get_by_label("任务类型",exact=True).select_option("case_generation")
            row=browser.locator(f'[data-task-id="{task["id"]}"]')
            await expect(row).to_be_visible()
            summary=by_id[task["id"]]["task"]
            if summary["retry"]:
                await expect(row).to_contain_text("生成模块重试")
                assert summary["retry"]["parent_task_id"] in tasks
            await row.get_by_role("button",name="查看生成任务",exact=True).click()
            route=f'/projects/{group["project_id"]}/documents/{group["document_id"]}/generations/{task["group_id"]}/tasks/{task["id"]}'
            await expect(browser).to_have_url(re.compile(re.escape(origin+route)+r'(?:\?.*)?$'))
            detail=browser.get_by_label("固定生成任务详情",exact=True)
            await expect(detail.locator(".generation-task-status")).to_contain_text(task["message"])
            await expect(detail).to_contain_text(task["id"])
            read={"summary":summary,"route":browser.url,"drafts":[]}
            if summary["retry"]:
                origin_view=detail.get_by_label("固定重试来源",exact=True)
                await expect(origin_view).to_contain_text(summary["retry"]["creation_key"])
                await expect(origin_view).to_contain_text(summary["retry"]["parent_task_id"])
            receipt=(task.get("checkpoint") or {}).get("receipt") or {}
            for ref in receipt.get("versions",[]):
                await detail.get_by_role("button",name="读取完整任务草稿 "+ref["version_id"],exact=True).click()
                full=detail.get_by_label("完整任务草稿 "+ref["version_id"],exact=True)
                await expect(full).to_contain_text("完整步骤11")
                await expect(full).to_contain_text('"布尔": false')
                raw=full.get_by_text("完整草稿版本与自身说明",exact=True).locator("..").locator("pre")
                version=json.loads(await raw.inner_text())
                fixed=next(item for item in await get("/test-cases/"+ref["case_id"]+"/versions") if item["id"]==ref["version_id"])
                assert version==fixed and len(version["content_json"]["steps"])==12
                assert version["content_hash"]==ref["content_hash"]
                read["drafts"].append(version)
            if task.get("checkpoint") and task["checkpoint"].get("response"):
                await detail.get_by_role("button",name="读取固定任务原响应",exact=True).click()
                raw=detail.get_by_text("固定任务完整原响应",exact=True).locator("..").locator("pre")
                await expect(raw).to_be_visible()
                read["response"]=json.loads(await raw.inner_text())
                assert read["response"]==await get(base+"/case-generation-tasks/"+task["id"]+"/response")
            if task["status"]=="failed":
                button=detail.get_by_role("button",name="为失败模块创建新任务 "+task["id"],exact=True)
                if archived: await expect(button).to_be_disabled()
                else: await expect(button).to_be_enabled()
            if summary["result_kind"]=="retirement_candidate":
                await expect(detail.get_by_text("固定停用候选（待评审）",exact=True)).to_be_visible()
            await browser.reload()
            await expect(browser.get_by_label("固定生成任务详情",exact=True).locator(".generation-task-status")).to_contain_text(task["message"])
            output["items"].append(read)
        layout=await browser.evaluate("({viewport:innerWidth,width:document.documentElement.scrollWidth})")
        assert layout["width"]<=layout["viewport"],layout
        output["layout"]=layout
        await browser.screenshot(path=str(evidence/("generation-pool-archived.png" if archived else "generation-pool.png")),full_page=True)
        with postgres_connect(settings) as conn,conn.cursor() as cur:
            cur.execute(f"SELECT id,project_id,document_id,group_id,module_id,status,phase,error_code,checkpoint FROM {settings.database.postgres_api_doc_table}_case_gen_tasks ORDER BY id")
            output["pg_tasks"]=cur.fetchall()
            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_case_gen_retries ORDER BY id")
            output["pg_retries"]=[row["record"] for row in cur.fetchall()]
        output["public_catalog"]=actual
        output["physical_posts_after"]=len(sends)
        assert len(sends)==count and not errors,errors
        (evidence/("generation-pool-archived.json" if archived else "generation-pool.json")).write_text(json.dumps(output,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        return output
    finally:
        await asyncio.sleep(.05)
        (evidence/("generation-pool-archived-diagnostics.json" if archived else "generation-pool-diagnostics.json")).write_text(
            json.dumps({"responses":responses,"page_errors":errors,"dom":await browser.content(),"url":browser.url},ensure_ascii=False,indent=2),encoding="utf-8")
        await browser.screenshot(path=str(evidence/("generation-pool-archived-diagnostic.png" if archived else "generation-pool-diagnostic.png")),full_page=True)
        await probe_context.close()

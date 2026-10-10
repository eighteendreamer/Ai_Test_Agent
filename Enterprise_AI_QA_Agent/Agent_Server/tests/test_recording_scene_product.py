"""Actual newly captured scene assets, independent main and stores; no model."""
import asyncio
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
from playwright.async_api import async_playwright, expect
import pytest

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.postgres_runtime import postgres_connect
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_document_full_product import product_browser

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_SCENE") != "1", reason="real Chromium/main/PG/MySQL/Redis")]


async def test_actual_recording_scene_management(tmp_path):
    await actual_recording_scene_management(tmp_path)


async def actual_recording_scene_management(tmp_path, scene_probe=None):
    evidence = Path(os.environ["RECORDING_SCENE_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    html = '<!doctype html><meta charset="utf-8"><title>场景整理验收</title><input id="value"><button id="repeat">增加计数</button><p id="count">0</p><script>repeat.onclick=()=>{count.textContent=String(Number(count.textContent)+1)}</script>'
    fixture = tmp_path / "scene.html"
    fixture.write_text(html, encoding="utf-8")
    (evidence / "fixture.html").write_text(html, encoding="utf-8")
    project_ids = []
    graph = None
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
            graph = MemgraphRuntimeProvider(settings)
            port = free_port()
            args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("scene-api", *args)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=60) as client:
                await wait_api(client, api)
                for name in ("场景项目", "隔离项目"):
                    response = await client.post("/projects", json={"project_key":"scene-"+uuid4().hex[:12],"name":name})
                    response.raise_for_status()
                    project_ids.append(response.json()["id"])
                project_id = project_ids[0]
                async with async_playwright() as playwright:
                    cdp = free_port()
                    browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={cdp}"])
                    try:
                        page = await browser.new_page()
                        response = await client.post("/recordings", json={"project_id":project_id,"name":"实际录制场景","entry_url":fixture.resolve().as_uri(),"driver":{"kind":"cdp-attach","endpoint":f"http://127.0.0.1:{cdp}"}})
                        response.raise_for_status()
                        recording_id = response.json()["id"]
                        async with asyncio.timeout(15):
                            while (await client.get(f"/recordings/{recording_id}")).json()["status"] != "ready": await asyncio.sleep(.1)
                        (await client.post(f"/recordings/{recording_id}/control", json={"action":"start"})).raise_for_status()
                        await page.locator("#value").fill("完整输入😀"*40)
                        await page.wait_for_timeout(600)
                        await page.locator("#repeat").click()
                        await page.locator("#repeat").click()
                        assert await page.locator("#count").inner_text() == "2"
                        response = await client.post(f"/recordings/{recording_id}/control", json={"action":"stop"})
                        response.raise_for_status()
                        assert response.json()["status"] == "completed"
                    finally:
                        await browser.close()
                original = (await client.get(f"/recordings/{recording_id}")).json()
                save("original-recording.json", original)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq", (recording_id,))
                    original_rows = [dict(row) for row in cur.fetchall()]
                save("postgres-original-events.json", original_rows)
                url = f"/recordings/scenes/projects/{project_id}"
                creation = {"request_id":str(uuid4()),"recording_id":recording_id}
                response = await client.post(url, json=creation)
                save("scene-create-http.json", {"status":response.status_code,"payload":response.text})
                assert response.status_code == 201, response.text
                v1 = response.json()
                scene_id = v1["scene_id"]
                detail_url = url+"/"+scene_id
                assert v1["version"]["content"]["status"] == "draft"
                duplicates = await asyncio.gather(*[client.post(url,json=creation) for _ in range(2)])
                assert all(r.status_code==201 and r.json()["version"]["id"]==v1["version"]["id"] for r in duplicates)
                assert (await client.get(url+"/by-request/"+creation["request_id"])).json() == v1
                collision = await client.post(url,json={**creation,"recording_id":"different"})
                assert collision.status_code==409
                wrong_url = f"/recordings/scenes/projects/{project_ids[1]}/{scene_id}"
                assert (await client.get(wrong_url)).status_code==404
                assert (await client.post(f"/recordings/scenes/projects/{project_ids[1]}",json={**creation,"request_id":str(uuid4())})).status_code==404
                content = json.loads(json.dumps(v1["version"]["content"]))
                click_seq = next(step["data"]["seq"] for step in v1["version"]["source"]["original_draft"]["steps"] if step["data"]["recorder_type"]=="click")
                content["name"] = "两次点击业务场景"
                content["description"] = "重复点击需要保留两个独立操作，完整输入与定位供后续理解"
                content["status"] = "organized"
                content["aliases"] = [{"name":"计数按钮","source_seq":click_seq,"description":"本次录制的按钮"}]
                next(s for s in content["steps"] if s["source_seq"]==click_seq).update(alias="计数按钮",expected="计数增加")
                content["steps"] = list(reversed(content["steps"]))
                edit = {"request_id":str(uuid4()),"parent_version_id":v1["version"]["id"],"change_reason":"整理说明与别名，保留完整证据","content":content}
                edits = await asyncio.gather(*[client.post(detail_url+"/versions",json=edit) for _ in range(2)])
                assert all(r.status_code==201 for r in edits),[r.text for r in edits]
                v2 = edits[0].json()
                assert edits[1].json()==v2 and v2["version"]["version"]==2
                save("version2.json",v2)
                if scene_probe is not None:
                    await scene_probe(stage="initial", settings=settings, client=client, evidence=evidence,
                        project_ids=project_ids, version=v2)
                assert (await client.get(detail_url,params={"version_id":v1["version"]["id"]})).json()["version"]==v1["version"]
                assert (await client.get(detail_url+"/versions/by-request/"+edit["request_id"])).json()==v2
                for changed in ({**edit,"request_id":str(uuid4())},{**edit,"change_reason":"更改相同请求"}):
                    assert (await client.post(detail_url+"/versions",json=changed)).status_code==409
                bad = json.loads(json.dumps(edit))
                bad.update(request_id=str(uuid4()),parent_version_id=v2["version"]["id"])
                bad["content"]["steps"][0]["source_seq"]=999999
                assert (await client.post(detail_url+"/versions",json=bad)).status_code==409
                draft_url = detail_url+f"/versions/{v2['version']['id']}/case-draft"
                draft_response = await client.post(draft_url)
                draft_response.raise_for_status()
                draft = draft_response.json()
                save("formal-draft.json",draft)
                assert draft["case"]["lifecycle_status"]=="draft"
                assert draft["version"]["steps"][0]["data"]["seq"]==content["steps"][0]["source_seq"]
                assert next(s for s in draft["version"]["steps"] if s["data"]["seq"]==click_seq)["data"]["business_alias"]=="计数按钮"
                assert draft["version"]["test_data"]["scene"]["description"]==content["description"]
                target_step=next(s for s in v1["version"]["source"]["original_draft"]["steps"] if s["data"]["seq"]==click_seq)
                alias=draft["version"]["test_data"]["scene"]["aliases"][0]
                assert alias["target"]==target_step["data"]["event"]["target"]
                assert alias["page"]==target_step["data"]["event"]["page"]
                assert (await client.post(draft_url)).json()==draft
                assert (await client.get(draft_url)).json()==draft
                # A newer version cannot mutate the original case conversion.
                newer_content=json.loads(json.dumps(content))
                excluded=newer_content["steps"].pop()["source_seq"]
                newer = {**edit,"request_id":str(uuid4()),"parent_version_id":v2["version"]["id"],"change_reason":"明确排除一个场景动作","content":newer_content}
                v3_response = await client.post(detail_url+"/versions",json=newer)
                v3_response.raise_for_status()
                v3=v3_response.json()
                assert v3["version"]["source"]["excluded_sequences"]==[excluded]
                assert (await client.get(draft_url)).json()==draft
                # Competing saves from the same parent have exactly one winner.
                races = await asyncio.gather(*[client.post(detail_url+"/versions",json={**newer,"request_id":str(uuid4()),"parent_version_id":v3["version"]["id"],"change_reason":f"并发{i}"}) for i in range(2)])
                assert sorted(r.status_code for r in races)==[201,409]
                save("concurrent-http.json",[{"status":r.status_code,"payload":r.json()} for r in races])
                async with product_browser(tmp_path,port,evidence) as (page,errors,network):
                    await expect(page.locator(".left-nav")).to_be_visible()
                    await page.goto(page.url.split("/home")[0]+f"/projects/{project_id}")
                    await page.get_by_role("link",name="录制场景",exact=True).click()
                    await expect(page.get_by_role("heading",name="录制场景 · 场景项目")).to_be_visible()
                    committed=[]
                    async def lose_response(route):
                        if route.request.method!="POST":
                            await route.continue_(); return
                        actual=await route.fetch()
                        committed.append({"status":actual.status,"payload":await actual.json()})
                        await route.abort("failed")
                    pattern=f"**{url}"
                    await page.route(pattern,lose_response)
                    await page.get_by_role("button",name="从录制创建场景",exact=True).click()
                    await expect(page.locator(".pending")).to_be_visible()
                    await expect(page.locator(".pending button").first).to_be_enabled()
                    assert len(committed)==1 and committed[0]["status"]==201
                    ui_v1=committed[0]["payload"]
                    save("committed-response-lost.json",committed)
                    await page.unroute(pattern,lose_response)
                    await page.reload()
                    await expect(page.locator(".pending")).to_be_visible()
                    await page.get_by_role("button",name="恢复提交",exact=True).click()
                    await expect(page.get_by_role("status")).to_contain_text("已恢复保存结果")
                    assert len((await client.get(url)).json()["items"])==2
                    await page.get_by_label("场景名称",exact=True).fill("页面整理场景")
                    ui_step = page.locator(".scene-step").filter(has_text=f"原事件 #{click_seq}")
                    await ui_step.get_by_label("操作说明",exact=True).fill("点击计数按钮并检查页面响应")
                    await ui_step.get_by_label("预期结果",exact=True).fill("计数增加，重复点击仍保留")
                    await ui_step.get_by_role("button",name="上移",exact=True).click()
                    await page.get_by_role("button",name="添加别名",exact=True).click()
                    await page.locator(".scene-alias").get_by_label("别名",exact=True).fill("页面计数按钮")
                    await page.locator(".scene-alias").get_by_label("原动作序号",exact=True).fill(str(click_seq))
                    await page.locator(".scene-step").filter(has_text=f"原事件 #{click_seq}").get_by_label("元素别名",exact=True).select_option("页面计数按钮")
                    await page.get_by_label("本次修改原因",exact=True).fill("实际页面整理")
                    async with page.expect_response(lambda r:r.request.method=="POST" and r.url.endswith("/versions")) as saved:
                        await page.get_by_role("button",name="保存新版本",exact=True).click()
                    ui_v2 = await (await saved.value).json()
                    assert (await saved.value).status==201,ui_v2
                    await expect(page.get_by_role("status")).to_contain_text("已保存 V2")
                    assert ui_v2["version"]["content"]["aliases"][0]["name"]=="页面计数按钮"
                    await page.get_by_label("历史版本",exact=True).select_option(ui_v1["version"]["id"])
                    await expect(page.get_by_label("场景名称",exact=True)).to_have_value("实际录制场景")
                    await expect(page.get_by_role("button",name="保存新版本",exact=True)).to_be_disabled()
                    await page.get_by_label("历史版本",exact=True).select_option(ui_v2["version"]["id"])
                    await expect(page.get_by_label("场景名称",exact=True)).to_have_value("页面整理场景")
                    await page.get_by_role("button",name="将已保存版本转为用例草稿",exact=True).click()
                    await expect(page.get_by_role("status")).to_contain_text("需评审启用")
                    await page.screenshot(path=str(evidence/"scene-editor.png"),full_page=True)
                    before_failure = [r for r in network if "/api/v1/" in r["url"] and r["status"]>=400]
                    assert not before_failure,before_failure
                    restart_start=len(network)
                    await processes.stop(api)
                    api = processes.start("scene-api-restarted",*args)
                    await wait_api(client,api)
                    after = (await client.get(detail_url)).json()
                    assert after["version"]["version"]==4
                    assert (await client.get(draft_url)).json()==draft
                    if scene_probe is not None:
                        await scene_probe(stage="restart", settings=settings, client=client, evidence=evidence,
                            project_ids=project_ids, version=v2)
                    await page.reload()
                    await page.locator("aside button").filter(has_text="页面整理场景").click()
                    await expect(page.get_by_label("场景名称",exact=True)).to_have_value("页面整理场景")
                    assert not errors,errors
                    save("ui-versions.json",{"v1":ui_v1,"v2":ui_v2,"page_errors":errors,"restart_window":network[restart_start:]})
                detail = (await client.get(f"/recordings/{recording_id}")).json()
                assert detail["events"]==original["events"] and detail["finalize_metrics"]==original["finalize_metrics"]
                with postgres_connect(settings) as conn,conn.cursor() as cur:
                    raw={}
                    for table in (settings.database.postgres_recording_scene_table,settings.database.postgres_recording_scene_version_table,settings.database.postgres_recording_event_table,settings.database.postgres_test_case_version_table):
                        cur.execute(f"SELECT * FROM {table}")
                        raw[table]=[dict(r) for r in cur.fetchall()]
                    save("postgres-final-scenes.json",raw)
                assert sorted(raw[settings.database.postgres_recording_event_table], key=lambda row: row["seq"]) == original_rows
                archived_response=await client.post(f"/projects/{project_id}/archive")
                archived_response.raise_for_status()
                assert (await client.post(url,json={**creation,"request_id":str(uuid4())})).status_code==409
                assert (await client.get(detail_url)).status_code==200
                if scene_probe is not None:
                    await scene_probe(stage="archived", settings=settings, client=client, evidence=evidence,
                        project_ids=project_ids, version=v2)
                save("facts.json",{"scene_heads":len(raw[settings.database.postgres_recording_scene_table]),"scene_versions":len(raw[settings.database.postgres_recording_scene_version_table]),"original_events_unchanged":True,"postgres_original_rows_unchanged":True,"excluded_sequence":excluded,"project_isolation":404,"archived_write":409,"race_statuses":[201,409],"default_ai_invoked":False})
    finally:
        if graph:
            for project_id in project_ids:
                graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n", {"project_id":project_id})
            graph.close()
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json","postgres-finally.json"}: shutil.copyfile(path, evidence/path.name)

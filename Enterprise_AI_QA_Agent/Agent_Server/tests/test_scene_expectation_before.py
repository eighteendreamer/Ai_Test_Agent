"""Legacy receipt compatibility, after the original failing reproduction was saved."""
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright
import pytest

from src.schemas.case_execution import AgentCaseExecutionReceipt
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_EXPECTATION") != "1",
    reason="actual new recording/main/API/scene/Chromium")]


async def test_original_receipt_rejects_scene_reference_without_protocol_upgrade(tmp_path):
    async def probe(*, stage, settings, client, evidence, project_ids, version):
        if stage != "initial":
            return
        def save(name,value):
            (evidence/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
        root=f"/recordings/scenes/projects/{project_ids[0]}"
        result=await client.post(root,json={"request_id":str(uuid4()),
            "recording_id":version["version"]["source"]["recording_id"]})
        result.raise_for_status(); fresh=result.json()
        content=fresh["version"]["content"]
        content["name"]="逐项回执来源场景"
        content["assertions"]=[{"kind":"page","target":"#count","operator":"equals","expected":2,
            "description":"点击两次后计数为2"}]
        result=await client.post(root+f"/{fresh['scene_id']}/versions",json={"request_id":str(uuid4()),
            "parent_version_id":fresh["version"]["id"],"change_reason":"明确填写业务预期","content":content})
        result.raise_for_status(); scene=result.json()
        save("scene-expectation-new-asset.json",scene)
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch()
            try:
                page=await browser.new_page()
                await page.goto((tmp_path/"scene.html").as_uri())
                await page.locator("#repeat").click(); await page.locator("#repeat").click()
                observed=int(await page.locator("#count").inner_text())
                await page.screenshot(path=str(evidence/"scene-expectation-observation.png"))
                save("scene-expectation-direct-observation.json",{"actual_count":observed,
                    "source":"actual DOM after two clicks; not persisted ToolRuntime evidence"})
                assert observed==2
            finally:
                await browser.close()
        v=scene["version"]
        candidate={"schema_version":1,"steps":[{"item_key":"target","status":"passed","summary":"计数核对",
            "checks":[{"name":"两次点击计数","expected_source":{
                "evidence_id":f"ui_recording_scene:{scene['scene_id']}:{v['id']}:{v['content_hash']}",
                "content_hash":v["content_hash"],"payload_path_tokens":["content","assertions","0","expected"]},
                "operator":"equals","observations":[{"call_id":"ui-read-count","output_path_tokens":["page","count"]}]}]}]}
        save("scene-expectation-proposed-receipt.json",candidate)
        parsed=None
        try:
            parsed=AgentCaseExecutionReceipt.model_validate(candidate)
        except ValueError as exc:
            save("scene-expectation-schema-error.json",{"error":str(exc),"new_scene_exists":True,"actual_count":observed})
        assert parsed is None,"Old receipt must not silently adopt a new scene criterion contract"
    await actual_recording_scene_management(tmp_path,scene_probe=probe,additional_scenes=1)

"""Formal App, production API and actual isolated DBs; no model judgement is needed."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import expect

from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_case_json_templates import _draft
from test_document_full_product import product_browser
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_VERSION_REVIEWS_PRODUCT") != "1",
    reason="formal App/production API and actual isolated MySQL/PG/RustFS/Redis")]


async def test_formal_project_version_review_lost_reply_later_active_and_restart(tmp_path):
    folder = Path(os.environ["QA_CASE_VERSION_REVIEWS_PRODUCT_EVIDENCE_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    try:
        async with controlled_http(tmp_path) as (business, model_calls, *_):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                # Production wires the atomic embedding Outbox only for a registered stream.
                settings.orchestration.redis_task_outbox_streams = settings.orchestration.embedding_task_stream
                assert settings.orchestration.embedding_task_stream in settings.orchestration.task_stream_names
                models = MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-version-review", provider="openai",
                    transport="openai_chat_completions", base_url=business+"/v1", api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                arguments = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api_process = processes.start("version-review-api-first", *arguments)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                    await wait_api(client, api_process)
                    async def post(path, payload, status=200):
                        response = await client.post(path, json=payload)
                        assert response.status_code == status, response.text
                        return response.json()
                    async def get(path):
                        response = await client.get(path)
                        assert response.status_code == 200, response.text
                        return response.json()
                    project = await post("/projects", {"project_key": "review-"+uuid4().hex, "name": "版本评审实际验收"}, 201)
                    source = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-impact-input-final/md/input.json"
                    original = next(v["version"]["content_json"] for v in json.loads(source.read_text(encoding="utf-8"))["input"]["cases"]
                        if v["case"]["case_key"] == "retention")
                    draft_path = tmp_path/"retention.json"
                    draft_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
                    bundle = await post(f"/projects/{project['id']}/test-cases", _draft(case_key="retention",
                        title="保留期限版本治理", content_json=json.loads(draft_path.read_text(encoding="utf-8"))), 201)
                    case_id, v1 = bundle["case"]["id"], bundle["version"]
                    base = f"/test-cases/{case_id}"
                    await post(base+"/submit-review", {"version_id": v1["id"], "request_id": "initial-submit"})
                    await post(base+"/activate", {"version_id": v1["id"], "request_id": "initial-enable", "expected_active_version_id": None})
                    version_payload = {key: v1[key] for key in ("content_json", "execution_notes", "generation_metadata", "source_refs", "model_key", "prompt_version", "skill_versions")}
                    async with product_browser(tmp_path, port, folder) as (page, errors, network):
                        await page.locator('.left-nav a[href="/projects"]').click()
                        await page.locator(".project-card").filter(has_text=project["name"]).click()
                        row = page.locator("tbody tr").filter(has_text="保留期限版本治理")
                        await row.get_by_role("button", name="评审与启用", exact=True).click()
                        dialog = page.get_by_role("dialog", name="测试用例内容")
                        await expect(dialog).to_contain_text("当前启用")
                        await dialog.get_by_role("button", name="编辑新版本", exact=True).click()
                        updated = {**original, "目标": "验证订单保留9天"}
                        await dialog.get_by_label("完整 JSON", exact=True).fill(json.dumps(updated, ensure_ascii=False))
                        async with page.expect_response(lambda r:r.request.method=="POST" and r.url.endswith(base+"/versions")) as saved:
                            await dialog.get_by_role("button", name="保存新草稿版本", exact=True).click()
                        reply = await saved.value
                        assert reply.status == 201, await reply.text()
                        v2 = await reply.json()
                        await expect(dialog.get_by_role("button", name="提交当前版本评审", exact=True)).to_be_visible()
                        assert (await get(base))["active_version_id"] == v1["id"]
                        candidates = await get(f"/projects/{project['id']}/test-cases?status=active")
                        assert [c["id"] for c in candidates["items"]] == [case_id]
                        suite = await post(f"/projects/{project['id']}/suites", {"name": "V1 固定套件", "items": [{"case_id": case_id, "case_version_id": v1["id"]}]}, 201)
                        run = await post(f"/suites/{suite['suite']['id']}/runs", {"mode_key": "api_testing"}, 201)
                        async with page.expect_response(lambda r:r.request.method=="POST" and r.url.endswith(base+"/submit-review")):
                            await dialog.get_by_role("button", name="提交当前版本评审", exact=True).click()
                        await expect(dialog.get_by_role("button", name="启用当前版本", exact=True)).to_be_visible()
                        v3 = await post(base+"/versions", version_payload, 201)
                        invalid = await client.post(base+"/activate", json={"version_id": v3["id"]})
                        assert invalid.status_code == 409
                        captured = []
                        lost_reply = asyncio.Event()
                        async def lose_reply(route):
                            payload = route.request.post_data_json
                            captured.append(payload)
                            assert payload["version_id"] == v2["id"]
                            response = await route.fetch()
                            assert response.status == 200, await response.text()
                            await post(base+"/submit-review", {"version_id": v3["id"], "request_id": "other-review"})
                            await post(base+"/activate", {"version_id": v3["id"], "request_id": "other-activate"})
                            await route.abort("failed")
                            lost_reply.set()
                        await page.route("**"+base+"/activate", lose_reply)
                        await dialog.get_by_role("button", name="启用当前版本", exact=True).click()
                        await asyncio.wait_for(lost_reply.wait(), timeout=20)
                        await expect(dialog.get_by_role("alert")).to_be_visible()
                        await expect(dialog).to_contain_text("尚待核对")
                        await page.reload()
                        row = page.locator("tbody tr").filter(has_text="保留期限版本治理")
                        await row.get_by_role("button", name="评审与启用", exact=True).click()
                        dialog = page.get_by_role("dialog", name="测试用例内容")
                        await expect(dialog).not_to_contain_text("尚待核对")
                        await expect(dialog.locator("select option").filter(has_text="v3")).to_contain_text("已启用")
                        assert len(captured) == 1
                        assert await page.evaluate("Object.keys(sessionStorage).filter(k=>k.startsWith('case-version-review:')).length") == 0
                        await page.unroute("**"+base+"/activate", lose_reply)
                        await page.screenshot(path=str(folder/"later-active.png"), full_page=True)
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_embedding_job_table}")
                            jobs_before = cur.fetchone()["n"]
                        api_process.terminate()
                        forced_stop_code = await processes.wait(api_process)
                        # Explicit termination is not a clean server exit on Windows.
                        # Recovery is proved by process termination + a new API reading the same records.
                        assert api_process.poll() is not None
                        restarted = processes.start("version-review-api-restarted", *arguments)
                        await wait_api(client, restarted)
                        replay = await post(base+"/activate", captured[0])
                        assert replay["active_version_id"] == v3["id"] and replay["latest_version"] == 3
                        assert (await get(base+"/versions"))[0] == v1
                        frozen = await get(f"/runs/{run['run']['id']}")
                        assert frozen["items"][0]["case_version_id"] == v1["id"]
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_embedding_job_table}")
                            assert cur.fetchone()["n"] == jobs_before == 3
                        assert errors == [] and model_calls == []
                        (folder/"result.json").write_text(json.dumps({"current_case": replay, "original": v1,
                            "v2": v2, "v3": v3, "fixed_run": frozen, "original_request": captured[0],
                            "embedding_jobs": jobs_before, "forced_stop_code": forced_stop_code,
                            "embedding_stream": settings.orchestration.embedding_task_stream,
                            "browser_errors": errors, "model_calls": len(model_calls)},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for extension in ("stdout", "stderr"):
            for file in tmp_path.rglob("*."+extension):
                if file.parent == folder:
                    continue
                shutil.copy2(file, folder/(file.parent.name+"-"+file.name))

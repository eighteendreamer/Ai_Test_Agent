"""Built App and production API recovery using controlled, saved candidate plans.

This does not test a model discovering a gap or deciding to revise a Run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import expect

from src.application.test_cases.planning_store import PostgresCasePlanStore
from src.application.test_runs.run_store import PostgresTestRunStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_management import TestCaseVersionRecord as CaseVersionRecord
from src.schemas.case_planning import CasePlanRecord, PlanningDecision, PlanningEvidence
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_case_json_templates import _draft
from test_case_plan_run_creation import (
    _wait_for_blocked_revision_insert,
    _wait_for_postgres_backend_exit,
    fixture_plan,
)
from test_document_full_product import product_browser
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api
from test_case_planning import finish, item


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_RUN_PLAN_REVISION_PRODUCT") != "1",
    reason="built App, production API restart and isolated MySQL/PostgreSQL/Redis",
)]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def saved_plan(client, settings, project_id, bundles, *, revised):
    evidence = []
    for bundle in bundles:
        case, version = bundle["case"], CaseVersionRecord.model_validate(bundle["version"])
        evidence.append(PlanningEvidence(id=f"case:{version.id}", kind="case",
            source_id=case["id"], version=version.id, content_hash=version.content_hash,
            payload={"case_id": case["id"], "case_version_id": version.id,
                "case_key": case["case_key"], "title": case["title"], "historical_mode_key": case["mode_key"],
                "content": version.business_content(), "execution_notes": version.execution_notes,
                "source_refs": [ref.model_dump(mode="json") for ref in version.source_refs]}))
    context = {"evidence": [entry.model_dump(mode="json") for entry in evidence]}
    decision = finish(
        item(context, "item-0", key="create", kind="dependency"),
        item(context, "item-1", key="left", depends_on=["create"]),
        item(context, "item-3", key="independent"),
        item(context, "item-4", key="extra"),
    ) if revised else fixture_plan(context)
    policy_response = await client.get(f"/projects/{project_id}/case-plans/policy")
    assert policy_response.status_code == 200, policy_response.text
    record = CasePlanRecord(id=str(uuid4()), project_id=project_id,
        request={"objective": "验证运行中未领取部分的计划修订"}, status="planned",
        summary="受控候选；只验证持久化和产品恢复，不代表模型规划验收",
        decision=PlanningDecision.model_validate(decision), evidence=evidence,
        config_snapshot=policy_response.json(), model_key="manual",
        created_at=datetime.now(timezone.utc))
    stored = await PostgresCasePlanStore(settings).save(record)
    response = await client.get(f"/projects/{project_id}/case-plans/{stored.id}")
    assert response.status_code == 200 and response.json()["id"] == stored.id
    return stored


async def open_run(page, project):
    await page.locator('.left-nav a[href="/projects"]').click()
    await page.locator(".project-card").filter(has_text=project["name"]).click()
    await page.get_by_role("button", name="测试历史", exact=True).click()
    await page.get_by_role("button", name="详情", exact=True).click()
    panel = page.get_by_role("region", name="运行计划修订")
    await expect(panel.get_by_role("button", name="刷新历史", exact=True)).to_be_enabled()
    return panel


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
async def test_run_revision_browser_lost_reply_and_api_restart(tmp_path, boundary):
    folder = Path(os.environ["QA_RUN_PLAN_REVISION_PRODUCT_EVIDENCE_DIR"]) / boundary
    folder.mkdir(parents=True, exist_ok=True)
    try:
        async with controlled_http(tmp_path) as (business, model_calls, business_effects, *_):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                models = MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="controlled-run-revision", provider="openai",
                    transport="openai_chat_completions", base_url=business+"/v1", api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False,
                    capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                arguments = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api_process = processes.start("run-revision-api-first", *arguments)
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

                    project = await post("/projects", {"project_key": "revision-"+uuid4().hex,
                        "name": "运行计划修订实测-"+boundary}, 201)
                    bundles = []
                    for number, title in enumerate(("创建前置订单", "查询左目标", "查询右目标", "独立检查", "新增检查")):
                        source = tmp_path / f"item-{number}.json"
                        write_json(source, {"objective": title, "steps": [{"操作": title, "预期": "保存原始验证依据"}]})
                        shutil.copy2(source, folder/source.name)
                        bundle = await post(f"/projects/{project['id']}/test-cases", _draft(
                            case_key=f"item-{number}", title=title,
                            content_json=json.loads(source.read_text(encoding="utf-8"))), 201)
                        base = f"/test-cases/{bundle['case']['id']}"
                        await post(base+"/submit-review", {"version_id": bundle["version"]["id"], "request_id": "review"})
                        await post(base+"/activate", {"version_id": bundle["version"]["id"], "request_id": "enable"})
                        bundles.append(bundle)
                    plan = await saved_plan(client, settings, project["id"], bundles, revised=False)
                    candidate = await saved_plan(client, settings, project["id"], bundles, revised=True)
                    write_json(folder/"input.json", {"project": project, "cases": bundles,
                        "plan": plan.model_dump(mode="json"), "candidate": candidate.model_dump(mode="json")})
                    created = await post(f"/projects/{project['id']}/case-plans/{plan.id}/runs",
                        {"creation_key": "product-revision", "mode_key": "api_testing"}, 201)
                    run_id = created["run"]["id"]
                    claims = (await post(f"/runs/{run_id}/claim", {"worker_id": "before-revision",
                        "limit": 2, "lease_seconds": 3600}))["claims"]
                    assert {claim["item"]["plan_item_key"] for claim in claims} == {"create", "independent"}
                    create_claim = next(claim for claim in claims if claim["item"]["plan_item_key"] == "create")
                    item_id = create_claim["item"]["id"]
                    lease = {"lease_token": create_claim["lease_token"]}
                    await post(f"/run-items/{item_id}/start", lease)
                    checkpoint = await post(f"/run-items/{item_id}/checkpoint", {**lease,
                        "checkpoint_key": "before-revision", "checkpoint_payload": {"progress": "already-started"},
                        "checkpoint_version": 1})
                    original = await get(f"/runs/{run_id}")
                    original_suite = await get(f"/suites/{created['run']['suite_id']}")
                    store = PostgresTestRunStore(settings)
                    path = f"/projects/{project['id']}/runs/{run_id}/plan-revisions"
                    captured, upstream = [], []
                    lost_reply = asyncio.Event()

                    async with product_browser(tmp_path, port, folder) as (page, errors, network):
                        panel = await open_run(page, project)
                        await panel.get_by_label("候选计划编号", exact=True).fill(candidate.id)
                        await panel.get_by_label("修订原因", exact=True).fill("保留已领取前置，调整未领取的查询并增加独立检查")

                        async def lose_reply(route):
                            if route.request.method != "POST":
                                await route.continue_()
                                return
                            captured.append(route.request.post_data_json)
                            try:
                                response = await route.fetch(timeout=60000)
                                text = await response.text()
                                try:
                                    body = {"body": json.loads(text)}
                                except json.JSONDecodeError:
                                    body = {"body_text": text}
                                upstream.append({"status": response.status, **body})
                            except Exception as exc:
                                upstream.append({"transport_error": type(exc).__name__})
                            finally:
                                await route.abort("failed")
                                lost_reply.set()

                        await page.route("**/api/v1"+path, lose_reply)
                        backend_pid = None
                        if boundary == "before_commit":
                            with postgres_connect(settings) as blocker, blocker.cursor() as cur:
                                cur.execute(f"LOCK TABLE {store._plan_revision_table} IN SHARE MODE")
                                cur.execute("SELECT pg_backend_pid() AS pid")
                                blocker_pid = int(cur.fetchone()["pid"])
                                await panel.get_by_role("button", name="提交计划修订", exact=True).click()
                                backend_pid = await _wait_for_blocked_revision_insert(
                                    settings, blocker_pid, store._plan_revision_table, api_process)
                                assert (await get(f"/runs/{run_id}"))["run"]["plan_revision"] == 0
                                assert await get(path) == []
                                api_process.terminate()
                                stop_code = await processes.wait(api_process)
                                # As in the existing writer-process test, a Windows TCP
                                # client exit alone does not abort a blocked PG backend.
                                with postgres_connect(settings) as terminator, terminator.cursor() as terminate_cur:
                                    terminate_cur.execute("SELECT pg_terminate_backend(pid) AS terminated FROM pg_stat_activity "
                                        "WHERE pid=%s AND datname=current_database()", (backend_pid,))
                                    row = terminate_cur.fetchone()
                                    assert row is None or row["terminated"] is True
                                await _wait_for_postgres_backend_exit(settings, backend_pid)
                            await asyncio.wait_for(lost_reply.wait(), timeout=20)
                        else:
                            await panel.get_by_role("button", name="提交计划修订", exact=True).click()
                            await asyncio.wait_for(lost_reply.wait(), timeout=20)
                            assert upstream[0]["status"] == 200
                            assert len(await get(path)) == 1
                            api_process.terminate()
                            stop_code = await processes.wait(api_process)
                        assert api_process.poll() is not None and len(captured) == 1
                        await expect(panel).to_contain_text("提交结果尚未确认")
                        await expect(panel.get_by_label("候选计划编号", exact=True)).to_be_disabled()
                        storage_key = f"run-plan-revision:{project['id']}:{run_id}"
                        assert json.loads(await page.evaluate("key => sessionStorage.getItem(key)", storage_key)) == captured[0]
                        await page.screenshot(path=str(folder/"uncertain.png"), full_page=True)
                        await page.unroute("**/api/v1"+path, lose_reply)
                        restarted = processes.start("run-revision-api-restarted", *arguments)
                        await wait_api(client, restarted)
                        after_restart = await get(f"/runs/{run_id}")
                        assert after_restart["run"]["plan_revision"] == (0 if boundary == "before_commit" else 1)
                        if boundary == "before_commit":
                            assert after_restart["items"] == original["items"]
                            assert after_restart["attempts"] == original["attempts"]
                            assert after_restart["run"]["plan_snapshot"] == original["run"]["plan_snapshot"]
                            assert await get(path) == []
                        await page.reload()
                        panel = await open_run(page, project)
                        if boundary == "before_commit":
                            retry = panel.get_by_role("button", name="重试原修订", exact=True)
                            await expect(retry).to_be_enabled()
                            async with page.expect_response(lambda response: response.request.method == "POST"
                                and response.url.endswith(path)) as retried:
                                await retry.click()
                            response = await retried.value
                            assert response.status == 200 and response.request.post_data_json == captured[0]
                        await expect(panel).to_contain_text("当前修订号 1")
                        await expect(panel).to_contain_text("新增 1 · 更新 1 · 停用 1")
                        assert await page.evaluate("key => sessionStorage.getItem(key)", storage_key) is None
                        assert await get(path) == [await post(path, captured[0])]
                        rejected = await client.post(path, json={**captured[0], "reason": "同键异请求"})
                        assert rejected.status_code == 409, rejected.text
                        stale = await client.post(path, json={**captured[0], "idempotency_key": "stale-revision"})
                        assert stale.status_code == 409, stale.text
                        detail = await get(f"/runs/{run_id}")
                        assert detail["run"]["plan_revision"] == 1 and len(detail["items"]) == 5
                        assert detail["run"]["stats"]["cancelled"] == 1
                        assert await get(f"/suites/{created['run']['suite_id']}") == original_suite
                        for claim in claims:
                            original_item = next(entry for entry in original["items"] if entry["id"] == claim["item"]["id"])
                            assert next(entry for entry in detail["items"] if entry["id"] == original_item["id"]) == original_item
                        actual_attempt = next(attempt for attempt in detail["attempts"] if attempt["id"] == checkpoint["id"])
                        assert actual_attempt == checkpoint
                        frozen = await store.get_execution_detail(run_id, item_id, checkpoint["id"])
                        assert frozen.run.plan_snapshot.plan.id == plan.id
                        assert detail["run"]["plan_snapshot"]["plan"]["id"] == candidate.id
                        assert len(detail["results"]) == 0
                        await expect(page.locator(".run-items-table tbody tr")).to_have_count(5)
                        await expect(page.locator(".run-items-table")).to_contain_text("cancelled")
                        next_claims = (await post(f"/runs/{run_id}/claim", {"worker_id": "after-revision",
                            "limit": 10, "lease_seconds": 3600}))["claims"]
                        assert [entry["item"]["plan_item_key"] for entry in next_claims] == ["extra"]
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT plan_snapshots FROM {store._run_table} WHERE id=%s", (run_id,))
                            assert len(cur.fetchone()["plan_snapshots"]) == 2
                            cur.execute(f"SELECT count(*) AS n FROM {store._plan_revision_table} WHERE run_id=%s", (run_id,))
                            assert cur.fetchone()["n"] == 1
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                                (f"test_run_dispatch:{run_id}",))
                            assert cur.fetchone()["n"] == 1
                        assert errors == [] and model_calls == [] and business_effects == []
                        write_json(folder/"result.json", {"scope": "saved controlled plans; actual product and production API restart",
                            "boundary": boundary, "run_id": run_id, "request": captured[0], "upstream": upstream,
                            "forced_stop_code": stop_code, "terminated_owned_backend": backend_pid,
                            "revision_after_restart_before_recovery": after_restart["run"]["plan_revision"],
                            "item_count_after_restart_before_recovery": len(after_restart["items"]),
                            "revision": await get(path), "stats": detail["run"]["stats"],
                            "attempt_plan_id": frozen.run.plan_snapshot.plan.id,
                            "current_plan_id": candidate.id, "checkpoint_preserved": actual_attempt == checkpoint,
                            "suite_preserved": True, "snapshot_count": 2, "dispatch_intent_count": 1,
                            "new_claim_keys": [entry["item"]["plan_item_key"] for entry in next_claims],
                            "model_calls": len(model_calls), "business_http_calls": len(business_effects)})
                        await page.screenshot(path=str(folder/"recovered.png"), full_page=True)
    finally:
        for file in tmp_path.rglob("*"):
            if file.is_file() and (file.suffix in {".stdout", ".stderr"} or file.name == "docker.json"):
                shutil.copy2(file, folder/(file.parent.name+"-"+file.name))

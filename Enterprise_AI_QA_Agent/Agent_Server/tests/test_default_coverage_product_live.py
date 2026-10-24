"""Built product CasePlanDialog drives a real default-model plan, launch and execution."""
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
from pydantic import SecretStr
from playwright.async_api import async_playwright, expect

from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.registry.modes import ModeRegistry
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_agent_case_execution import regression_business_http
from test_default_coverage_production import multi_case_dependency_fixture
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_COVERAGE_PRODUCT") != "1",
    reason="real default model via the product CasePlanDialog, production API/workers, real browser")]


async def test_product_dialog_plans_launches_and_executes_with_default_model(tmp_path,
    regression_business_http):
    evidence = Path(os.environ["QA_DEFAULT_COVERAGE_PRODUCT_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    endpoint, effects, _ = regression_business_http[0]
    orders, created = regression_business_http[1:]

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"model_id": configured.model_id, "transport": configured.transport,
             "configured_parameters": configured_parameters, "controlled_model": False,
             "product_planning": None, "product_execution": None}
    web_root = Path(__file__).resolve().parents[2] / "agent_web"
    node = shutil.which("node")
    assert node
    vite = None
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            # Keep the fixture-registered per-runtime streams and append the
            # document parse stream this scenario's upload flow depends on.
            settings.orchestration.redis_task_outbox_streams = ",".join(
                [settings.orchestration.redis_task_outbox_streams, DOCUMENT_PARSE_STREAM])
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=configured.model_id, provider=configured.provider, transport=configured.transport,
                request_profile=configured.request_profile, base_url=configured.api_base_url,
                api_key=configured.api_key, auth_type=configured.auth_type,
                oauth_provider=configured.oauth_provider, oauth_refresh_token=configured.oauth_refresh_token,
                applications=configured.applications, is_active=True, use_provider_defaults=False,
                capability_overrides=configured.capability_overrides, context_window=configured.context_window,
                max_output_tokens=configured.max_output_tokens))
            assert model_invocation_parameters(models.get_default_active()) == configured_parameters
            save("model-parameters.json", configured_parameters)
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            api_port = free_port()
            api = processes.start("coverage-product-api", "-m", "tests.default_model_probe", "uvicorn",
                "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=300,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)

                    async def post(path, body=None, status=200):
                        response = await client.post(path, json=body)
                        assert response.status_code == status, (path, response.status_code, response.text)
                        return response.json()

                    async def get(path):
                        response = await client.get(path)
                        assert response.status_code == 200, (path, response.status_code, response.text)
                        return response.json()

                    async def relay(stream, limit=1):
                        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                            group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                        await queue.connect()
                        try:
                            return await PostgresTaskOutbox(settings).relay_once(queue, limit=limit)
                        finally:
                            await queue.close()

                    project = await post("/projects", {"project_key": "coverage-product-" + uuid4().hex[:10],
                        "name": "产品页面默认模型用例执行", "base_url": endpoint}, 201)
                    root = "/projects/" + project["id"]
                    document, objective, candidate_specs = multi_case_dependency_fixture(endpoint)
                    uploaded = await post("/registry/api-docs/upload", {"project_id": project["id"],
                        "filename": "requirements.md", "request_id": "product-doc",
                        "content_base64": base64.b64encode(document.encode("utf-8")).decode()})
                    parse_task = await post(root + f"/documents/{uploaded['id']}/parse-tasks",
                        {"creation_key": "product-parse", "version_id": uploaded["current_version_id"]}, 202)
                    assert await relay(DOCUMENT_PARSE_STREAM, 1) == 1
                    parser = processes.start("product-parser", "-m", "src.cli.run_document_parse_worker",
                        "--once", "--worker-id", "product-parser")
                    assert await processes.wait(parser, timeout=120) == 0
                    candidate_ids = []
                    for spec in candidate_specs:
                        case = await post(root + "/test-cases", {"case_key": spec["case_key"],
                            "title": spec["title"], "mode_key": "api_testing", "case_type": "json",
                            "content_json": spec["content"], "execution_notes": "完整读取用例 JSON 并核对业务结果",
                            "source_refs": [{"source_type": "api_doc", "source_id": uploaded["id"]}],
                            "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"}}, 201)
                        case_id = case["case"]["id"]
                        await post(f"/test-cases/{case_id}/submit-review", {})
                        await post(f"/test-cases/{case_id}/activate", {})
                        candidate_ids.append(case_id)

                    # Serve the real product CasePlanDialog against the production API.
                    host = web_root / f"__default_coverage_probe_{uuid4().hex}.html"
                    assert not host.exists()
                    host.write_text('''<html><head><meta charset="utf-8"><style>body{font-family:sans-serif}</style></head><body><div id="app"></div><script type="module">
import { createApp } from 'vue';
import CasePlanDialog from '/src/features/projects/CasePlanDialog.vue';
createApp(CasePlanDialog, { projectId: ''' + json.dumps(project["id"]) + ''', candidateCaseIds: ''' + json.dumps(candidate_ids) + ''', modes: ''' + json.dumps([m.model_dump(mode="json") for m in ModeRegistry().list() if m.key == 'api_testing']) + ''', onStarted: detail => { document.body.dataset.openedRun = detail.run.id; } }).mount('#app');
</script></body></html>''', encoding="utf-8")
                    port_socket = socket.socket(); port_socket.bind(("127.0.0.1", 0))
                    web_port = port_socket.getsockname()[1]; port_socket.close()
                    vite = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"),
                        "--port", str(web_port)], cwd=str(web_root),
                        env={**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{api_port}",
                             # The proxy injects this bearer token into /api calls,
                             # authenticating the product page end to end.
                             "API_AUTH_TOKEN": settings.api_auth_token.get_secret_value()},
                        stdout=(tmp_path / "vite.stdout").open("wb"), stderr=(tmp_path / "vite.stderr").open("wb"))
                    url = f"http://127.0.0.1:{web_port}/{host.name}"
                    async with httpx.AsyncClient() as probe:
                        async with asyncio.timeout(60):
                            while True:
                                assert vite.poll() is None, (tmp_path / "vite.stderr").read_text(errors="replace")[-1000:]
                                try:
                                    if (await probe.get(url)).status_code == 200:
                                        break
                                except httpx.ConnectError:
                                    pass
                                await asyncio.sleep(.2)

                    async with async_playwright() as playwright:
                        browser = await playwright.chromium.launch()
                        try:
                            page = await browser.new_page(viewport={"width": 1360, "height": 980})
                            page_errors = []
                            page.on("pageerror", lambda error: page_errors.append(str(error)))
                            await page.goto(url)
                            await expect(page.get_by_label("测试目标")).to_be_enabled()
                            await page.get_by_label("测试目标").fill(objective)
                            async with page.expect_response(
                                lambda response: response.request.method == "POST"
                                and "/case-planning-tasks" in response.url) as response_info:
                                await page.get_by_role("button", name="生成执行计划", exact=True).click()
                            submitted = await (await response_info.value).json()
                            task_id = submitted["id"]
                            # A real model's plan can fail schema validation; the
                            # product path for that is the user re-planning from the
                            # dialog. Bound the acceptance to two attempts.
                            for attempt in range(2):
                                assert await relay(settings.orchestration.case_planning_task_stream, 1) == 1
                                planning_worker = processes.start("product-planning-worker", "-m",
                                    "tests.default_model_probe", "src.cli.run_case_planning_worker",
                                    "--once", "--worker-id", f"product-planning-{attempt}")
                                assert await processes.wait(planning_worker, timeout=600) == 0, (
                                    (tmp_path / "product-planning-worker.stderr").read_text(errors="replace")[-2000:])
                                done = await get(root + "/case-planning-tasks/" + task_id)
                                if done["status"] == "completed":
                                    break
                                assert done["status"] == "failed" and attempt == 0, done
                                save("planning-failed-attempt.json", done)
                                await page.get_by_role("button", name="另建规划任务").click()
                                async with page.expect_response(
                                    lambda response: response.request.method == "POST"
                                    and "/case-planning-tasks" in response.url) as retry_info:
                                    await page.get_by_role("button", name="生成执行计划", exact=True).click()
                                task_id = (await (await retry_info.value).json())["id"]
                            assert done["status"] == "completed", done
                            plan = await get(root + "/case-plans/" + done["plan_id"])
                            save("product-plan.json", plan)
                            assert plan["status"] == "planned" and plan["decision"]["items"], plan

                            reopen = web_root / f"__default_coverage_probe_{uuid4().hex}.html"
                            reopen.write_text('''<html><head><meta charset="utf-8"></head><body><div id="app"></div><script type="module">
import { createApp } from 'vue';
import CasePlanDialog from '/src/features/projects/CasePlanDialog.vue';
createApp(CasePlanDialog, { projectId: ''' + json.dumps(project["id"]) + ''', candidateCaseIds: ''' + json.dumps(candidate_ids) + ''', modes: ''' + json.dumps([m.model_dump(mode="json") for m in ModeRegistry().list() if m.key == 'api_testing']) + ''', initialTaskId: ''' + json.dumps(task_id) + ''', onStarted: detail => { document.body.dataset.openedRun = detail.run.id; } }).mount('#app');
</script></body></html>''', encoding="utf-8")
                            await page.goto(f"http://127.0.0.1:{web_port}/{reopen.name}")
                            await expect(page.get_by_text("计划已生成")).to_be_visible(timeout=30000)
                            await page.get_by_role("button", name="启动测试运行").click()
                            await expect(page.get_by_text("已保存运行")).to_be_visible(timeout=30000)
                            run_id = await page.evaluate("document.body.dataset.openedRun")
                            assert run_id, "dialog did not report the launched run"
                            save("product-launch.json", {"task_id": task_id, "plan_id": plan["id"], "run_id": run_id})
                            facts["product_planning"] = {"task_id": task_id, "plan_id": plan["id"],
                                "model_http": model_sends() - 0, "dialog": "CasePlanDialog"}
                        finally:
                            await browser.close()

                    assert await relay(settings.orchestration.redis_task_stream, 1) >= 1
                    loop = asyncio.get_event_loop()
                    deadline = loop.time() + 480
                    run_queue = RedisTaskQueue(settings.database.redis_url,
                        stream=settings.orchestration.redis_task_stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await run_queue.connect()
                    try:
                        detail = await get("/runs/" + run_id)
                        while detail["run"]["status"] not in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
                            assert loop.time() < deadline, detail
                            waiting = next((i for i in detail["items"] if i["status"] == "waiting_approval"), None)
                            if waiting is not None:
                                await post("/run-items/" + waiting["id"] + "/approval",
                                    {"approval_id": waiting["approval_id"], "decision": "approved"})
                                detail = await get("/runs/" + run_id)
                                continue
                            await relay(settings.orchestration.redis_task_stream, 10)
                            execution_worker = processes.start("product-execution-worker", "-m",
                                "tests.default_model_probe", "src.cli.run_redis_worker",
                                "--once", "--worker-id", "product-execute")
                            assert await processes.wait(execution_worker, timeout=300) == 0, (
                                (tmp_path / "product-execution-worker.stderr").read_text(errors="replace")[-2000:])
                            detail = await get("/runs/" + run_id)
                    finally:
                        await run_queue.close()
                    for _ in range(6):
                        waiting = next((i for i in detail["items"] if i["status"] == "waiting_approval"), None)
                        if waiting is None:
                            break
                        await post("/run-items/" + waiting["id"] + "/approval",
                            {"approval_id": waiting["approval_id"], "decision": "approved"})
                        detail = await get("/runs/" + run_id)
                    save("product-run.json", detail)
                    assert detail["run"]["status"] == "completed", detail["run"]
                    assert all(item["status"] == "passed" for item in detail["items"]), detail["items"]
                    effects_snapshot = list(effects)
                    facts["product_execution"] = {"run_status": detail["run"]["status"],
                        "items": [i["status"] for i in detail["items"]],
                        "business_HTTP": [list(e) for e in effects_snapshot],
                        "model_http": model_sends()}
                    assert any(e[0] == "POST" for e in effects_snapshot) and \
                        any(e[0] == "GET" for e in effects_snapshot) and \
                        any(e[0] == "DELETE" for e in effects_snapshot), effects_snapshot
                    result = detail["results"][0]
                    decision_items = plan["decision"]["items"]
                    assert any(item["kind"] == "dependency" for item in decision_items) and \
                        any(item["kind"] == "target" for item in decision_items), decision_items

                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} "
                            "WHERE event_key=%s", ("test_run_dispatch:" + run_id,))
                        delivery = cur.fetchone()["payload"]
                    replay_before = (model_sends(), json.dumps(detail, sort_keys=True, default=str))
                    run_queue = RedisTaskQueue(settings.database.redis_url,
                        stream=settings.orchestration.redis_task_stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await run_queue.connect()
                    try:
                        await run_queue.enqueue(delivery)
                        terminal = processes.start("product-terminal-worker", "-m", "tests.default_model_probe",
                            "src.cli.run_redis_worker", "--once", "--worker-id", "product-terminal")
                        assert await processes.wait(terminal, timeout=120) == 0
                    finally:
                        await run_queue.close()
                    detail_after = await get("/runs/" + run_id)
                    assert json.dumps(detail_after, sort_keys=True, default=str) == replay_before[1]
                    assert model_sends() == replay_before[0]
            finally:
                if api.poll() is None:
                    await processes.stop(api)
    finally:
        if vite is not None and vite.poll() is None:
            vite.terminate()
        for probe_file in web_root.glob("__default_coverage_probe_*.html"):
            probe_file.unlink(missing_ok=True)
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"}:
                (evidence / path.name).write_bytes(path.read_bytes())
        facts["global_model_unchanged"] = model_invocation_parameters(
            configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)


from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM  # noqa: E402

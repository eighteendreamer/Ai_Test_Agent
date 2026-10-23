"""Configured Agent from a user message to actual k6 and the built report page."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
from time import monotonic
from uuid import uuid4

import httpx
from playwright.async_api import expect
import pytest

from test_document_full_product import product_browser
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api
from src.application.performance.mock_target_runner import MockTargetRunner
from src.core.config import get_settings
from src.core.request_context import bind_request_context
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_PERFORMANCE_AGENT") != "1",
    reason="configured default model, actual k6, isolated stores, API/Agent CLI and built App")]


async def test_default_agent_actual_k6_report_and_terminal_replay(tmp_path):
    evidence = Path(os.getenv("QA_DEFAULT_PERFORMANCE_AGENT_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=False)
    configured_store = MySQLModelConfigStore(get_settings())
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"injected_campaign_or_plan": False, "controlled_model": False,
             "configured_parameters": configured_parameters, "full_goal_complete": False}

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.orchestration.performance_runner_backend = "docker"
            settings.orchestration.performance_default_engine = "k6"
            settings.orchestration.performance_max_vus = 1
            settings.orchestration.performance_max_duration_seconds = 2
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.agent_task_stream
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=configured.model_id, provider=configured.provider, transport=configured.transport,
                request_profile=configured.request_profile, base_url=configured.api_base_url,
                api_key=configured.api_key, auth_type=configured.auth_type, oauth_provider=configured.oauth_provider,
                oauth_refresh_token=configured.oauth_refresh_token, applications=configured.applications,
                is_active=True, use_provider_defaults=False, capability_overrides=configured.capability_overrides,
                context_window=configured.context_window, max_output_tokens=configured.max_output_tokens))
            assert model_invocation_parameters(models.get_default_active()) == configured_parameters
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            api_port = free_port()
            api_args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            api = processes.start("perf-agent-api", *api_args)
            queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_stream,
                group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
            await queue.connect()
            target = MockTargetRunner(settings)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=35) as client:
                    await wait_api(client, api)

                    async def post(path, body, statuses=(200,)):
                        response = await client.post(path, json=body)
                        assert response.status_code in statuses, (path, response.status_code, response.text)
                        return response.json()

                    async def get(path):
                        response = await client.get(path)
                        response.raise_for_status()
                        return response.json()

                    project = await post("/projects", {"project_key": "default-perf-" + uuid4().hex[:10],
                        "name": "默认Agent实际性能报告"}, (201,))
                    session = await post("/sessions", {"project_id": project["id"], "mode_key": "performance_testing",
                        "title": "本地1VU性能验收", "session_mode": "background_task", "runtime_mode": "background",
                        "selected_agent": "performance-testing-agent", "preferred_model": model.key})
                    with bind_request_context(project_id=project["id"], session_id=session["id"]):
                        started = await target.start(port=free_port(), response_text="owned default Agent performance")
                    assert started.ok, started
                    docker = DockerResourceManager(settings)
                    code, address, stderr = await docker._command(["inspect", "--format",
                        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", started.stdout], timeout=20)
                    assert code == 0 and address.strip(), stderr
                    target_url = f"http://{address.strip()}:5678/"
                    message = (f"请对我已确认的隔离测试目标 {target_url} 做一次k6性能回归验证。"
                        "固定1个虚拟用户（closed），持续2秒；SLA P99小于1000ms。无需鉴权、请求体或参数化。"
                        "目标和本次负载已确认，请按现有审批流程执行，保留冒烟验证，保存真实指标、原件和报告。")
                    (evidence / "user-request.txt").write_text(message, encoding="utf-8")
                    accepted = await post(f"/sessions/{session['id']}/messages", {
                        "content": message, "model_key": model.key, "agent_key": "performance-testing-agent"}, (202,))
                    task_id = accepted["task"]["id"]
                    facts.update(project_id=project["id"], session_id=session["id"], task_id=task_id,
                                 target_url=target_url)
                    worker = processes.start("perf-agent-worker", "-m", "tests.default_model_probe",
                        "src.cli.run_agent_worker", "--worker-id", "actual-default-perf")
                    approved, timeline = set(), []
                    deadline = monotonic() + 300
                    while monotonic() < deadline:
                        await PostgresTaskOutbox(settings).relay_once(queue, limit=10)
                        session = await get(f"/sessions/{session['id']}")
                        tasks = await get(f"/sessions/{session['id']}/tasks")
                        sample = {"status": session["status"],
                            "tasks": [{k: t.get(k) for k in ("id", "status", "result_status", "error_code")} for t in tasks],
                            "pending_approval_ids": [a["id"] for a in session["pending_approvals"]]}
                        if not timeline or timeline[-1] != sample:
                            timeline.append(sample)
                            save("timeline.json", timeline)
                        for approval in session["pending_approvals"]:
                            if approval["id"] not in approved and session["status"] == "waiting_approval":
                                await post(f"/sessions/{session['id']}/approvals/{approval['id']}", {
                                    "decision": "approved", "reason": "本次独占本地1VU两秒性能验收"}, (200, 202))
                                approved.add(approval["id"])
                        if (session["status"] in {"completed", "failed", "interrupted", "cancelled"}
                            and all(t["status"] in {"completed", "failed", "interrupted", "cancelled", "recovery_required"}
                                    for t in tasks)):
                            break
                        assert worker.poll() is None, "Agent CLI exited; preserved stdout/stderr"
                        await asyncio.sleep(.25)
                    save("session.json", session)
                    save("tasks.json", tasks)
                    facts.update(session_status=session["status"], approvals=len(approved), model_HTTP=model_sends())
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} ORDER BY created_at")
                        jobs = [dict(row) for row in cur.fetchall()]
                        cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table}")
                        metrics_rows = [dict(row) for row in cur.fetchall()]
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} "
                            "WHERE payload->>'task_id'=%s", (task_id,))
                        replay_command = cur.fetchone()["payload"]
                    save("jobs.json", jobs)
                    save("postgres-metrics.json", metrics_rows)
                    assert session["status"] == "completed", facts
                    reports = [j for j in jobs if j["tool_key"] == "performance-test-runner"
                        and (j.get("output_payload") or {}).get("metrics")]
                    assert len(reports) == 1, "expected one real final performance report"
                    job, output = reports[0], reports[0]["output_payload"]
                    assert output["phase"] == "report_ready" and output["metrics_persistence"]["status"] == "saved"
                    assert output["verdict"] == "pass" and output["metrics"]["samples"] > 0
                    assert all(output["metrics"][key] is not None for key in ("p50_ms", "p95_ms", "p99_ms", "error_rate", "tps"))
                    assert len(metrics_rows) == 1 and metrics_rows[0]["metrics_json"] == output["metrics"]
                    plan = output["performance_testing_state"]["plan"]
                    assert plan["workload"]["virtual_users"] == 1 and plan["workload"]["hold_seconds"] == 2
                    assert plan["targets"][0]["url"] == target_url
                    detail = await get(f"/sessions/{session['id']}/tool-jobs/{job['id']}")
                    save("final-job.json", detail)
                    originals = evidence / "originals"
                    originals.mkdir()
                    downloaded = []
                    for artifact in detail["artifacts"]:
                        response = await client.get(f"/sessions/{session['id']}/artifacts/{artifact['id']}/content")
                        response.raise_for_status()
                        filename = Path(artifact["metadata"].get("filename") or artifact["id"]).name
                        (originals / (artifact["id"] + "-" + filename)).write_bytes(response.content)
                        digest = hashlib.sha256(response.content).hexdigest()
                        if artifact["metadata"].get("evidence_schema") == "performance-execution-v1":
                            assert digest == artifact["metadata"]["sha256"]
                        downloaded.append({"id": artifact["id"], "type": artifact["artifact_type"], "sha256": digest})
                    execution_ids = {a["metadata"]["execution_id"] for a in detail["artifacts"]
                        if a["metadata"].get("execution_id")}
                    assert len(execution_ids) == 2, "expected one smoke and one load execution"
                    save("downloads.json", downloaded)
                    listing = await get("/reports?limit=100")
                    entry = next(i for i in listing["items"] if i["session"]["id"] == session["id"])
                    assert entry["report_meta"]["tool_job_id"] == job["id"]
                    async with product_browser(tmp_path, api_port, evidence) as (page, errors, network):
                        await expect(page.locator(".left-nav")).to_be_visible()
                        await page.locator('.left-nav a[href="/reports"]').click()
                        await page.locator(".batch-item").filter(has_text=session["id"][:8]).click()
                        await expect(page.locator(".report-detail .grade-box")).to_have_text("良好")
                        await expect(page.locator(".report-detail .summary-body").first).to_contain_text("pass")
                        metrics_artifact = next(a for a in detail["artifacts"] if a["artifact_type"] == "performance_metrics_json")
                        async with page.expect_download() as downloaded_json:
                            await page.locator(f'a[href$="/artifacts/{metrics_artifact["id"]}/content"]').click()
                        await (await downloaded_json.value).save_as(str(evidence / "browser-metrics.json"))
                        assert json.loads((evidence / "browser-metrics.json").read_bytes())["metrics"] == output["metrics"]
                        assert not errors and not [r for r in network if r["status"] >= 400]
                    await processes.stop(worker)
                    before = model_sends(), len(jobs), len(metrics_rows)
                    await processes.stop(api)
                    api = processes.start("perf-agent-api-restarted", *api_args)
                    await wait_api(client, api)
                    await queue.enqueue(replay_command)
                    replay = processes.start("perf-agent-terminal", "-m", "tests.default_model_probe",
                        "src.cli.run_agent_worker", "--once", "--worker-id", "actual-default-perf-terminal")
                    assert await processes.wait(replay, timeout=30) == 0
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT count(*) AS count FROM {settings.database.postgres_tool_job_table}")
                        job_count = cur.fetchone()["count"]
                        cur.execute(f"SELECT count(*) AS count FROM {settings.database.postgres_perf_runs_table}")
                        run_count = cur.fetchone()["count"]
                    assert (model_sends(), job_count, run_count) == before
                    facts.update(real_k6_executions=len(execution_ids), metrics=output["metrics"], verdict=output["verdict"],
                        report_job_id=job["id"], downloaded_originals=len(downloaded), built_product_verified=True,
                        terminal_redelivery_extra_model_HTTP=0, terminal_redelivery_extra_jobs=0,
                        terminal_redelivery_extra_perf_runs=0)
            finally:
                await target.close()
                await queue.close()
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {settings.database.postgres_tool_artifact_table}")
                    for artifact in cur.fetchall():
                        if str(artifact.get("path", "")).startswith("rustfs://") and artifact["path"] not in owned:
                            owned.append(artifact["path"])
                    cur.execute(f"SELECT * FROM {settings.database.postgres_resource_allocation_table}")
                    save("allocations.json", [dict(row) for row in cur.fetchall()])
    finally:
        facts["global_model_unchanged"] = model_invocation_parameters(configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

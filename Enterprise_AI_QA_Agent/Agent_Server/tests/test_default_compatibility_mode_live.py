"""Real default model plans a compatibility matrix through the product runtime."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_COMPATIBILITY_MODE") != "1",
    reason="real default model compatibility-mode planning over the production runtime")]


@pytest.fixture
def web_target():
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(("GET", self.path))
            body = b"<html><body><h1>Demo product</h1><button id=go>Go</button></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests_seen
    finally:
        server.shutdown()
        server.server_close()


async def test_default_model_plans_compatibility_matrix(tmp_path, web_target):
    evidence = Path(os.environ["QA_DEFAULT_COMPATIBILITY_MODE_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    target_url, target_requests = web_target

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"model_id": configured.model_id, "transport": configured.transport,
             "configured_parameters": configured_parameters, "controlled_model": False,
             "target_url": target_url}
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.agent_task_stream
            settings.orchestration.redis_task_reclaim_idle_ms = 120000
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
            api = processes.start("compatibility-api", "-m", "tests.default_model_probe", "uvicorn",
                "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_stream,
                group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
            await queue.connect()
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=120,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)

                    async def post(path, body, statuses=(200, 202)):
                        response = await client.post(path, json=body)
                        assert response.status_code in statuses, (path, response.status_code, response.text)
                        return response.json()

                    async def get(path):
                        response = await client.get(path)
                        assert response.status_code == 200, (path, response.status_code, response.text)
                        return response.json()

                    project = await post("/projects", {"project_key": "compat-" + uuid4().hex[:10],
                        "name": "默认模型兼容性规划", "base_url": target_url}, (201,))
                    session = await post("/sessions", {"project_id": project["id"],
                        "mode_key": "compatibility_testing", "title": "兼容性计划验收",
                        "session_mode": "background_task", "runtime_mode": "background",
                        "selected_agent": "compatibility-testing-agent", "preferred_model": model.key})
                    # Same server-owned authority fixture as the other default-model
                    # live acceptances: the private loopback target is trusted for
                    # this session only through its resource scope.
                    from src.runtime.postgres_session_store import PostgresSessionStore
                    session_store = PostgresSessionStore(settings)
                    await session_store.initialize()
                    owned_session = await session_store.get_session(session["id"])
                    owned_session.metadata["resource_scope"] = {
                        "project_id": project["id"], "project_url": target_url}
                    await session_store.save_session(owned_session)
                    message = (f"请为产品入口 {target_url} 制定兼容性测试计划：识别可用的环境矩阵、"
                        "生成的用例与主要风险，只做计划，不要执行。")
                    (evidence / "user-request.txt").write_text(message, encoding="utf-8")
                    accepted = await post(f"/sessions/{session['id']}/messages", {
                        "content": message, "model_key": model.key,
                        "agent_key": "compatibility-testing-agent"}, (202,))
                    task_id = accepted["task"]["id"]
                    worker = processes.start("compatibility-worker", "-m", "tests.default_model_probe",
                        "src.cli.run_agent_worker", "--worker-id", "default-compat")
                    deadline = monotonic() + 420
                    session_view = None
                    while monotonic() < deadline:
                        await PostgresTaskOutbox(settings).relay_once(queue, limit=10)
                        session_view = await get(f"/sessions/{session['id']}")
                        if session_view["status"] in {"completed", "failed", "interrupted", "cancelled"}:
                            break
                        for approval in list(session_view.get("pending_approvals") or []):
                            # 400 = already handled continuation; a stale list from
                            # the previous poll is legitimate.
                            response = await client.post(
                                f"/sessions/{session['id']}/approvals/{approval['id']}",
                                json={"decision": "approved", "reason": "兼容性计划验收批准"})
                            assert response.status_code in (200, 202, 400), (
                                response.status_code, response.text)
                        assert worker.poll() is None, "agent worker exited early"
                        await asyncio_sleep()
                    save("session.json", session_view)
                    assert session_view["status"] == "completed", session_view["status"]

                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT id, tool_key, status, input_payload, output_payload "
                            f"FROM {settings.database.postgres_tool_job_table} ORDER BY created_at")
                        jobs = [dict(row) for row in cur.fetchall()]
                    save("tool-jobs.json", jobs)
                    compat_jobs = [job for job in jobs if job["tool_key"] == "compatibility-test-runner"]
                    assert compat_jobs, jobs
                    plan_job = next((job for job in compat_jobs
                        if (job["input_payload"] or {}).get("action") == "draft_plan"), None)
                    assert plan_job is not None, [j["input_payload"].get("action") for j in compat_jobs]
                    # partial is the honest terminal state when no runner is
                    # registered for the planned environments; the plan itself
                    # must be complete and await explicit user approval.
                    assert plan_job["status"] in {"completed", "partial"}, plan_job["status"]
                    output = plan_job["output_payload"] or {}
                    plan = output.get("plan") or {}
                    save("compatibility-plan.json", plan)
                    assert output.get("phase") == "awaiting_approval", output.get("phase")
                    environments = plan.get("environments") or []
                    cases = plan.get("cases") or []
                    risks = plan.get("risks") or []
                    assert len(environments) >= 3 and len(cases) >= 1, {
                        "environments": len(environments), "cases": len(cases), "risks": len(risks)}
                    unavailable = [item for item in environments if item.get("availability") != "available"]
                    assert unavailable, "runner availability must be reported honestly"
                    assert all(item.get("unavailable_reason") for item in unavailable), environments
                    facts["plan_job_id"] = str(plan_job["id"])
                    facts["plan"] = {"environments": len(environments), "cases": len(cases),
                        "risks": len(risks), "phase": output.get("phase"),
                        "environment_names": [item.get("name") for item in environments][:8]}
                    facts["model_http"] = model_sends()
                    assert facts["model_http"] > 0, "default model made no physical call"

                    sends_before = model_sends()
                    for approval in list(session_view.get("pending_approvals") or []):
                        await post(f"/sessions/{session['id']}/approvals/{approval['id']}",
                            {"decision": "approved", "reason": "兼容性计划验收批准"}, (200, 202))
                        deadline = monotonic() + 180
                        while monotonic() < deadline:
                            await PostgresTaskOutbox(settings).relay_once(queue, limit=10)
                            session_view = await get(f"/sessions/{session['id']}")
                            if session_view["status"] in {"completed", "failed", "interrupted", "cancelled"}:
                                break
                            await asyncio_sleep()
                    facts["approvals"] = len(session_view.get("pending_approvals") or [])
                    facts["model_http_after_approvals"] = model_sends() - sends_before
            finally:
                if worker.poll() is None:
                    await processes.stop(worker)
                await queue.close()
            facts["target_requests"] = [list(r) for r in target_requests]
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"}:
                (evidence / path.name).write_bytes(path.read_bytes())
        facts["global_model_unchanged"] = model_invocation_parameters(
            configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)


async def asyncio_sleep(seconds=0.5):
    import asyncio
    await asyncio.sleep(seconds)

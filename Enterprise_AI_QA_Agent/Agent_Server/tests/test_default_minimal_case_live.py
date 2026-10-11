"""Real default Step acceptance for the smallest project case execution loop."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.settings import ModelConfigUpdateRequest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_MINIMAL_CASE") != "1",
    reason="requires authenticated default Step, isolated MySQL/PostgreSQL/RustFS/Redis and owned processes",
)


class HealthTarget:
    def __init__(self):
        self.requests: list[dict[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append({"method": "GET", "path": self.path})
                body = json.dumps({"ok": True, "status": "ready"}).encode()
                self.send_response(200 if self.path == "/health" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> int:
        self.thread.start()
        return self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.mark.asyncio
async def test_default_step_executes_minimal_project_case_and_terminal_replay_is_idempotent(tmp_path):
    global_settings = get_settings().model_copy(deep=True)
    original_model = MySQLModelConfigStore(global_settings).get_default_active()
    assert original_model is not None and original_model.provider == "openai"
    target = HealthTarget()
    port = target.start()
    evidence = Path(os.getenv("QA_DEFAULT_MINIMAL_CASE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=False)
    last_detail = None
    planning = None
    plan = None
    run_id = None
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned_objects):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=original_model.model_id,
                provider=original_model.provider,
                transport=original_model.transport,
                request_profile=original_model.request_profile,
                base_url=original_model.api_base_url,
                api_key=original_model.api_key,
                auth_type=original_model.auth_type,
                oauth_provider=original_model.oauth_provider,
                oauth_refresh_token=original_model.oauth_refresh_token,
                applications=original_model.applications,
                is_active=True,
                use_provider_defaults=False,
                capability_overrides=original_model.capability_overrides,
                context_window=original_model.context_window,
                max_output_tokens=original_model.max_output_tokens,
            ))
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            api_port = free_port()
            api = processes.start("minimal-api", "-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                "--host", "127.0.0.1", "--port", str(api_port))
            base = f"http://127.0.0.1:{api_port}/api/v1"
            async with httpx.AsyncClient(base_url=base, timeout=300,
                headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                await wait_api(client, api)

                async def post(path, payload, status=200):
                    response = await client.post(path, json=payload)
                    assert response.status_code == status, (response.status_code, response.text)
                    return response.json()

                async def get(path):
                    response = await client.get(path)
                    assert response.status_code == 200, (response.status_code, response.text)
                    return response.json()

                target_url = f"http://127.0.0.1:{port}/health"
                project = await post("/projects", {
                    "project_key": "minimal-default-" + uuid4().hex,
                    "name": "默认Step最小用例验收",
                    "base_url": f"http://127.0.0.1:{port}",
                }, 201)
                root = "/projects/" + project["id"]
                source = tmp_path / "health.md"
                source.write_text(
                    f"# 健康检查\nGET {target_url} 返回 HTTP 200，JSON 的 ok 必须为 true。\n",
                    encoding="utf-8",
                )
                document = await post("/registry/api-docs/upload", {
                    "project_id": project["id"],
                    "filename": source.name,
                    "content_base64": base64.b64encode(source.read_bytes()).decode(),
                }, 200)
                owned_objects.append(document["storage_uri"])
                assert (await objects.read_object_uri(document["storage_uri"]))["content"] == source.read_bytes()
                case = await post(root + "/test-cases", {
                    "case_key": "health-check",
                    "title": "健康检查",
                    "mode_key": "api_testing",
                    "case_type": "json",
                    "content_json": {
                        "objective": "验证健康检查接口",
                        "task": {
                            "task_id": "health-check",
                            "name": "健康检查",
                            "method": "GET",
                            "path": "/health",
                            "full_url": target_url,
                            "execution_mode": "read",
                            "assertions": [
                                {"kind": "status_code", "expected": 200},
                                {"kind": "json_field_equals", "path": "ok", "expected": True},
                            ],
                            "timeout_seconds": 30,
                        },
                        "steps": [
                            {"order": 1, "action": "按 task 执行 GET /health"},
                            {"order": 2, "action": "核对 task.assertions 中的 HTTP 200 与 body.ok=true"},
                        ],
                    },
                    "execution_notes": "只读请求；完整读取用例 JSON 和文档，不需要准备或清理数据。",
                    "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                    "model_key": "manual",
                    "prompt_version": "manual-v1",
                    "skill_versions": {"manual": "v1"},
                }, 201)
                case_id = case["case"]["id"]
                await post(f"/test-cases/{case_id}/submit-review", {}, 200)
                await post(f"/test-cases/{case_id}/activate", {}, 200)
                policy_response = await client.put(root + "/case-plans/policy", json={
                    "receipt_correction_protocol": "case-receipt-correction-v1",
                })
                assert policy_response.status_code == 200, (policy_response.status_code, policy_response.text)

                plan_task = await post(root + "/case-planning-tasks", {
                    "creation_key": "minimal-default-plan",
                    "objective": "验证健康检查接口并核对 ok=true",
                    "model_key": model.key,
                    "candidate_case_ids": [case_id],
                }, 202)
                planning_queue = RedisTaskQueue(settings.database.redis_url,
                    stream=settings.orchestration.case_planning_task_stream,
                    group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                await planning_queue.connect()
                try:
                    assert await PostgresTaskOutbox(settings).relay_once(planning_queue, limit=1) == 1
                    planning_worker = processes.start("minimal-planning-worker", "-m", "src.cli.run_case_planning_worker",
                        "--once", "--worker-id", "minimal-plan")
                    assert await processes.wait(planning_worker, timeout=240) == 0
                finally:
                    await planning_queue.close()
                planning = await get(root + "/case-planning-tasks/" + plan_task["id"])
                assert planning["status"] == "completed", planning
                plan = await get(root + "/case-plans/" + planning["plan_id"])
                assert plan["status"] == "planned" and plan["decision"]["items"], plan
                assert any(item["case_id"] == case_id for item in plan["decision"]["items"])

                detail = await post(root + "/case-plans/" + plan["id"] + "/runs",
                    {"creation_key": "minimal-default-run", "mode_key": "api_testing"}, 201)
                run_id = detail["run"]["id"]
                assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                execution_worker = processes.start("minimal-execution-worker", "-m", "src.cli.run_redis_worker",
                    "--once", "--worker-id", "minimal-execute")
                assert await processes.wait(execution_worker, timeout=420) == 0
                for _ in range(12):
                    detail = await get("/runs/" + run_id)
                    waiting = next((item for item in detail["items"] if item["status"] == "waiting_approval"), None)
                    if waiting is None:
                        break
                    await post("/run-items/" + waiting["id"] + "/approval",
                        {"approval_id": waiting["approval_id"], "decision": "approved"})
                detail = await get("/runs/" + run_id)
                last_detail = detail
                assert target.requests, {"run": detail, "planning": planning, "plan": plan}
                assert all(item["status"] in {"passed", "failed", "error", "blocked", "skipped"} for item in detail["items"])
                assert len(target.requests) == 1, target.requests
                assert len(detail["results"]) == 1
                result = detail["results"][0]
                assert result["status"] == "passed", detail
                assert result["actual"]["case_status"] == "passed", result
                correction_used = "receipt_correction" in result["actual"]

                evidence.joinpath("final-run.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
                evidence.joinpath("plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                evidence.joinpath("target-requests.json").write_text(json.dumps(target.requests, ensure_ascii=False, indent=2), encoding="utf-8")
                evidence.joinpath("verification.json").write_text(json.dumps({
                    "model_key": model.key,
                    "project_id": project["id"],
                    "case_id": case_id,
                    "receipt_correction_protocol": "case-receipt-correction-v1",
                    "receipt_correction_used": correction_used,
                    "planning_status": planning["status"],
                    "run_status": detail["run"]["status"],
                    "result_status": result["status"],
                    "target_requests": target.requests,
                    "terminal_replay_extra_requests": 0,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                # Re-deliver the same terminal dispatch; production should not
                # reclaim a completed item or call the target again.
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE payload->>'run_id'=%s", (run_id,))
                    dispatch = cur.fetchone()["payload"]
                assert dispatch is not None
                await queue.enqueue(dispatch)
                terminal_worker = processes.start("minimal-terminal-replay", "-m", "src.cli.run_redis_worker",
                    "--once", "--worker-id", "minimal-terminal")
                assert await processes.wait(terminal_worker, timeout=90) == 0
                replay = await get("/runs/" + run_id)
                assert replay == detail
                assert len(target.requests) == 1
    finally:
        if last_detail is not None:
            evidence.joinpath("last-run.json").write_text(
                json.dumps(last_detail, ensure_ascii=False, indent=2), encoding="utf-8")
        if planning is not None:
            evidence.joinpath("planning-task.json").write_text(
                json.dumps(planning, ensure_ascii=False, indent=2), encoding="utf-8")
        if plan is not None:
            evidence.joinpath("plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence.joinpath("target-requests-finally.json").write_text(
            json.dumps(target.requests, ensure_ascii=False, indent=2), encoding="utf-8")
        target.close()

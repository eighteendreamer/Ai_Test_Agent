"""Business-chain outage recovery: a real model-call connection failure and a
real execution Worker kill must never repeat an already executed side effect.

Both boundaries drive the production API, the production execution Worker and
a controlled HTTP business target. The execution graph replay is scripted by the
existing default-model probe so the failure window is deterministic; the
read-only correction path (unused here) stays on the real default provider.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from datetime import datetime, timezone
from time import monotonic
import subprocess
from uuid import uuid4

import httpx
import psutil
import pytest
from pydantic import SecretStr

from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api
from test_receipt_process_recovery_live import read_state, resource_state
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_EXECUTION_OUTAGE_RECOVERY") != "1",
    reason="requires isolated MySQL/PostgreSQL/RustFS/Redis, owned processes and an unreachable local port",
)


class OrderTarget:
    """Controlled business endpoint; counts every side-effecting request."""

    def __init__(self):
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                owner.requests.append({"method": "POST", "path": self.path,
                    "body": raw.decode("utf-8", "replace")})
                body = json.dumps({"id": "order-" + str(len(owner.requests)),
                    "status": "created"}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                owner.requests.append({"method": "GET", "path": self.path})
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

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


def _kill_owned_process_tree(pid: int) -> dict:
    completed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, text=True)
    return {"pid": pid, "exit_code": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr}


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["model-outage", "worker-kill"])
async def test_model_transport_outage_and_worker_kill_do_not_repeat_business(tmp_path, boundary):
    target = OrderTarget()
    target_port = target.start()
    orders_url = f"http://127.0.0.1:{target_port}/orders"
    evidence_root = Path(os.getenv("QA_DEFAULT_EXECUTION_OUTAGE_RECOVERY_DIR",
        str(tmp_path / "evidence")))
    evidence = evidence_root / boundary
    evidence.mkdir(parents=True, exist_ok=False)
    physical = evidence / "physical"
    block_marker = evidence / "model-call-blocked.json"
    block_release = evidence / "release-model-call.json"
    global_before = MySQLModelConfigStore(
        get_settings().model_copy(deep=True)).get_default_active()
    assert global_before is not None
    api = None
    last_detail = None
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned_objects):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
            # The killed Worker also leaves its execution resource lease behind;
            # a short lease keeps the natural-expiry window bounded.
            settings.orchestration.resource_default_lease_seconds = 20
            original = MySQLModelConfigStore(get_settings().model_copy(deep=True)).get_default_active()
            assert original is not None and original.transport == "openai_chat_completions"
            models = MySQLModelConfigStore(settings)
            models.initialize()
            # The scripted graph answers plain chat completions, so the isolated
            # copy of the real default configuration disables streaming only.
            overrides = (original.capability_overrides.model_copy(deep=True)
                if original.capability_overrides is not None else ModelCapabilitiesOverride())
            overrides.streaming = False
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=original.model_id, provider=original.provider, transport=original.transport,
                request_profile=original.request_profile, base_url=original.api_base_url,
                api_key=original.api_key, auth_type=original.auth_type, oauth_provider=original.oauth_provider,
                oauth_refresh_token=original.oauth_refresh_token, applications=original.applications,
                is_active=True, use_provider_defaults=False, capability_overrides=overrides,
                context_window=original.context_window, max_output_tokens=original.max_output_tokens,
            ))
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(physical)
            create_order = {"tool_calls": [{"id": "create-order", "name": "api-test-runner",
                "arguments": json.dumps({
                    "worker_action": "execute_task",
                    "task": {"task_id": "create-order", "method": "POST", "path": orders_url,
                        "request_body": {"amount": 100}, "timeout_seconds": 30}})}]}
            if boundary == "model-outage":
                # The only closed port in this test: the next model call dials it
                # for real, so httpx raises its genuine connection error.
                script = [create_order, {"network_outage":
                    f"http://127.0.0.1:{free_port()}/v1/chat/completions"}]
            else:
                script = [{"block_model_call": True}]
                processes.env["QA_DEFAULT_PROBE_GRAPH_BLOCK_MARKER"] = str(block_marker)
                processes.env["QA_DEFAULT_PROBE_GRAPH_BLOCK_RELEASE"] = str(block_release)
            processes.env["QA_DEFAULT_PROBE_SCRIPTED_GRAPH"] = json.dumps(script, ensure_ascii=False)
            api_port = free_port()
            api_args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                        "--host", "127.0.0.1", "--port", str(api_port))
            api = processes.start("outage-recovery-api", *api_args)
            base = f"http://127.0.0.1:{api_port}/api/v1"
            async with httpx.AsyncClient(base_url=base, timeout=300,
                headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                await wait_api(client, api)

                async def post(path, payload=None, status=200):
                    response = await client.post(path, json=payload)
                    assert response.status_code == status, (response.status_code, response.text)
                    return response.json()

                async def get(path):
                    response = await client.get(path)
                    assert response.status_code == 200, (response.status_code, response.text)
                    return response.json()

                project = await post("/projects", {
                    "project_key": "outage-recovery-" + uuid4().hex,
                    "name": "执行断网与Worker中断恢复验收",
                    "base_url": f"http://127.0.0.1:{target_port}",
                }, 201)
                root = "/projects/" + project["id"]
                source = tmp_path / "orders.md"
                source.write_text(
                    f"# 订单创建\nPOST {orders_url} 携带 JSON 请求体创建订单，成功返回 HTTP 201。\n",
                    encoding="utf-8")
                document = await post("/registry/api-docs/upload", {
                    "project_id": project["id"], "filename": source.name,
                    "content_base64": base64.b64encode(source.read_bytes()).decode(),
                })
                owned_objects.append(document["storage_uri"])
                case = await post(root + "/test-cases", {
                    "case_key": "create-order", "title": "创建订单", "mode_key": "api_testing",
                    "case_type": "json",
                    "content_json": {
                        "objective": "创建一条订单并核对 HTTP 201",
                        "task": {"task_id": "create-order", "name": "创建订单", "method": "POST",
                            "path": "/orders", "full_url": orders_url, "execution_mode": "write",
                            "request_body": {"amount": 100},
                            "assertions": [{"kind": "status_code", "expected": 201}],
                            "timeout_seconds": 30},
                        "steps": [{"order": 1, "action": "按 task 执行 POST /orders"},
                            {"order": 2, "action": "核对 task.assertions 中的 HTTP 201"}],
                    },
                    "execution_notes": "写操作；只执行一次，不要重复创建订单。",
                    "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                    "model_key": "manual", "prompt_version": "manual-v1",
                    "skill_versions": {"manual": "v1"},
                }, 201)
                case_id = case["case"]["id"]
                await post(f"/test-cases/{case_id}/submit-review", {})
                await post(f"/test-cases/{case_id}/activate", {})
                planning_task = await post(root + "/case-planning-tasks", {
                    "creation_key": "outage-recovery-plan",
                    "objective": "创建一条订单并核对 HTTP 201",
                    "model_key": model.key, "candidate_case_ids": [case_id]}, 202)
                planning_queue = RedisTaskQueue(settings.database.redis_url,
                    stream=settings.orchestration.case_planning_task_stream,
                    group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                await planning_queue.connect()
                try:
                    assert await PostgresTaskOutbox(settings).relay_once(planning_queue, limit=1) == 1
                    planning_worker = processes.start("outage-recovery-planning", "-m",
                        "src.cli.run_case_planning_worker", "--once", "--worker-id", "outage-recovery-plan")
                    assert await processes.wait(planning_worker, timeout=240) == 0
                finally:
                    await planning_queue.close()
                planning = await get(root + "/case-planning-tasks/" + planning_task["id"])
                assert planning["status"] == "completed", planning
                plan = await get(root + "/case-plans/" + planning["plan_id"])
                assert plan["status"] == "planned" and plan["decision"]["items"], plan
                detail = await post(root + "/case-plans/" + plan["id"] + "/runs",
                    {"creation_key": "outage-recovery-run", "mode_key": "api_testing"}, 201)
                run_id, item_id = detail["run"]["id"], detail["items"][0]["id"]
                assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                from src.infrastructure.postgres_runtime import postgres_connect
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} "
                        "WHERE payload->>'run_id'=%s", (run_id,))
                    dispatch = cur.fetchone()["payload"]

                # The Worker itself must run through the probe entrypoint: the
                # scripted turn (tool call or test-only model-call barrier) is
                # what makes this failure window deterministic.
                execution_worker = processes.start("outage-recovery-execution", "-m",
                    "tests.default_model_probe", "src.cli.run_redis_worker", "--once",
                    "--worker-id", "outage-recovery-first")
                if boundary == "worker-kill":
                    deadline = monotonic() + 240
                    while not block_marker.exists():
                        assert monotonic() < deadline, "execution Worker never reached the model call"
                        await asyncio.sleep(.1)
                    blocked = json.loads(block_marker.read_text(encoding="utf-8"))
                    # The venv interpreter may be a trampoline: the probe runs in
                    # a child of the owned process. taskkill /T owns the whole
                    # tree, and the tree's death is verified below.
                    assert blocked["process_id"], blocked
                    before_kill = await asyncio.to_thread(read_state, settings, item_id)
                    assert before_kill["item"]["status"] == "running"
                    assert before_kill["attempts"][0]["status"] == "running"
                    assert len(target.requests) == 0, target.requests
                    physical_before = sorted(path.name for path in physical.glob("*.request.json"))
                    kill_record = await asyncio.to_thread(_kill_owned_process_tree, execution_worker.pid)
                    killed_exit = await processes.wait(execution_worker, timeout=15)
                    tree_deadline = monotonic() + 15
                    while psutil.pid_exists(blocked["process_id"]) and monotonic() < tree_deadline:
                        await asyncio.sleep(.2)
                    probe_kill = None
                    if psutil.pid_exists(blocked["process_id"]):
                        # The venv entry point is a launcher; if the tree kill did
                        # not reach the real interpreter, kill that owned pid too.
                        probe_kill = await asyncio.to_thread(_kill_owned_process_tree,
                            blocked["process_id"])
                        tree_deadline = monotonic() + 15
                        while psutil.pid_exists(blocked["process_id"]) and monotonic() < tree_deadline:
                            await asyncio.sleep(.2)
                    assert not psutil.pid_exists(blocked["process_id"]), blocked
                    state = await asyncio.to_thread(read_state, settings, item_id)
                    expiry = datetime.fromisoformat(state["item"]["lease_expires_at"])
                    while datetime.now(timezone.utc) <= expiry:
                        assert monotonic() < deadline, "run-item lease did not naturally expire"
                        await asyncio.sleep(.5)
                    recovered = None
                    recover_deadline = monotonic() + 120
                    while monotonic() < recover_deadline:
                        recovered = await post(f"/runs/{run_id}/recover-expired")
                        if recovered["recovered_count"] == 1:
                            break
                        await asyncio.sleep(1)
                    assert recovered is not None and recovered["recovered_count"] == 1, recovered
                    expired = await asyncio.to_thread(read_state, settings, item_id)
                    assert expired["item"]["status"] == "queued", expired["item"]
                    assert expired["attempts"][0]["status"] == "expired", expired["attempts"]
                    # The killed Worker's execution resource lease must expire and
                    # be cleaned before a new Worker can claim the item.
                    resources = await resource_state(settings, project["id"])
                    resource_deadline = monotonic() + settings.orchestration.resource_default_lease_seconds + 30
                    while resources["active"] and monotonic() < resource_deadline:
                        await asyncio.sleep(1)
                        resources = await resource_state(settings, project["id"])
                    cleanup_worker = processes.start("outage-recovery-resource-cleanup", "-m",
                        "src.cli.run_resource_cleanup_worker", "--once",
                        "--worker-id", "outage-recovery-cleanup")
                    assert await processes.wait(cleanup_worker, timeout=90) == 0
                    resources = await resource_state(settings, project["id"])
                    assert not resources["active"] and not resources["cleanup_pending"], resources
                    block_release.write_text("release", encoding="utf-8")
                    await queue.enqueue(dispatch)
                    takeover_worker = processes.start("outage-recovery-takeover", "-m",
                        "src.cli.run_redis_worker", "--once", "--worker-id", "outage-recovery-second")
                    assert await processes.wait(takeover_worker, timeout=300) == 0
                    completed = await get("/runs/" + run_id)
                    last_detail = completed
                    result = completed["results"][0]
                    assert result["status"] == "error", completed
                    assert result["actual"]["execution_recovery_required"] is True, result
                    final = await asyncio.to_thread(read_state, settings, item_id)
                    assert len(final["attempts"]) == 2, final["attempts"]
                    assert final["attempts"][0]["status"] == "expired"
                    assert final["attempts"][1]["attempt_no"] == 2, final["attempts"][1]
                    assert (final["attempts"][1]["lease_token"]
                        != final["attempts"][0]["lease_token"]), final["attempts"]
                    assert len(target.requests) == 0, target.requests
                    physical_after = sorted(path.name for path in physical.glob("*.request.json"))
                    assert physical_after == physical_before, (physical_before, physical_after)
                    operation = {"kill_tree": kill_record, "killed_worker_exit": killed_exit,
                        "probe_process_kill": probe_kill, "blocked_marker": blocked,
                        "expired_attempt_token": final["attempts"][0]["lease_token"],
                        "takeover_attempt_token": final["attempts"][1]["lease_token"]}
                else:
                    assert await processes.wait(execution_worker, timeout=300) == 0
                    detail = await get("/runs/" + run_id)
                    waiting = None
                    wait_deadline = monotonic() + 60
                    while monotonic() < wait_deadline:
                        waiting = next((item for item in detail["items"]
                            if item["status"] == "waiting_approval"), None)
                        if waiting is not None:
                            break
                        await asyncio.sleep(.2)
                        detail = await get("/runs/" + run_id)
                    assert waiting is not None, detail
                    # Approval resumes the frozen graph inline in the API: the
                    # POST executes once, then the next model call hits the closed
                    # port and fails at the real transport boundary.
                    approval = await client.post("/run-items/" + item_id + "/approval",
                        json={"approval_id": waiting["approval_id"], "decision": "approved"})
                    assert approval.status_code in {200, 500}, (approval.status_code, approval.text)
                    assert len(target.requests) == 1, target.requests
                    assert target.requests[0]["method"] == "POST" and target.requests[0]["path"] == "/orders"
                    deadline = monotonic() + 60
                    while monotonic() < deadline:
                        detail = await get("/runs/" + run_id)
                        if detail["results"]:
                            break
                        await asyncio.sleep(.3)
                    last_detail = detail
                    result = detail["results"][0]
                    assert result["status"] == "error", detail
                    assert result["actual"]["execution_checkpoint"], result
                    attempt = (await asyncio.to_thread(read_state, settings, item_id))["attempts"][-1]
                    assert attempt["checkpoint_payload"]["phase"] in {"running", "snapshot"}, attempt
                    outage_attempts = [json.loads(path.read_text(encoding="utf-8"))
                        for path in physical.glob("*.attempt.json")]
                    assert any(item.get("error_type") for item in outage_attempts), outage_attempts
                    physical_before = sorted(path.name for path in physical.glob("*.request.json"))
                    operation = {"approval_status": approval.status_code,
                        "attempt_records": sorted(
                            {item.get("error_type") for item in outage_attempts if item.get("error_type")})}

                # Terminal redelivery: the failed attempt must not be replayed into
                # another business or model request.
                before_replay = await get("/runs/" + run_id)
                await queue.enqueue(dispatch)
                terminal_worker = processes.start("outage-recovery-terminal", "-m",
                    "src.cli.run_redis_worker", "--once", "--worker-id", "outage-recovery-terminal")
                assert await processes.wait(terminal_worker, timeout=180) == 0
                after_replay = await get("/runs/" + run_id)
                assert after_replay == before_replay
                if boundary == "model-outage":
                    assert len(target.requests) == 1, target.requests
                    physical_after = sorted(path.name for path in physical.glob("*.request.json"))
                    assert physical_after == physical_before, (physical_before, physical_after)

                global_after = MySQLModelConfigStore(
                    get_settings().model_copy(deep=True)).get_default_active()
                assert global_after is not None and global_after.model_id == global_before.model_id
                evidence.joinpath("summary.json").write_text(json.dumps({
                    "scope": "production API + production execution Worker + controlled HTTP target; "
                             "real connection refusal / real Worker taskkill",
                    "boundary": boundary, "model_key": model.key,
                    "global_default_unchanged": True,
                    "run_id": run_id, "run_item_id": item_id,
                    "result_status": after_replay["results"][0]["status"],
                    "business_requests": target.requests,
                    "business_request_count": len(target.requests),
                    "terminal_replay_extra_business_requests": 0,
                    "operation": operation,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                evidence.joinpath("final-run.json").write_text(
                    json.dumps(after_replay, ensure_ascii=False, indent=2), encoding="utf-8")
                if api is not None and api.poll() is None:
                    await asyncio.to_thread(_kill_owned_process_tree, api.pid)
                    await processes.wait(api, timeout=15)
    finally:
        if api is not None and api.poll() is None:
            await asyncio.to_thread(_kill_owned_process_tree, api.pid)
        for path in tmp_path.iterdir():
            if path.is_file() and path.suffix in {".stdout", ".stderr"}:
                try:
                    (evidence / path.name).write_bytes(path.read_bytes())
                except OSError:
                    pass
        evidence.joinpath("target-requests-finally.json").write_text(json.dumps(
            target.requests, ensure_ascii=False, indent=2), encoding="utf-8")
        target.close()

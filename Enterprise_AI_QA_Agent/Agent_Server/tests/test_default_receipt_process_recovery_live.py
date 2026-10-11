"""Kill the production API during a real default-model receipt correction."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from test_default_minimal_case_live import HealthTarget
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api
from test_receipt_process_recovery_live import read_state, resource_state
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.settings import ModelConfigUpdateRequest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_RECEIPT_RECOVERY") != "1",
    reason="requires authenticated default Step, isolated storage, Docker and an owned API process",
)


def _kill_owned_process_tree(pid: int) -> dict:
    completed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, text=True)
    return {"pid": pid, "exit_code": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr}


@pytest.mark.asyncio
async def test_default_model_receipt_correction_api_kill_recovers_without_repeat(tmp_path):
    target = HealthTarget()
    target_port = target.start()
    evidence = Path(os.getenv("QA_DEFAULT_RECEIPT_RECOVERY_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=False)
    marker = evidence / "correction-requested.json"
    release = evidence / "release-correction.json"
    last_detail = None
    api = None
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned_objects):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
            global_settings = get_settings().model_copy(deep=True)
            original = MySQLModelConfigStore(global_settings).get_default_active()
            assert original is not None and original.provider == "openai"
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=original.model_id, provider=original.provider, transport=original.transport,
                request_profile=original.request_profile, base_url=original.api_base_url,
                api_key=original.api_key, auth_type=original.auth_type, oauth_provider=original.oauth_provider,
                oauth_refresh_token=original.oauth_refresh_token, applications=original.applications,
                is_active=True, use_provider_defaults=False, capability_overrides=original.capability_overrides,
                context_window=original.context_window, max_output_tokens=original.max_output_tokens,
            ))
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            processes.env["QA_DEFAULT_PROBE_BLOCK_CORRECTION_MARKER"] = str(marker)
            processes.env["QA_DEFAULT_PROBE_BLOCK_CORRECTION_RELEASE"] = str(release)
            api_port = free_port()
            api_args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                        "--host", "127.0.0.1", "--port", str(api_port))
            api = processes.start("default-recovery-api-before-kill", *api_args)
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
                    "project_key": "default-recovery-" + uuid4().hex,
                    "name": "默认Step回执纠正进程恢复验收",
                    "base_url": f"http://127.0.0.1:{target_port}",
                }, 201)
                root = "/projects/" + project["id"]
                target_url = f"http://127.0.0.1:{target_port}/health"
                source = tmp_path / "health.md"
                source.write_text(f"# 健康检查\nGET {target_url} 返回 HTTP 200，JSON 的 ok 必须为 true。\n", encoding="utf-8")
                document = await post("/registry/api-docs/upload", {
                    "project_id": project["id"], "filename": source.name,
                    "content_base64": base64.b64encode(source.read_bytes()).decode(),
                })
                owned_objects.append(document["storage_uri"])
                assert (await objects.read_object_uri(document["storage_uri"]))["content"] == source.read_bytes()
                case = await post(root + "/test-cases", {
                    "case_key": "health-check", "title": "健康检查", "mode_key": "api_testing", "case_type": "json",
                    "content_json": {
                        "objective": "验证健康检查接口",
                        "task": {"task_id": "health-check", "name": "健康检查", "method": "GET",
                            "path": "/health", "full_url": target_url, "execution_mode": "read",
                            "assertions": [{"kind": "status_code", "expected": 200},
                                {"kind": "json_field_equals", "path": "ok", "expected": True}],
                            "timeout_seconds": 30},
                        "steps": [{"order": 1, "action": "按 task 执行 GET /health"},
                            {"order": 2, "action": "核对 task.assertions 中的 HTTP 200 与 body.ok=true"}],
                    },
                    "execution_notes": "只读请求；完整读取用例 JSON 和文档，不需要准备或清理数据。",
                    "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                    "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"},
                }, 201)
                case_id = case["case"]["id"]
                await post(f"/test-cases/{case_id}/submit-review", {})
                await post(f"/test-cases/{case_id}/activate", {})
                policy = await client.put(root + "/case-plans/policy", json={
                    "receipt_correction_protocol": "case-receipt-correction-v1"})
                assert policy.status_code == 200, (policy.status_code, policy.text)
                planning_task = await post(root + "/case-planning-tasks", {
                    "creation_key": "default-recovery-plan", "objective": "验证健康检查接口并核对 ok=true",
                    "model_key": model.key, "candidate_case_ids": [case_id]}, 202)
                planning_queue = RedisTaskQueue(settings.database.redis_url,
                    stream=settings.orchestration.case_planning_task_stream,
                    group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                await planning_queue.connect()
                try:
                    assert await PostgresTaskOutbox(settings).relay_once(planning_queue, limit=1) == 1
                    planning_worker = processes.start("default-recovery-planning", "-m",
                        "src.cli.run_case_planning_worker", "--once", "--worker-id", "default-recovery-plan")
                    assert await processes.wait(planning_worker, timeout=240) == 0
                finally:
                    await planning_queue.close()
                planning = await get(root + "/case-planning-tasks/" + planning_task["id"])
                assert planning["status"] == "completed", planning
                plan = await get(root + "/case-plans/" + planning["plan_id"])
                assert plan["status"] == "planned" and plan["decision"]["items"], plan
                detail = await post(root + "/case-plans/" + plan["id"] + "/runs",
                    {"creation_key": "default-recovery-run", "mode_key": "api_testing"}, 201)
                run_id, item_id = detail["run"]["id"], detail["items"][0]["id"]
                assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} "
                        "WHERE payload->>'run_id'=%s", (run_id,))
                    dispatch = cur.fetchone()["payload"]
                execution_worker = processes.start("default-recovery-execution", "-m", "src.cli.run_redis_worker",
                    "--once", "--worker-id", "default-recovery-execute")
                assert await processes.wait(execution_worker, timeout=420) == 0
                detail = await get("/runs/" + run_id)
                approval_task = None
                marker_data = None
                for _ in range(12):
                    waiting = next((item for item in detail["items"] if item["status"] == "waiting_approval"), None)
                    assert waiting is not None, detail
                    approval_task = asyncio.create_task(client.post("/run-items/" + item_id + "/approval",
                        json={"approval_id": waiting["approval_id"], "decision": "approved"}))
                    deadline = monotonic() + 120
                    while monotonic() < deadline and not marker.exists() and not approval_task.done():
                        await asyncio.sleep(.1)
                    if marker.exists():
                        marker_data = json.loads(marker.read_text(encoding="utf-8"))
                        break
                    assert approval_task.done(), "approval did not finish or enter correction"
                    response = await approval_task
                    assert response.status_code == 200, (response.status_code, response.text)
                    detail = await get("/runs/" + run_id)
                    if marker.exists():
                        marker_data = json.loads(marker.read_text(encoding="utf-8"))
                        break
                assert marker_data is not None, detail
                before_kill = await asyncio.to_thread(read_state, settings, item_id)
                correction = before_kill["attempts"][-1]["checkpoint_payload"].get("receipt_correction")
                assert correction and correction["phase"] == "invoking", before_kill
                assert len(target.requests) == 1, target.requests
                reference = correction["request"]
                owned_objects.append(reference["uri"])
                raw = (await objects.read_object_uri(reference["uri"]))["content"]
                assert len(raw) == reference["size_bytes"] and hashlib.sha256(raw).hexdigest() == reference["sha256"]
                evidence.joinpath("receipt-request-original.json").write_bytes(raw)
                evidence.joinpath("before-kill.json").write_text(json.dumps(before_kill["attempts"],
                    ensure_ascii=False, default=str, indent=2), encoding="utf-8")
                physical_before = sorted(path.name for path in (evidence / "physical").glob("*.request.json"))
                killed_pid = api.pid
                kill_record = await asyncio.to_thread(_kill_owned_process_tree, api.pid)
                killed_exit = await processes.wait(api, timeout=15)
                release.write_text("release", encoding="utf-8")
                try:
                    await approval_task
                    raise AssertionError("killed API unexpectedly returned approval response")
                except (httpx.TransportError, asyncio.CancelledError):
                    pass

                # Restart without the test-only request barrier. The persisted
                # checkpoint, not a fresh model call, drives recovery.
                processes.env.pop("QA_DEFAULT_PROBE_BLOCK_CORRECTION_MARKER", None)
                processes.env.pop("QA_DEFAULT_PROBE_BLOCK_CORRECTION_RELEASE", None)
                api = processes.start("default-recovery-api-after-kill", *api_args)
                await wait_api(client, api)
                immediate = await post(f"/runs/{run_id}/recover-expired")
                assert immediate["recovered_count"] == 0, immediate
                state = await asyncio.to_thread(read_state, settings, item_id)
                expiry = datetime.fromisoformat(state["item"]["lease_expires_at"])
                deadline = monotonic() + 120
                while datetime.now(timezone.utc) <= expiry:
                    assert monotonic() < deadline, "run-item lease did not naturally expire"
                    await asyncio.sleep(.5)
                recovered = await post(f"/runs/{run_id}/recover-expired")
                assert recovered["recovered_count"] in (0, 1), recovered
                expired = await asyncio.to_thread(read_state, settings, item_id)
                assert expired["item"]["status"] == "queued" and expired["attempts"][0]["status"] == "expired", expired
                resources = await resource_state(settings, project["id"])
                resource_deadline = monotonic() + settings.orchestration.resource_default_lease_seconds + 20
                while resources["active"] and monotonic() < resource_deadline:
                    await asyncio.sleep(.5)
                    resources = await resource_state(settings, project["id"])
                cleanup_worker = processes.start("default-recovery-resource-cleanup", "-m",
                    "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "default-recovery-cleanup")
                assert await processes.wait(cleanup_worker, timeout=90) == 0
                resources = await resource_state(settings, project["id"])
                assert not resources["active"] and not resources["cleanup_pending"], resources
                assert all(value == 0 for value in resources["usage"].values()), resources
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                    cleanup_receipts = [dict(row) for row in cur.fetchall()]
                evidence.joinpath("resource-cleanup.json").write_text(json.dumps({
                    "resources": resources, "receipts": cleanup_receipts}, ensure_ascii=False,
                    default=str, indent=2), encoding="utf-8")
                await queue.enqueue(dispatch)
                recovery_worker = processes.start("default-recovery-execution-recovery", "-m",
                    "src.cli.run_redis_worker", "--once", "--worker-id", "default-recovery-replay")
                assert await processes.wait(recovery_worker, timeout=420) == 0
                completed = await get("/runs/" + run_id)
                last_detail = completed
                result = completed["results"][0]
                assert result["status"] == "error", completed
                assert result["actual"]["execution_recovery_required"] is True, result
                assert result["actual"]["execution_checkpoint"]["receipt_correction"] == correction, result
                assert len(target.requests) == 1, target.requests
                final = await asyncio.to_thread(read_state, settings, item_id)
                assert len(final["attempts"]) == 2 and final["attempts"][0]["status"] == "expired", final
                assert final["attempts"][1]["checkpoint_payload"]["receipt_correction"] == correction
                await queue.enqueue(dispatch)
                terminal_worker = processes.start("default-recovery-terminal-replay", "-m",
                    "src.cli.run_redis_worker", "--once", "--worker-id", "default-recovery-terminal")
                assert await processes.wait(terminal_worker, timeout=120) == 0
                assert await get("/runs/" + run_id) == completed
                assert len(target.requests) == 1
                physical_after = sorted(path.name for path in (evidence / "physical").glob("*.request.json"))
                assert physical_after == physical_before, (physical_before, physical_after)
                evidence.joinpath("summary.json").write_text(json.dumps({
                    "scope": "real authenticated default Step; owned API kill after durable correction intent, before HTTP network send; natural run/resource expiry; production recovery workers",
                    "model_key": model.key, "run_id": run_id, "item_id": item_id,
                    "marker": marker_data, "correction": correction,
                    "killed_api_pid": killed_pid, "killed_api_exit_code": killed_exit,
                    "kill_tree": kill_record,
                    "result_status": result["status"], "target_requests": target.requests,
                    "terminal_replay_extra_requests": 0,
                    "recovery_and_terminal_extra_model_requests": 0,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                await asyncio.to_thread(_kill_owned_process_tree, api.pid)
                await processes.wait(api, timeout=15)
    finally:
        if api is not None and api.poll() is None:
            await asyncio.to_thread(_kill_owned_process_tree, api.pid)
        for path in tmp_path.iterdir():
            if path.is_file() and (path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "health.md"}):
                shutil.copyfile(path, evidence / path.name)
        evidence.joinpath("target-requests-finally.json").write_text(json.dumps(target.requests,
            ensure_ascii=False, indent=2), encoding="utf-8")
        if last_detail is not None:
            evidence.joinpath("last-run.json").write_text(json.dumps(last_detail, ensure_ascii=False, indent=2), encoding="utf-8")
        target.close()

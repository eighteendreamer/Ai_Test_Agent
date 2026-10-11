"""Kill a real production API at each durable receipt-correction boundary."""
from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from threading import Event
from time import monotonic
from uuid import uuid4

import httpx
import pytest

from test_execution_worker_cli import (
    child_environment, controlled_case_content, controlled_http, free_port, isolated_runtime, wait_api,
)
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.model_config import ModelCapabilitiesOverride, ModelTransportOptions
from src.schemas.settings import ModelConfigUpdateRequest

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_EXECUTION_CLI_TESTS") != "1",
    reason="requires isolated real storage, local Docker and owned production processes")


def write(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


def read_state(settings, item_id):
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {settings.database.postgres_test_run_item_table} WHERE id=%s", (item_id,))
        item = cur.fetchone()["record"]
        cur.execute(f"SELECT record FROM {settings.database.postgres_test_run_attempt_table} "
            "WHERE run_item_id=%s ORDER BY attempt_no", (item_id,))
        attempts = [r["record"] for r in cur.fetchall()]
        cur.execute(f"SELECT * FROM {settings.database.postgres_snapshot_table}")
        snapshots = [dict(r) for r in cur.fetchall()]
        return {"item": item, "attempts": attempts, "snapshots": snapshots}


async def wait_boundary(settings, item_id, boundary):
    deadline = monotonic() + 45
    last = None
    while monotonic() < deadline:
        last = await asyncio.to_thread(read_state, settings, item_id)
        cp = last["attempts"][-1]["checkpoint_payload"].get("receipt_correction")
        if cp and cp["phase"] == boundary:
            return last
        await asyncio.sleep(.1)
    raise AssertionError(("receipt boundary not reached", boundary, last))


def blocked_result_insert(settings, blocker_pid):
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT a.pid,a.state,a.wait_event_type,a.wait_event,a.query,l.mode,l.granted "
            "FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid "
            "WHERE a.datname=current_database() AND l.relation=%s::regclass "
            "AND NOT l.granted AND %s=ANY(pg_blocking_pids(a.pid))",
            (settings.database.postgres_test_case_result_table, blocker_pid))
        return [dict(r) for r in cur.fetchall()]


async def resource_state(settings, project_id):
    manager = RedisResourceLeaseManager(settings.database.redis_url,
        cleanup_stream=settings.orchestration.cleanup_task_stream)
    try:
        await manager.connect()
        active = await manager.list_active(project_id=project_id)
        for lease in active:
            lease.pop("lease_token", None)
            lease.pop("lease_key", None)
        return {"active": active, "cleanup_pending": await manager.list_cleanup_pending(project_id=project_id),
            "usage": await manager.usage_snapshot()}
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["invoking", "response"])
async def test_owned_api_kill_recovers_receipt_without_resending_or_repeating_business(tmp_path, boundary):
    arrived, release = Event(), Event()
    response_streaming = os.getenv("QA_RECEIPT_PROCESS_STREAMING") == "1"

    async def stop_owned_api(processes, process, name):
        if process.poll() is not None: return
        if os.name == "nt":
            from test_default_receipt_process_recovery_live import _kill_owned_process_tree
            stopped = await asyncio.to_thread(_kill_owned_process_tree, process.pid)
            write(tmp_path, name + ".json", stopped)
            assert stopped["exit_code"] == 0, stopped
            await processes.wait(process, timeout=15)
        else:
            await processes.stop(process)

    def gate(request):
        assert not request.get("tools"), "read-only correction must expose no tools"
        assert bool(request.get("stream")) == response_streaming
        arrived.set()
        if boundary == "invoking":
            assert release.wait(55), "test did not release its owned HTTP connection"
            return False  # No reply; the API has already been killed.
        return True

    root = Path(os.getenv("QA_RECEIPT_PROCESS_DIR", str(tmp_path.parent / "receipt-process-evidence")))
    output = root / boundary
    output.mkdir(parents=True, exist_ok=False)
    try:
        async with controlled_http(tmp_path, mixed_report=True, correction_gate=gate) as (
            business, calls, effects, orders, actual_id, errors,
        ):
            async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned), AsyncExitStack() as api_cleanup:
                settings.orchestration.runtime_max_iterations = 8
                models = MySQLModelConfigStore(settings)
                models.initialize()
                model = models.upsert(ModelConfigUpdateRequest(model_name="controlled-cli", provider="openai",
                    transport="openai_chat_completions", base_url=business + "/v1", api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False,
                    capability_overrides=ModelCapabilitiesOverride(streaming=response_streaming), max_output_tokens=4096))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("api-before-kill", *args)
                api_cleanup.push_async_callback(stop_owned_api, processes, api, "api-before-kill-tree")
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=55) as client:
                    await wait_api(client, api)

                    async def post(path, payload=None, status=200):
                        reply = await client.post(path, json=payload)
                        assert reply.status_code == status, (reply.status_code, reply.text, errors)
                        return reply.json()

                    project = await post("/projects", {"project_key": "receipt-kill-" + uuid4().hex,
                        "name": "回执进程中断验收", "base_url": business}, 201)
                    policy_path = f"/projects/{project['id']}/case-plans/policy"
                    policy = CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1",
                        receipt_correction_transport_options=ModelTransportOptions(timeout_seconds=60, max_retries=0))
                    assert (await client.put(policy_path, json=policy.model_dump(mode="json"))).status_code == 200
                    source = tmp_path / "requirements.md"
                    source.write_text(f"# 订单查询\nPOST {business}/orders 创建，201，order.id。\n"
                        f"GET {business}/orders/{{id}} 查询，200，flag=false，count=0，nullable=null。\n"
                        f"DELETE {business}/orders/{{id}} 清理，200。\n", encoding="utf-8")
                    document = await post("/registry/api-docs/upload", {"project_id": project["id"],
                        "filename": source.name, "content_base64": base64.b64encode(source.read_bytes()).decode()})
                    owned.append(document["storage_uri"])
                    assert (await objects.read_object_uri(document["storage_uri"]))["content"] == source.read_bytes()
                    bundle = await post(f"/projects/{project['id']}/test-cases", {"case_key": "query",
                        "title": "查询已有订单", "mode_key": "api_testing", "case_type": "json",
                        "content_json": controlled_case_content(business),
                        "execution_notes": "完整读取 JSON；准备的订单在查询后清理",
                        "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                        "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"},
                        "generation_metadata": {"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}}, 201)
                    case_id = bundle["case"]["id"]
                    await post(f"/test-cases/{case_id}/submit-review")
                    await post(f"/test-cases/{case_id}/activate", {})
                    plan = await post(f"/projects/{project['id']}/case-plans", {"objective": "验证订单详情",
                        "model_key": model.key, "candidate_case_ids": [case_id]}, 201)
                    assert plan["status"] == "planned", plan
                    detail = await post(f"/projects/{project['id']}/case-plans/{plan['id']}/runs",
                        {"creation_key": "receipt-kill", "mode_key": "api_testing"}, 201)
                    run_id, item_id = detail["run"]["id"], detail["items"][0]["id"]
                    assert await PostgresTaskOutbox(settings).relay_once(queue) == 1
                    child = processes.start("worker-before-kill", "-m", "src.cli.run_redis_worker", "--once",
                        "--worker-id", "receipt-initial")
                    assert await processes.wait(child) == 0
                    assert len(calls) == 2 and not effects
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table}")
                        delivery = cur.fetchone()["payload"]

                    for index in range(2):
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT id FROM {settings.database.postgres_approval_table} WHERE status='pending'")
                            pending = cur.fetchall()
                        assert len(pending) == 1
                        outcome = await post(f"/run-items/{item_id}/approval",
                            {"approval_id": str(pending[0]["id"]), "decision": "approved"})
                        assert outcome["status"] == "waiting_approval" and len(effects) == index + 1
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT id FROM {settings.database.postgres_approval_table} WHERE status='pending'")
                        pending = cur.fetchall()
                    assert len(pending) == 1

                    blockers = ExitStack()
                    blocker = blockers.enter_context(postgres_connect(settings)) if boundary == "response" else None
                    approval_task = None
                    try:
                        if blocker:
                            table = settings.database.postgres_test_case_result_table
                            assert re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", table)
                            with blocker.cursor() as cur:
                                cur.execute("SET lock_timeout='5s'")
                                cur.execute(f"LOCK TABLE {table} IN SHARE MODE")
                                cur.execute("SELECT pg_backend_pid() AS pid")
                                blocker_pid = cur.fetchone()["pid"]
                        approval_task = asyncio.create_task(client.post(f"/run-items/{item_id}/approval",
                            json={"approval_id": str(pending[0]["id"]), "decision": "approved"}))
                        state = await wait_boundary(settings, item_id, boundary)
                        assert arrived.is_set() and len(calls) == 6
                        assert [verb for verb, _ in effects] == ["POST", "GET", "DELETE"] and not orders
                        if blocker:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                locks = await asyncio.to_thread(blocked_result_insert, settings, blocker_pid)
                                if locks:
                                    break
                                await asyncio.sleep(.1)
                            assert locks and all("INSERT INTO " + table in row["query"] for row in locks), locks
                            write(tmp_path, "blocked-insert.json", locks)
                        write(tmp_path, "before-kill.json", state)
                        resources_before = await resource_state(settings, project["id"])
                        write(tmp_path, "resources-before-kill.json", resources_before)
                        assert len(resources_before["active"]) == 1
                        resource_expiry = datetime.fromtimestamp(
                            resources_before["active"][0]["lease_expire_at_ms"] / 1000, timezone.utc)
                        killed_pid = api.pid
                        await stop_owned_api(processes, api, "api-kill-tree")
                        killed_exit = await processes.wait(api, timeout=10)
                        assert killed_exit != 0
                        release.set()
                        try:
                            reply = await approval_task
                            raise AssertionError(("killed API returned a response", reply.status_code, reply.text))
                        except httpx.TransportError as exc:
                            write(tmp_path, "lost-approval-transport.json", {"error_type": type(exc).__name__})
                    finally:
                        release.set()
                        if blocker:
                            blocker.rollback()
                        blockers.close()
                        if approval_task and not approval_task.done():
                            approval_task.cancel()
                            await asyncio.gather(approval_task, return_exceptions=True)

                    correction = state["attempts"][-1]["checkpoint_payload"]["receipt_correction"]
                    assert correction.get("response_transport_protocol") == (
                        "case-receipt-stream-response-v1" if response_streaming else None)
                    for kind in ("request", "response"):
                        ref = correction.get(kind)
                        if ref:
                            owned.append(ref["uri"])
                            raw = (await objects.read_object_uri(ref["uri"]))["content"]
                            assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                            (tmp_path / f"receipt-{kind}.json").write_bytes(raw)
                    # Changing current configuration must not rewrite the saved execution.
                    settings.orchestration.runtime_max_iterations = 18
                    updated_model = models.upsert(ModelConfigUpdateRequest(model_name="controlled-cli", provider="openai",
                        transport="openai_chat_completions", base_url=business + "/changed/v1", api_key=uuid4().hex,
                        is_active=True, use_provider_defaults=False,
                        capability_overrides=ModelCapabilitiesOverride(streaming=False), max_output_tokens=128))
                    assert updated_model.key == model.key and updated_model.max_output_tokens == 128
                    processes.env = child_environment(settings)
                    api = processes.start("api-after-kill", *args)
                    api_cleanup.push_async_callback(stop_owned_api, processes, api, "api-after-kill-tree")
                    await wait_api(client, api)
                    assert (await client.put(policy_path, json=CasePlanningPolicy().model_dump(mode="json"))).status_code == 200
                    immediate = await post(f"/runs/{run_id}/recover-expired")
                    assert immediate["recovered_count"] == 0
                    expiry = datetime.fromisoformat(state["item"]["lease_expires_at"])
                    deadline = monotonic() + 110
                    while datetime.now(timezone.utc) <= expiry:
                        assert monotonic() < deadline, "original lease did not naturally expire"
                        await asyncio.sleep(.5)
                    recovered = await post(f"/runs/{run_id}/recover-expired")
                    # The production API lease reaper may win this same recovery.
                    assert recovered["recovered_count"] in (0, 1)
                    expired_state = await asyncio.to_thread(read_state, settings, item_id)
                    assert expired_state["item"]["status"] == "queued"
                    assert expired_state["attempts"][0]["status"] == "expired"
                    write(tmp_path, "expired-item.json", expired_state)
                    # Logical resource ownership has an independent default lease.
                    # Never release it manually or reset its Redis TTL for a test.
                    deadline = monotonic() + settings.orchestration.resource_default_lease_seconds + 15
                    while datetime.now(timezone.utc) <= resource_expiry:
                        assert monotonic() < deadline, "original resource lease did not naturally expire"
                        await asyncio.sleep(.5)
                    cleanup = processes.start("resource-cleanup", "-m", "src.cli.run_resource_cleanup_worker",
                        "--once", "--worker-id", "receipt-resource-cleanup")
                    assert await processes.wait(cleanup) == 0
                    resources_after = await resource_state(settings, project["id"])
                    write(tmp_path, "resources-after-cleanup.json", resources_after)
                    assert not resources_after["active"] and not resources_after["cleanup_pending"]
                    assert all(value == 0 for value in resources_after["usage"].values())
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                        cleanup_receipts = [dict(row) for row in cur.fetchall()]
                    write(tmp_path, "resource-cleanup-receipts.json", cleanup_receipts)
                    assert len(cleanup_receipts) == 1 and cleanup_receipts[0]["status"] == "skipped"
                    assert cleanup_receipts[0]["record"]["run_item_id"] == item_id
                    await queue.enqueue(delivery)
                    worker = processes.start("worker-recovery", "-m", "src.cli.run_redis_worker", "--once",
                        "--worker-id", "receipt-recovery")
                    assert await processes.wait(worker) == 0
                    completed = (await client.get(f"/runs/{run_id}")).json()
                    write(tmp_path, "completed.json", completed)
                    assert completed["run"]["status"] == "completed" and len(completed["results"]) == 1
                    result = completed["results"][0]
                    assert result["status"] == ("passed" if boundary == "response" else "error"), result
                    if boundary == "response":
                        assert result["actual"]["case_output"]["body"]["order.id"] == actual_id
                        assert result["actual"]["receipt_correction"] == correction
                    else:
                        assert result["actual"]["execution_recovery_required"] is True
                        assert result["actual"]["execution_checkpoint"]["receipt_correction"] == correction
                    final = await asyncio.to_thread(read_state, settings, item_id)
                    write(tmp_path, "after-recovery.json", final)
                    assert len(final["attempts"]) == 2
                    assert final["attempts"][0]["status"] == "expired"
                    assert final["attempts"][1]["recovered_from_attempt_id"] == final["attempts"][0]["id"]
                    assert final["attempts"][1]["checkpoint_payload"]["receipt_correction"] == correction
                    graph = [r["graph_state"] for r in final["snapshots"] if r["stage"] == "completed"]
                    assert len(graph) == 1 and graph[0]["model_call_count"] == 4 and graph[0]["max_iterations"] == 8
                    assert correction["total_model_calls"] == 5 and correction["max_iterations"] == 8
                    assert correction["runtime_parameters"]["transport_options"] == {"timeout_seconds": 60, "max_retries": 0}
                    assert len(calls) == 6 and len(effects) == 3 and not orders and not errors
                    await queue.enqueue(delivery)
                    terminal = processes.start("worker-terminal-replay", "-m", "src.cli.run_redis_worker", "--once",
                        "--worker-id", "receipt-terminal")
                    assert await processes.wait(terminal) == 0 and not await queue.pending()
                    assert (await client.get(f"/runs/{run_id}")).json() == completed
                    assert len(calls) == 6 and len(effects) == 3
                    write(tmp_path, "summary.json", {"boundary": boundary, "response_streaming": response_streaming,
                        "scope": "real owned API kill, natural lease expiry, production CLI, isolated stores, controlled HTTP",
                        "killed_api_pid": killed_pid, "killed_api_exit_code": killed_exit,
                        "original_lease_expires_at": expiry, "immediate_recovery": immediate, "expired_recovery": recovered,
                        "original_resource_lease_expires_at": resource_expiry,
                        "resource_lease_seconds": settings.orchestration.resource_default_lease_seconds,
                        "resource_cleanup_receipts": cleanup_receipts,
                        "formal_status": result["status"], "run_id": run_id, "result_id": result["id"],
                        "correction": correction, "model_calls": calls, "business_effects": effects,
                        "terminal_replay_model_calls": 0, "terminal_replay_business_effects": 0,
                        "remaining_business_records": len(orders), "current_default_limit": 18,
                        "original_turn_limit": 8, "current_correction_policy_disabled": True,
                        "current_model_configuration_changed": True})
    finally:
        release.set()
        # Also retain failures after fixture cleanup, without copying secret settings.
        for path in tmp_path.iterdir():
            if path.is_file() and path.suffix in {".json", ".stdout", ".stderr", ".md"}:
                shutil.copyfile(path, output / path.name)

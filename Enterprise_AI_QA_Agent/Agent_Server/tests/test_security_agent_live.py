"""Real configured-model Security Agent path against an owned local target.

The model is selected from the user's MySQL configuration and copied into the
isolated MySQL created by ``isolated_runtime``. The production default model is
never changed. Docker, PostgreSQL, RustFS, Redis, the API and Agent Workers use
their normal runtime paths.
"""
from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from time import monotonic
from uuid import uuid4

import httpx
import pytest

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from src.modes.security_testing_mode.contracts import STATE_METADATA_KEY
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_SECURITY_AGENT") != "1",
        reason="requires an explicitly selected configured model, local Docker image, isolated stores and Agent Workers",
    ),
]


def _config_identity(config) -> str:
    payload = {
        "parameters": model_invocation_parameters(config),
        "api_key_sha256": hashlib.sha256((config.api_key or "").encode()).hexdigest(),
        "oauth_refresh_token_sha256": hashlib.sha256(
            (config.oauth_refresh_token or "").encode()
        ).hexdigest(),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def test_configured_security_agent_runs_docker_campaign_and_terminal_replay(tmp_path):
    evidence = Path(os.environ["QA_SECURITY_AGENT_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    physical = evidence / "physical"
    physical.mkdir()
    save = lambda name, value: (evidence / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    source_models = MySQLModelConfigStore(get_settings())
    global_default = source_models.get_default_active()
    global_default_identity = _config_identity(global_default)
    selected_name = os.getenv("QA_SECURITY_AGENT_MODEL_NAME", "agnes-2.5-flash")
    selected = source_models.get_by_name(selected_name)
    assert selected.api_key, "The selected non-default model has no configured credential."
    assert selected.supports_tools, "The selected model is not configured for tool calling."
    assert selected.transport == "openai_chat_completions", (
        "This live scenario is pinned to the already-verified Chat Completions transport."
    )

    facts = {
        "scope": "configured alternate model; explicit security Agent; owned loopback HTTP target; real Docker runner",
        "model_key": selected.key,
        "provider": selected.provider,
        "transport": selected.transport,
        "model_parameters": model_invocation_parameters(selected),
        "controlled_model": False,
        "full_goal_complete": False,
        "global_default_unchanged": False,
        "security_authorization_source": "server-owned isolated session metadata",
        "public_target_claim_used_as_authorization": False,
        "target_requests": [],
        "tool_jobs": [],
        "agent_completed": False,
        "report_verified": False,
        "terminal_replay_added_model_or_target_calls": None,
    }

    target_requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, body: bytes) -> None:
            target_requests.append({"method": self.command, "path": self.path})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # Deliberately omit common security headers for a testable finding.
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            self._reply(b"owned local security-agent fixture")

        def do_HEAD(self):
            self._reply(b"owned local security-agent fixture")

        def do_OPTIONS(self):
            self._reply(b"")

        def do_POST(self):
            target_requests.append({"method": self.command, "path": self.path})
            self.send_error(405)

        def do_PUT(self):
            target_requests.append({"method": self.command, "path": self.path})
            self.send_error(405)

        def do_DELETE(self):
            target_requests.append({"method": self.command, "path": self.path})
            self.send_error(405)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    target_url = f"http://127.0.0.1:{server.server_port}"
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.agent_task_stream
            settings.security.security_runner_backend = "docker"
            settings.security.security_runner_docker_image = (
                "sha256:9520f0e11a5c74e80c758260e5ac2ad46bcae90fc0dda45670cb50b9f421c3d4"
            )
            settings.security.security_runner_docker_net_raw = False
            settings.security.security_runner_docker_cleanup_after_run = True
            settings.security.security_target_allowlist = "127.0.0.1"
            settings.security.security_attack_chain_enabled = False
            settings.security.security_campaign_max_loops = 1
            settings.security.security_campaign_max_attempts = 4
            # The shared CLI fixture lowers this to 1 second for fast recovery
            # tests. A live model/tool turn can legitimately be idle longer,
            # so keep the production reclaim window here.
            settings.orchestration.redis_task_reclaim_idle_ms = 120000

            isolated_models = MySQLModelConfigStore(settings)
            isolated_models.initialize()
            isolated_models.upsert(ModelConfigUpdateRequest(
                model_name=selected.model_id,
                provider=selected.provider,
                transport=selected.transport,
                request_profile=selected.request_profile,
                base_url=selected.api_base_url,
                api_key=selected.api_key,
                auth_type=selected.auth_type,
                oauth_provider=selected.oauth_provider,
                oauth_refresh_token=selected.oauth_refresh_token,
                applications=["task_execution"],
                is_active=True,
                use_provider_defaults=False,
                capability_overrides=selected.capability_overrides,
                context_window=selected.context_window,
                max_output_tokens=min(selected.max_output_tokens, 4096),
            ))
            isolated_model = isolated_models.get_by_name(selected.model_id)
            assert isolated_model.is_active and isolated_model.key == selected.key
            assert isolated_model.api_key == selected.api_key
            facts["isolated_model_parameters"] = model_invocation_parameters(isolated_model)

            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(physical)
            api_port = free_port()
            api_args = (
                "-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                "--host", "127.0.0.1", "--port", str(api_port),
            )
            api = processes.start("security-agent-api", *api_args)
            queue = RedisTaskQueue(
                settings.database.redis_url,
                stream=settings.orchestration.agent_task_stream,
                group=settings.orchestration.redis_task_consumer_group,
                socket_timeout_seconds=1,
            )
            await queue.connect()
            workers = []
            try:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=45
                ) as client:
                    await wait_api(client, api)

                    async def post(path, body, expected):
                        response = await client.post(path, json=body)
                        assert response.status_code == expected, (
                            path, response.status_code, response.text[:500]
                        )
                        return response.json()

                    async def get(path):
                        response = await client.get(path)
                        assert response.status_code == 200, (path, response.status_code)
                        return response.json()

                    project = await post("/projects", {
                        "project_key": "security-agent-" + uuid4().hex[:12],
                        "name": "安全 Agent 本机目标验收",
                    }, 201)
                    session = await post("/sessions", {
                        "project_id": project["id"],
                        "mode_key": "security_testing",
                        "title": "本机低风险 HTTP 安全基线",
                        "session_mode": "background_task",
                        "runtime_mode": "background",
                        "selected_agent": "security-testing-agent",
                        "preferred_model": isolated_model.key,
                    }, 200)

                    # The grant is installed by this test into its isolated
                    # PostgreSQL session after public session creation. A user
                    # message alone cannot create this server-trusted record.
                    sessions = PostgresSessionStore(settings)
                    owned_session = await sessions.get_session(session["id"])
                    assert owned_session is not None
                    owned_session.metadata["resource_scope"] = {
                        "project_id": project["id"],
                        "project_url": target_url,
                        "allowed_targets": [target_url],
                    }
                    owned_session.metadata["security_authorization"] = {
                        "status": "verified",
                        "targets": [target_url],
                        "expires_at": "2099-01-01T00:00:00Z",
                        "approval_scope_hash": "qa-security-agent-" + uuid4().hex,
                    }
                    await sessions.save_session(owned_session)

                    message = (
                        "请通过安全测试模式，对这个本地隔离测试服务做一轮低风险、只读的 HTTP 安全基线检查："
                        f"{target_url}。范围仅限此本机服务；只检查响应和常见安全响应头，禁止凭据测试、"
                        "目录枚举、模糊测试、漏洞利用、持久会话、工具安装、访问其他目标或发送邮件。"
                        "保存真实 Docker runner 证据并生成结构化安全报告。"
                    )
                    accepted = await post(f"/sessions/{session['id']}/messages", {
                        "content": message,
                        "model_key": isolated_model.key,
                        "agent_key": "security-testing-agent",
                    }, 202)
                    task_id = accepted["task"]["id"]
                    facts.update(project_id=project["id"], session_id=session["id"], task_id=task_id,
                                 target_url=target_url)

                    cleanup_worker = processes.start(
                        "security-agent-resource-cleanup",
                        "-m", "src.cli.run_resource_cleanup_worker",
                        "--worker-id", "security-agent-cleanup",
                    )

                    # Scenario-analysis children and campaign workers execute
                    # through the existing durable Agent queue, so give them
                    # independent consumers instead of faking a controlled model.
                    workers = [
                        processes.start(
                            f"security-agent-worker-{index}", "-m", "tests.default_model_probe",
                            "src.cli.run_agent_worker", "--worker-id", f"security-agent-{index}",
                        )
                        for index in range(5)
                    ]
                    timeline = []
                    deadline = monotonic() + float(os.getenv("QA_SECURITY_AGENT_TIMEOUT_SECONDS", "600"))
                    latest_session, latest_tasks = None, []
                    while monotonic() < deadline:
                        await PostgresTaskOutbox(settings).relay_once(queue, limit=20)
                        latest_session = await get(f"/sessions/{session['id']}")
                        latest_tasks = await get(f"/sessions/{session['id']}/tasks")
                        sample = {
                            "session_status": latest_session["status"],
                            "task_statuses": [
                                {key: item.get(key) for key in ("id", "status", "result_status", "error_code")}
                                for item in latest_tasks
                            ],
                            "pending_approval_count": len(latest_session.get("pending_approvals") or []),
                            "worker_dispatch_count": len(latest_session.get("metadata", {}).get("worker_dispatches", [])),
                            "target_request_count": len(target_requests),
                        }
                        if not timeline or timeline[-1] != sample:
                            timeline.append(sample)
                            save("timeline.json", timeline)
                        if latest_session.get("pending_approvals"):
                            facts["unexpected_approval_count"] = len(latest_session["pending_approvals"])
                            save("unexpected-approvals.json", [
                                {"id": item.get("id"), "tool_key": item.get("tool_key"),
                                 "reason_code": (item.get("metadata") or {}).get("permission_reason_code")}
                                for item in latest_session["pending_approvals"]
                            ])
                            pytest.fail("The authorized low-risk local profile unexpectedly stopped for approval.")
                        all_terminal = all(
                            item.get("status") in {
                                "completed", "failed", "interrupted", "cancelled", "recovery_required"
                            }
                            for item in latest_tasks
                        )
                        if latest_session["status"] in {"completed", "failed", "interrupted", "cancelled"} and all_terminal:
                            break
                        assert all(worker.poll() is None for worker in workers), (
                            "An Agent Worker exited before the security task reached a terminal state."
                        )
                        await asyncio.sleep(0.25)

                    assert latest_session is not None, "Session polling did not start."
                    assert cleanup_worker.poll() is None, (
                        "The resource cleanup worker exited before Docker leases were settled."
                    )
                    save("session-status.json", {
                        "status": latest_session["status"],
                        "tasks": latest_tasks,
                        "message_roles": [item.get("role") for item in latest_session.get("messages", [])],
                    })
                    assert latest_session["status"] == "completed", timeline[-5:]
                    assert all(item.get("status") == "completed" for item in latest_tasks), latest_tasks
                    facts["agent_completed"] = True

                    lease_manager = RedisResourceLeaseManager(
                        settings.database.redis_url,
                        cleanup_stream=settings.orchestration.cleanup_task_stream,
                    )
                    try:
                        await lease_manager.connect()
                        cleanup_deadline = monotonic() + 30
                        pending_docker = []
                        active_docker = []
                        while monotonic() < cleanup_deadline:
                            # The lease registry is global; scope this campaign's
                            # assertion to its own project instead of another
                            # session's entries that may still be pending there.
                            pending_docker = [
                                item for item in await lease_manager.list_cleanup_pending(project_id=project["id"])
                                if item.get("resource_type") == "docker"
                            ]
                            active_docker = [
                                item for item in await lease_manager.list_active(project_id=project["id"])
                                if item.get("resource_type") == "docker"
                            ]
                            if not pending_docker and not active_docker:
                                break
                            await asyncio.sleep(0.1)
                        save("resource-cleanup.json", {
                            "pending_docker_count": len(pending_docker),
                            "active_docker_count": len(active_docker),
                        })
                        assert not pending_docker, pending_docker
                        assert not active_docker, active_docker
                    finally:
                        await lease_manager.close()

                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(
                            f"SELECT * FROM {settings.database.postgres_tool_job_table} ORDER BY created_at"
                        )
                        jobs = [dict(row) for row in cur.fetchall()]
                        cur.execute(
                            f"SELECT payload FROM {settings.database.postgres_task_outbox_table} "
                            "WHERE payload->>'task_id'=%s ORDER BY created_at LIMIT 1",
                            (task_id,),
                        )
                        replay_row = cur.fetchone()
                    replay_command = replay_row["payload"] if replay_row else None
                    assert replay_command is not None, "The completed user task has no persisted Outbox command."

                    root_campaign_jobs = [
                        job for job in jobs
                        if job.get("session_id") == session["id"]
                        if job.get("tool_key") == "security-scan-runner"
                        and isinstance(job.get("output_payload"), dict)
                        and isinstance(job["output_payload"].get("report"), dict)
                    ]
                    assert len(root_campaign_jobs) == 1, [
                        {key: job.get(key) for key in ("id", "tool_key", "status", "error_code")}
                        for job in jobs
                    ]
                    campaign_job = root_campaign_jobs[0]
                    trace_jobs = [
                        job for job in jobs
                        if job.get("trace_id") == campaign_job.get("trace_id")
                    ]
                    campaign_jobs = [
                        job for job in trace_jobs
                        if job.get("tool_key") == "security-scan-runner"
                        and isinstance(job.get("output_payload"), dict)
                        and isinstance(job["output_payload"].get("report"), dict)
                    ]
                    assert len(campaign_jobs) == 1, [
                        {key: job.get(key) for key in ("id", "session_id", "tool_key", "status", "error_code")}
                        for job in campaign_jobs
                    ]
                    output = campaign_job["output_payload"]
                    report = output["report"]
                    state = output.get(STATE_METADATA_KEY) or {}
                    campaign = state.get("campaign") or {}
                    planned_tasks = campaign.get("tasks") or []
                    task_facts = [{
                        "task_id": item.get("task_id"),
                        "profile": item.get("command_profile"),
                        "target": item.get("target"),
                        "status": item.get("status"),
                        "risk_level": item.get("risk_level"),
                        "requires_approval": item.get("requires_approval"),
                        "attempts": item.get("attempts"),
                        "artifact_count": len(item.get("artifacts") or []),
                        "failure_category": (item.get("failure_analysis") or {}).get("failure_category"),
                    } for item in planned_tasks]
                    save("campaign-report.json", report)
                    save("campaign-tasks.json", task_facts)
                    facts["tool_jobs"] = [{
                        "id": job.get("id"),
                        "session_id": job.get("session_id"),
                        "tool_key": job.get("tool_key"),
                        "status": job.get("status"),
                        "error_code": job.get("error_code"),
                    } for job in trace_jobs]
                    facts["campaign_job_id"] = campaign_job["id"]
                    facts["campaign_status"] = output.get("status")
                    facts["report_counts"] = {
                        key: report.get(key) for key in (
                            "total_tasks", "completed_tasks", "failed_tasks", "skipped_tasks",
                            "total_findings", "evidence_count", "execution_record_count",
                        )
                    }
                    facts["planned_tasks"] = task_facts
                    facts["target_requests"] = list(target_requests)
                    facts["report_verified"] = True

                    allowed_profiles = {"whatweb_fingerprint", "httpx_probe", "http_headers_probe"}
                    assert planned_tasks, "The campaign did not retain its executed task plan."
                    assert all(item.get("command_profile") in allowed_profiles for item in planned_tasks), task_facts
                    assert all(item.get("target") == target_url for item in planned_tasks), task_facts
                    assert all(item.get("status") == "completed" for item in planned_tasks), task_facts
                    assert any(item.get("command_profile") == "http_headers_probe" for item in planned_tasks), task_facts
                    assert report["completed_tasks"] == report["total_tasks"] and report["failed_tasks"] == 0, report
                    assert report["total_findings"] > 0 and report["evidence_count"] > 0, report
                    assert campaign_job.get("status") == "completed", campaign_job
                    assert target_requests and all(
                        item["method"] in {"GET", "HEAD", "OPTIONS"} for item in target_requests
                    ), target_requests

                    originals = evidence / "originals"
                    originals.mkdir()
                    downloaded = []
                    saved_details = []
                    for job in trace_jobs:
                        if job.get("tool_key") not in {
                            "security-scan-runner", "network-recon-runner", "web-scan-runner",
                            "service-audit-runner", "credential-attack-runner",
                        }:
                            continue
                        job_session_id = str(job.get("session_id") or session["id"])
                        detail = await get(
                            f"/sessions/{job_session_id}/tool-jobs/{job['id']}"
                        )
                        saved_details.append({
                            key: detail.get(key)
                            for key in ("id", "session_id", "tool_key", "status", "error_code", "artifacts")
                        })
                        for artifact in detail.get("artifacts", []):
                            response = await client.get(
                                f"/sessions/{job_session_id}/artifacts/{artifact['id']}/content"
                            )
                            response.raise_for_status()
                            filename = Path(artifact.get("metadata", {}).get("filename") or artifact["id"]).name
                            target = originals / (artifact["id"] + "-" + filename)
                            target.write_bytes(response.content)
                            digest = hashlib.sha256(response.content).hexdigest()
                            metadata = artifact.get("metadata") or {}
                            if metadata.get("evidence_schema") == "security-command-output-v1":
                                assert len(response.content) == metadata["size_bytes"]
                                assert digest == metadata["sha256"]
                            uri = str(artifact.get("path") or "")
                            if uri.startswith("rustfs://"):
                                owned_objects.append(uri)
                            downloaded.append({
                                "id": artifact["id"], "session_id": job_session_id,
                                "tool_key": job.get("tool_key"), "type": artifact.get("artifact_type"),
                                "filename": filename, "size_bytes": len(response.content), "sha256": digest,
                                "evidence_schema": metadata.get("evidence_schema"),
                            })
                    save("runner-job-details.json", saved_details)
                    assert any(item.get("evidence_schema") == "security-command-output-v1" for item in downloaded), downloaded
                    save("downloads.json", downloaded)

                    # Re-read the final report through a fresh API process, then
                    # replay the terminal command to prove it does not rescan.
                    model_calls_before = len(list(physical.glob("*.sdk-create.json")))
                    physical_requests_before = len(list(physical.glob("*.send.json")))
                    target_calls_before = len(target_requests)
                    job_count_before = len(trace_jobs)
                    for worker in workers:
                        await processes.stop(worker)
                    await processes.stop(api)
                    api = processes.start("security-agent-api-restarted", *api_args)
                    await wait_api(client, api)
                    restored = await get(f"/sessions/{session['id']}/tool-jobs/{campaign_job['id']}")
                    assert restored["status"] == campaign_job["status"]
                    await queue.enqueue(replay_command)
                    replay = processes.start(
                        "security-agent-terminal-replay", "-m", "tests.default_model_probe",
                        "src.cli.run_agent_worker", "--once", "--worker-id", "security-agent-replay",
                    )
                    assert await processes.wait(replay, timeout=60) == 0
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(
                            f"SELECT count(*) AS count FROM {settings.database.postgres_tool_job_table} "
                            "WHERE trace_id=%s",
                            (campaign_job.get("trace_id"),),
                        )
                        job_count_after = cur.fetchone()["count"]
                    model_calls_after = len(list(physical.glob("*.sdk-create.json")))
                    physical_requests_after = len(list(physical.glob("*.send.json")))
                    target_calls_after = len(target_requests)
                    facts["terminal_replay_added_model_or_target_calls"] = {
                        "sdk_calls": model_calls_after - model_calls_before,
                        "physical_model_requests": physical_requests_after - physical_requests_before,
                        "target_requests": target_calls_after - target_calls_before,
                        "tool_jobs": job_count_after - job_count_before,
                    }
                    assert facts["terminal_replay_added_model_or_target_calls"] == {
                        "sdk_calls": 0, "physical_model_requests": 0, "target_requests": 0, "tool_jobs": 0,
                    }

                    sdk_records = [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in physical.glob("*.sdk-create.json")
                    ]
                    request_records = [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in physical.glob("*.send.json")
                    ]
                    response_records = [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in physical.glob("*.response.json")
                    ]
                    assert sdk_records and request_records, "No actual model SDK request reached HTTP."
                    assert all(item.get("sdk_create_returned") is True for item in sdk_records), sdk_records
                    assert all(item.get("model_key") == selected.key for item in request_records), request_records
                    assert response_records and all(
                        200 <= int(item.get("status_code") or 0) < 300 for item in response_records
                    ), response_records
                    facts["model_call_trace"] = {
                        "sdk_call_count": len(sdk_records),
                        "physical_http_request_count": len(request_records),
                        "response_statuses": [item.get("status_code") for item in response_records],
                        "selected_model_keys": sorted({item.get("model_key") for item in request_records}),
                    }
                    save("model-call-trace.json", facts["model_call_trace"])

                    docker = DockerResourceManager(settings)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_allocation_table}")
                        allocations = [dict(row) for row in cur.fetchall()]
                    cleanup = []
                    for allocation in allocations:
                        container_id = allocation.get("container_id")
                        if not container_id:
                            continue
                        code, stdout, stderr = await docker._command([
                            "ps", "--all", "--no-trunc", "--filter", "id=" + container_id,
                            "--format", "{{.ID}}",
                        ], timeout=20)
                        cleanup.append({"container_id": container_id, "exit_code": code,
                                        "still_present": bool(stdout.strip()), "stderr": stderr})
                        assert code == 0 and not stdout.strip(), cleanup[-1]
                    save("container-cleanup.json", cleanup)

                    facts["model_sdk_calls"] = model_calls_after
                    facts["physical_model_requests"] = physical_requests_after
                    facts["target_request_count"] = target_calls_after
                    facts["global_default_unchanged"] = (
                        _config_identity(source_models.get_default_active()) == global_default_identity
                    )
                    assert facts["global_default_unchanged"]
                    facts["full_goal_complete"] = False
                    save("verification.json", facts)
            finally:
                await queue.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        facts["global_default_unchanged"] = (
            _config_identity(MySQLModelConfigStore(get_settings()).get_default_active())
            == global_default_identity
        )
        save("verification.json", facts)
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)


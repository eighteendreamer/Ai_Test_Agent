"""Public performance tool arguments must reach actual Docker HTTP requests."""
import asyncio
import base64
import hashlib
import json
import os
import shutil
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from test_execution_worker_cli import (
    child_environment,
    free_port,
    isolated_runtime,
    wait_api,
)
from test_performance_docker_measurement import write_json

from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.session import CreateSessionRequest
from src.schemas.tool_runtime import ModelToolCall

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_PUBLIC_HTTP") != "1",
    reason="actual cached k6/JMeter, owned HTTP, isolated MySQL/PostgreSQL/RustFS and explicit Redis")]


@pytest.mark.parametrize("engine", ["k6", "jmeter"])
@pytest.mark.parametrize("kind", ["json", "text"])
async def test_public_tool_preserves_http_request_and_restart(tmp_path, engine, kind):
    evidence = Path(os.getenv("QA_PERF_PUBLIC_HTTP_DIR", str(tmp_path / "evidence"))) / (engine + "-" + kind)
    evidence.mkdir(parents=True, exist_ok=False)
    redis_url = (os.getenv("QA_ISOLATED_RUNTIME_REDIS_URL") or "").strip()
    assert redis_url, "Set QA_ISOLATED_RUNTIME_REDIS_URL to an isolated test Redis database"
    redis = Redis.from_url(redis_url, decode_responses=True)
    baseline_keys = sorted(str(key) async for key in redis.scan_iter(count=100))
    assert not baseline_keys, f"isolated Redis database must start empty: {baseline_keys}"
    write_json(evidence / "redis-before.json", {"dbsize": len(baseline_keys), "keys": baseline_keys})
    body = ({"flag": False, "count": 0, "nullable": None, "nested": ["中文😀", {"literal": "${1+1}"}]}
        if kind == "json" else "中文😀 ${1+1} ${__groovy(1+1)} \\path\r\nline")
    method = "POST" if kind == "json" else "PUT"
    headers = {"Content-Type": "application/json" if kind == "json" else "text/plain; charset=utf-8",
        "X-Public-Marker": "owned-fixture"}
    events, commands, executions = [], [], []
    docker = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def receive(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                actual = json.loads(raw) if kind == "json" else raw.decode("utf-8")
                matched = actual == body and self.command == method
            except (ValueError, UnicodeError):
                actual, matched = None, False
            matched = matched and all(self.headers.get(key) == value for key, value in headers.items())
            status = 200 if matched else 422
            events.append({"method": self.command, "actual": actual,
                "body_base64": base64.b64encode(raw).decode(), "body_sha256": hashlib.sha256(raw).hexdigest(),
                "content_type": self.headers.get("Content-Type"),
                "marker": self.headers.get("X-Public-Marker"), "matched": matched, "status": status})
            encoded = json.dumps({"matched": matched}).encode()
            # Keep this local functional test to at most about 50 req/s per VU.
            import time
            time.sleep(.02)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = do_POST = do_PUT = do_PATCH = receive

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Docker(DockerResourceManager):
        async def _command(self, args, *, timeout):
            result = await super()._command(args, timeout=timeout)
            commands.append({"args": args, "exit_code": result[0], "stdout": result[1], "stderr": result[2]})
            write_json(evidence / "docker-commands.json", commands)
            return result

    class Runner(PerfRunnerService):
        async def _run_in_docker(self, cmd, script, name, timeout):
            result = await super()._run_in_docker(cmd, script, name, timeout)
            executions.append(asdict(result))
            write_json(evidence / "executions.json", executions)
            return result

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.orchestration.performance_runner_backend = "docker"
            settings.orchestration.performance_max_vus = 2
            settings.orchestration.performance_max_duration_seconds = 3
            settings.orchestration.performance_target_allowlist = "127.0.0.1"
            processes.env = child_environment(settings)
            write_json(evidence / "allocations.json", {"database": settings.database.postgres_database,
                "mysql_tables": [getattr(settings.database, field) for field in
                    ("llm_model_table", "email_config_table", "channel_config_table", "sponsor_config_table", "application_config_table")]})
            port = free_port()
            args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("public-http-api", *args)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "public-http-" + uuid4().hex[:10],
                    "name": "性能公共工具HTTP契约"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"],
                    mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                jobs = ToolJobService(PostgresToolJobStore(settings))
                await jobs.initialize()
                tools = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
                mode = tools._performance_testing_mode_runtime
                adapter = mode._create_engine_adapter(engine)
                docker = Docker(settings)
                code, image, stderr = await docker._command(["image", "inspect", adapter._image, "--format", "{{.Id}}"], timeout=20)
                assert code == 0 and image.strip().startswith("sha256:"), stderr
                mode._runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                tool = ToolRegistry().get("performance-test-runner")
                arguments = {"objective": f"使用{engine}对已确认本机接口验证HTTP参数", "method": method,
                    "target_url": f"http://127.0.0.1:{server.server_port}/owned", "headers": headers, "body_template": body,
                    "virtual_users": 1, "duration_seconds": 2, "run_intent": "regression",
                    "confirm_target": True, "sla_p99_ms": 10000, "sla_error_rate": .01}
                write_json(evidence / "tool-arguments.json", arguments)
                context = ToolExecutionContext(session_id=session["id"], turn_id="public-http",
                    trace_id="public-http", user_message=arguments["objective"], normalized_input=arguments["objective"],
                    context_bundle={"project_id": project["id"]})
                record = None
                try:
                    record = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments=arguments), context)
                    write_json(evidence / "tool-record.json", record.model_dump(mode="json"))
                    write_json(evidence / "saved-context.json", context.context_bundle)
                    for artifact in record.output.get("artifacts", []):
                        uri = artifact.get("path", "")
                        if not uri.startswith("rustfs://"):
                            continue
                        owned.append(uri)
                        original = await objects.read_object_uri(uri)
                        if artifact.get("sha256"):
                            assert hashlib.sha256(original["content"]).hexdigest() == artifact["sha256"]
                        if artifact.get("size_bytes") is not None:
                            assert len(original["content"]) == artifact["size_bytes"]
                        if artifact.get("relative_path") in {"script.js", "test.jmx", "summary.json", "report/statistics.json"}:
                            filename = artifact["execution_id"] + "-" + Path(artifact["relative_path"]).name
                            (evidence / filename).write_bytes(original["content"])
                    write_json(evidence / "owned-objects.json", sorted(set(owned)))
                    assert record.status == "completed" and record.output.get("verdict") == "pass", record.output.get("summary")
                    assert len(executions) == 2 and record.output["smoke_result"]["passed"]
                    assert events and all(event["matched"] for event in events), events[:3]
                    target = record.output["performance_testing_state"]["plan"]["targets"][0]
                    assert target["method"] == method and target["headers"] == headers and target["body_template"] == body
                    assert record.output["metrics"]["samples"] == len(events) - 3
                    assert record.output["metrics_persistence"]["status"] == "saved"
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table}")
                        rows = [dict(row) for row in cur.fetchall()]
                    write_json(evidence / "postgres-metrics.json", rows)
                    assert len(rows) == 1 and rows[0]["metrics_json"] == record.output["metrics"]
                    before = len(events)
                    saved_bundle = json.loads((evidence / "saved-context.json").read_text(encoding="utf-8"))
                    restarted = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
                    restored_context = ToolExecutionContext(session_id=session["id"], turn_id="public-http-replay",
                        trace_id="public-http-replay", user_message="恢复已有性能结果", normalized_input="恢复已有性能结果",
                        context_bundle=saved_bundle)
                    replay = await restarted.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments=arguments), restored_context)
                    assert replay.status == "completed" and len(events) == before
                    await processes.stop(api)
                    api = processes.start("public-http-api-restarted", *args)
                    await wait_api(client, api)
                    response = await client.get(f"/sessions/{session['id']}/tool-jobs/{record.job_id}")
                    response.raise_for_status()
                    assert response.json()["output_payload"]["metrics"] == rows[0]["metrics_json"]
                    write_json(evidence / "facts.json", {"project_id": project["id"], "session_id": session["id"],
                        "engine": engine, "kind": kind, "method": method, "requests": len(events),
                        "smoke_requests": 3, "load_samples": rows[0]["metrics_json"]["samples"],
                        "verdict": record.output["verdict"], "terminal_replay_requests": len(events) - before,
                        "api_restart_readback": True, "model_calls": 0, "injected_plan": False})
                finally:
                    worker = processes.start("public-http-cleanup", "-m", "src.cli.run_resource_cleanup_worker",
                        "--once", "--worker-id", "public-http-cleanup")
                    assert await processes.wait(worker) == 0
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                        receipts = [dict(row) for row in cur.fetchall()]
                    write_json(evidence / "cleanup-receipts.json", receipts)
                    assert len(receipts) == len(executions) and all(row["status"] == "completed" for row in receipts)
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        write_json(evidence / "target-events.json", events)
        if docker:
            for command in commands:
                if command["args"][0] == "create" and command["exit_code"] == 0:
                    code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter",
                        "id=" + command["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                    assert code == 0 and not stdout.strip(), stderr
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name == "postgres-finally.json":
                shutil.copyfile(path, evidence / path.name)
        try:
            after_keys = sorted(str(key) async for key in redis.scan_iter(count=100))
            created_keys = sorted(set(after_keys) - set(baseline_keys))
            if created_keys:
                await redis.delete(*created_keys)
            final_keys = sorted(str(key) async for key in redis.scan_iter(count=100))
            cleanup = {"created_keys": created_keys, "remaining_keys": final_keys,
                "baseline_keys": baseline_keys, "restored": final_keys == baseline_keys}
            write_json(evidence / "redis-cleanup.json", cleanup)
            assert cleanup["restored"], cleanup
        finally:
            await redis.aclose()

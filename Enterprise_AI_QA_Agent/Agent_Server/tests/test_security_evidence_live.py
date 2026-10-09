"""Actual existing header profile, ToolJob, object storage and API download.

Three calls, two GETs against this test's owned service; no AI/provider.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_SECURITY_EVIDENCE") != "1", reason="actual Kali/PG/MySQL/RustFS/Redis")]


async def test_actual_header_findings_and_originals(tmp_path):
    await actual_header_evidence(tmp_path)


async def actual_header_evidence(tmp_path, report_probe=None):
    evidence = Path(os.getenv("QA_SECURITY_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    events, observations = [], []

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            events.append({"path": self.path, "method": "GET"})
            self.send_response(200)
            if self.path == "/complete":
                for name, value in (("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"),
                    ("Strict-Transport-Security", "max-age=31536000"), ("Content-Security-Policy", "default-src 'none'"),
                    ("Referrer-Policy", "no-referrer"), ("Permissions-Policy", "camera=()")):
                    self.send_header(name, value)
            # Non-sensitive evidence larger than the current transcript preview.
            self.send_header("X-Owned-Evidence", "owned-prefix-" + "a" * 30000 + "-owned-suffix")
            self.end_headers()
            self.wfile.write(b"owned local security fixture")

        def log_message(self, *_args):
            pass  # Request facts captured above; no production logging override.

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            settings.security.security_runner_backend = "docker"
            settings.security.security_runner_docker_image = "sha256:9520f0e11a5c74e80c758260e5ac2ad46bcae90fc0dda45670cb50b9f421c3d4"
            settings.security.security_runner_docker_net_raw = False
            settings.security.security_runner_docker_cleanup_after_run = True
            settings.security.security_target_allowlist = "127.0.0.1"
            api_port = free_port()
            api = processes.start("security-evidence-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            tools = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                    await wait_api(client, api)
                    response = await client.post("/projects", json={"project_key": "security-" + uuid4().hex[:10], "name": "安全证据验收"})
                    response.raise_for_status()
                    project = response.json()
                    response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="security_testing").model_dump(mode="json"))
                    response.raise_for_status()
                    session = response.json()
                    tool = ToolDescriptor(key="web-scan-runner", name="web scan", description="owned header probe", category="test")
                    records = []
                    for variant in ("missing", "complete", "unreachable"):
                        port = free_port() if variant == "unreachable" else server.server_port
                        target = f"http://127.0.0.1:{port}/{variant}"
                        context = ToolExecutionContext(session_id=session["id"], turn_id="security-" + variant,
                            trace_id="security-evidence-" + variant, user_message="owned local header validation", normalized_input="security",
                            context_bundle={"project_id": project["id"], "environment": "testing"})
                        record = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key,
                            arguments={"command_profile": "http_headers_probe", "target": target}), context)
                        records.append(record)
                        save(variant + "-record.json", record.model_dump(mode="json"))
                        observations.append({"variant": variant, "status": record.status,
                            "findings": len(record.output.get("findings", [])), "exit_code": record.output.get("exit_code"),
                            "error": record.output.get("error")})
                        save("observations.json", observations)
                        for artifact in record.output.get("artifacts", []):
                            if str(artifact.get("path", "")).startswith("rustfs://"):
                                owned_objects.append(artifact["path"])
                    save("target-events.json", events)
                    replay = await tools.record_from_job(await jobs.get_job(records[0].job_id))
                    assert replay.output == records[0].output and len(events) == 2
                    await processes.stop(api)
                    api = processes.start("security-evidence-api-restarted", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
                    await wait_api(client, api)
                    downloads = []
                    for variant, record in zip(("missing", "complete", "unreachable"), records):
                        response = await client.get(f"/sessions/{session['id']}/tool-jobs/{record.job_id}")
                        response.raise_for_status()
                        detail = response.json()
                        save(variant + "-persisted-job.json", detail)
                        for artifact in detail["artifacts"]:
                            response = await client.get(f"/sessions/{session['id']}/artifacts/{artifact['id']}/content")
                            response.raise_for_status()
                            if artifact["metadata"].get("evidence_schema") == "security-command-output-v1":
                                assert len(response.content) == artifact["metadata"]["size_bytes"]
                                assert hashlib.sha256(response.content).hexdigest() == artifact["metadata"]["sha256"]
                            filename = artifact["label"] or Path(artifact["path"]).name
                            folder = evidence / "download-originals" / variant
                            folder.mkdir(parents=True, exist_ok=True)
                            (folder / filename).write_bytes(response.content)
                            downloads.append({"variant": variant, "type": artifact["artifact_type"], "filename": filename,
                                "size_bytes": len(response.content), "sha256": hashlib.sha256(response.content).hexdigest()})
                    save("downloads.json", downloads)
                    response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"]).model_dump(mode="json"))
                    response.raise_for_status()
                    other_session = response.json()
                    artifact_id = (await client.get(f"/sessions/{session['id']}/tool-jobs/{records[0].job_id}")).json()["artifacts"][0]["id"]
                    response = await client.get(f"/sessions/{other_session['id']}/artifacts/{artifact_id}/content")
                    assert response.status_code == 404
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_allocation_table}")
                        allocations = [dict(row) for row in cur.fetchall()]
                    save("allocations.json", allocations)
                    from src.runtime.docker_resource_manager import DockerResourceManager
                    docker = DockerResourceManager(settings)
                    cleanup = []
                    for allocation in allocations:
                        container_id = allocation.get("container_id")
                        if container_id:
                            code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + container_id, "--format", "{{.ID}}"], timeout=20)
                            cleanup.append({"container_id": container_id, "exit_code": code, "stdout": stdout, "stderr": stderr})
                            assert code == 0 and not stdout.strip(), cleanup[-1]
                    save("container-cleanup.json", cleanup)
                    save("facts.json", {"scope": "existing ToolRuntime/header profile; real Kali/PG/RustFS/API restart; no Agent/model or full App",
                        "requests": len(events), "terminal_replay_new_requests": 0, "other_session_download": 404,
                        "observations": observations, "downloads": len(downloads)})
                    if report_probe is not None:
                        await report_probe(settings=settings, tools=tools, jobs=jobs, client=client,
                            session=session, records=records, evidence=evidence, events=events,
                            processes=processes, api_port=api_port, api=api, owned_objects=owned_objects)
                    problems = []
                    if [x["status"] for x in observations] != ["completed", "completed", "failed"]:
                        problems.append("real profile status mismatch")
                    if [x["findings"] for x in observations] != [4, 0, 0]:
                        problems.append("findings must require an observed HTTP response")
                    for variant in ("missing", "complete"):
                        files = list((evidence / "download-originals" / variant).glob("*"))
                        if not any(b"owned-prefix-" + b"a" * 30000 + b"-owned-suffix" in p.read_bytes() for p in files):
                            problems.append(variant + " complete raw output unavailable after restart")
                    assert not problems, problems
            finally:
                await tools._security_execution_environment.close()
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

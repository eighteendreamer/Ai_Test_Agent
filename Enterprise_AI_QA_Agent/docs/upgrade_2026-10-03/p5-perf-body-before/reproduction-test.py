"""Owned HTTP records actual request bytes from both production Docker runners."""
import asyncio
import base64
from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from uuid import uuid4

import httpx
import pytest

from src.application.performance.engine_adapter import RunOptions
from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.application.performance.perf_runner_service import PerfRunnerService
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfTarget, PerfWorkloadConfig, SmokeConfig, SLAConfig, PerformanceTestingState
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from src.runtime.docker_resource_manager import DockerResourceManager
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_BODY") != "1",
    reason="actual cached k6/JMeter and isolated owned HTTP/MySQL/PG/RustFS/Redis")]


class BodyTarget:
    def __init__(self):
        self.events = []
        self.expected = {
            "/json": ("POST", {"enabled": False, "count": 0, "nullable": None,
                "nested": ["中文", {"literal": "${__groovy(1+1)}", "extra": "keep"}]}),
            "/text": ("POST", "中文 ${1+1}\\path\nline"),
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def receive(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                method, expected = owner.expected[self.path]
                try:
                    actual = json.loads(body) if isinstance(expected, dict) else body.decode("utf-8")
                    matched = self.command == method and actual == (expected or "")
                except (ValueError, UnicodeError):
                    actual, matched = None, False
                status = 200 if matched else 422
                # Only public fixture data is recorded; no credentials or model call.
                owner.events.append({"path": self.path, "method": self.command,
                    "content_type": self.headers.get("Content-Type"), "body_base64": base64.b64encode(body).decode(),
                    "body_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                    "actual": actual, "matched": matched, "status": status})
                encoded = json.dumps({"matched": matched}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_POST = do_PUT = do_PATCH = receive

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self.server.server_port

    async def close(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


async def test_actual_request_body_bytes_and_reports(tmp_path):
    evidence = Path(os.getenv("QA_PERF_BODY_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    target = BodyTarget()
    port = target.start()
    commands, observations = [], []
    docker = None

    class Docker(DockerResourceManager):
        async def _command(self, args, *, timeout):
            result = await super()._command(args, timeout=timeout)
            commands.append({"args": args, "exit_code": result[0], "stdout": result[1], "stderr": result[2]})
            write_json(evidence / "docker-commands.json", commands)
            return result

    class Runner(PerfRunnerService):
        variant = ""
        last = None

        async def _run_in_docker(self, cmd, script, name, timeout):
            self.last = await super()._run_in_docker(cmd, script, name, timeout)
            write_json(evidence / (self.variant + "-execution.json"), asdict(self.last))
            (evidence / (self.variant + "-summary.json")).write_text(self.last.summary_json, encoding="utf-8")
            return self.last

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            settings.orchestration.performance_runner_backend = "docker"
            api_port = free_port()
            api = processes.start("body-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "perf-body-" + uuid4().hex[:10], "name": "性能请求正文实际验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                adapters = [K6EngineAdapter(image="sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec"),
                    JMeterEngineAdapter(image="sha256:088ac52b759a198a5afa5ae13d0a6306e9f2017d71ad140ff57427f6930406f7")]

                async def originals(variant, execution_evidence):
                    downloads = []
                    for artifact in execution_evidence.get("artifacts", []):
                        owned_objects.append(artifact["path"])
                        original = await objects.read_object_uri(artifact["path"])
                        content = original["content"]
                        assert hashlib.sha256(content).hexdigest() == artifact["sha256"] and len(content) == artifact["size_bytes"]
                        # Every object is actually downloaded and checked. Keep critical
                        # inputs/results; vendor dashboard assets need not be duplicated.
                        relative = artifact["relative_path"]
                        saved = None
                        if not relative.startswith("report/") or relative.endswith("statistics.json"):
                            saved = evidence / (variant + "-originals") / relative
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            saved.write_bytes(content)
                        downloads.append({"relative_path": relative, "stored_path": saved.relative_to(evidence).as_posix() if saved else None,
                            "sha256": artifact["sha256"], "bytes": len(content)})
                    write_json(evidence / (variant + "-downloads.json"), downloads)

                async def cleanup(variant):
                    child = processes.start("body-cleanup-" + variant, "-m", "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "perf-body-cleanup")
                    assert await processes.wait(child) == 0

                plans = {}
                with bind_request_context(project_id=project["id"], session_id=session["id"], trace_id="actual-perf-body"):
                    for adapter in adapters:
                        runner.variant = adapter.engine_key
                        plan = PerfPlan(engine=adapter.engine_key, targets=[PerfTarget(name=path[1:], method=method,
                            url=f"http://127.0.0.1:{port}" + path, body_template=body,
                            headers={"Content-Type": "application/json" if isinstance(body, dict) else "text/plain; charset=utf-8"})
                            for path, (method, body) in target.expected.items()],
                            smoke=SmokeConfig(vus=1, iterations=3, expect_status=[200]),
                            workload=PerfWorkloadConfig(virtual_users=1, hold_seconds=2, think_time_ms=25),
                            run_intent="regression", sla=SLAConfig(p99_ms=10000, error_rate=0.01))
                        plans[adapter.engine_key] = plan
                        script = adapter.build_script(plan)
                        (evidence / (runner.variant + "." + ("js" if adapter.engine_key == "k6" else "jmx"))).write_text(script.script_content, encoding="utf-8")
                        write_json(evidence / (runner.variant + "-plan.json"), plan.model_dump(mode="json"))
                        prior = len(target.events)
                        smoke = await runner.run_smoke(plan, script, adapter)
                        write_json(evidence / (runner.variant + "-smoke.json"), smoke.model_dump(mode="json"))
                        await originals(runner.variant, smoke.evidence)
                        observations.append({"engine": adapter.engine_key, "passed": smoke.passed,
                            "exit_code": runner.last.exit_code, "events": target.events[prior:]})
                        write_json(evidence / "observations.json", observations)
                        await cleanup(runner.variant)
                    assert all(o["passed"] and o["exit_code"] == 0 and len(o["events"]) == 3 * len(target.expected)
                        and all(e["matched"] for e in o["events"]) for o in observations), observations
                    runtime = PerformanceTestingModeRuntime(settings=settings)
                    runtime._metrics_store = PerfMetricsStore(settings)
                    for adapter in adapters:
                        runner.variant = "report-" + adapter.engine_key
                        plan = plans[adapter.engine_key]
                        run = await runner.execute(plan, adapter.build_script(plan), RunOptions(is_smoke=True, smoke_vus=1, smoke_iterations=3, timeout_seconds=60), adapter)
                        await originals(runner.variant, run.evidence)
                        parsed = PerfResultParser().parse(adapter.parse_results(runner.last.summary_json))
                        report = PerfReportBuilder().build(parsed, plan, run)
                        output = await runtime._handle_report(PerformanceTestingState(project_id=project["id"], plan=plan, run=run, report=report))
                        write_json(evidence / (runner.variant + "-report.json"), report.model_dump(mode="json"))
                        write_json(evidence / (runner.variant + "-output.json"), output)
                        assert run.exit_code == 0 and report.verdict == "pass" and report.sla_result.passed
                        assert report.metrics.samples == 3 * len(target.expected)
                        assert output["metrics_persistence"]["status"] == "saved"
                        await cleanup(runner.variant)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                        receipts = [dict(row) for row in cur.fetchall()]
                        cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY created_at")
                        metrics = [dict(row) for row in cur.fetchall()]
                    write_json(evidence / "postgres-cleanup.json", receipts)
                    write_json(evidence / "postgres-metrics.json", metrics)
                    assert len(receipts) == 4 and all(r["status"] == "completed" for r in receipts)
                    assert len(metrics) == 2 and all(r["verdict"] == "pass" and str(r["project_id"]) == project["id"] for r in metrics)
                    write_json(evidence / "facts.json", {"scope": "actual two Docker engines/HTTP bytes/runner/report/PG/objects; no Agent/model/App",
                        "project_id": project["id"], "session_id": session["id"], "observations": observations})
    finally:
        await target.close()
        write_json(evidence / "target-events.json", target.events)
        if docker:
            for record in list(commands):
                if record["args"][0] == "create" and record["exit_code"] == 0:
                    code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + record["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                    assert code == 0 and not stdout.strip(), stderr
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

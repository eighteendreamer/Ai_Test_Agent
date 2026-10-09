"""Actual JMeter requests prove skipped dependencies and independent work."""
import asyncio
from collections import Counter
from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Lock, Thread
from uuid import uuid4

import httpx
import pytest

from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.performance.engine_adapter import RunOptions
from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.plan_state import Correlation, PerfPlan, PerfTarget, PerfWorkloadConfig, SmokeConfig, SLAConfig, PerformanceTestingState
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from src.runtime.docker_resource_manager import DockerResourceManager
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_JMETER_DEPENDENCIES") != "1",
    reason="actual cached JMeter, owned HTTP and isolated MySQL/PG/RustFS/Redis")]


class JourneyTarget:
    def __init__(self):
        self.events, self.credentials, self.lock = [], set(), Lock()
        self.mode, self.sources = "missing", 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                header = self.headers.get("Authorization", "")
                value = header.removeprefix("Bearer ")
                with owner.lock:
                    accepted = bool(header) and value in owner.credentials
                    issue = None
                    status, body = 200, {"ok": True}
                    if self.path == "/source":
                        owner.sources += 1
                        if owner.mode == "valid" or owner.mode == "mixed" and owner.sources % 2:
                            issue = uuid4().hex
                            body = {"session": {"credential": issue}}
                        else:
                            body = {"session": {}}
                    elif self.path == "/bridge":
                        status = 200 if accepted else 401
                        if accepted:
                            issue = uuid4().hex
                            body = {"session": {"credential": issue}}
                    elif self.path == "/final":
                        status = 200 if accepted else 401
                    if issue:
                        owner.credentials.add(issue)
                    owner.events.append({"path": self.path, "status": status,
                        "accepted": accepted, "header_present": bool(header),
                        "thread": self.headers.get("X-QA-Thread"), "iteration": self.headers.get("X-QA-Iteration"),
                        "credential_sha256": hashlib.sha256(value.encode()).hexdigest() if value else None,
                        "issued_sha256": hashlib.sha256(issue.encode()).hexdigest() if issue else None})
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

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


async def test_actual_jmeter_missing_dependencies_do_not_send_requests(tmp_path):
    evidence = Path(os.getenv("QA_JMETER_DEPENDENCIES_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    target = JourneyTarget()
    port = target.start()
    commands, observations = [], []
    docker = None
    plans = {}

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
            api = processes.start("dependency-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "jmeter-dependency-" + uuid4().hex[:10], "name": "JMeter依赖实际验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                adapter = JMeterEngineAdapter(image="sha256:088ac52b759a198a5afa5ae13d0a6306e9f2017d71ad140ff57427f6930406f7")
                base = f"http://127.0.0.1:{port}"

                async def save_originals(variant, execution_evidence):
                    owned_objects.extend(a["path"] for a in execution_evidence.get("artifacts", []))
                    downloads = []
                    for artifact in execution_evidence.get("artifacts", []):
                        original = await objects.read_object_uri(artifact["path"])
                        content = original["content"]
                        assert all(value.encode() not in content for value in target.credentials)
                        assert hashlib.sha256(content).hexdigest() == artifact["sha256"] and len(content) == artifact["size_bytes"]
                        saved = evidence / (variant + "-originals") / artifact["relative_path"]
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        saved.write_bytes(content)
                        downloads.append({"path": saved.relative_to(evidence).as_posix(), "sha256": artifact["sha256"], "bytes": len(content)})
                    write_json(evidence / (variant + "-downloads.json"), downloads)

                cleanup_child, cleanup_count = None, 0

                async def cleanup(variant):
                    nonlocal cleanup_child, cleanup_count
                    if cleanup_child is None:
                        cleanup_child = processes.start("dependency-cleanup", "-m", "src.cli.run_resource_cleanup_worker", "--worker-id", "jmeter-dependency-cleanup")
                    cleanup_count += 1
                    deadline = asyncio.get_running_loop().time() + 60
                    while asyncio.get_running_loop().time() < deadline:
                        assert cleanup_child.poll() is None, "production cleanup Worker exited before durable receipt"
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute("SELECT to_regclass(%s) AS existing", (settings.database.postgres_resource_cleanup_table,))
                            if cur.fetchone()["existing"]:
                                cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                                receipts = [dict(row) for row in cur.fetchall()]
                                write_json(evidence / (variant + "-cleanup-receipts.json"), receipts)
                                if sum(row["status"] == "completed" for row in receipts) == cleanup_count:
                                    return
                        await asyncio.sleep(0.2)
                    raise TimeoutError(f"durable cleanup receipt missing after {variant}")

                with bind_request_context(project_id=project["id"], session_id=session["id"], trace_id="actual-jmeter-dependencies"):
                    for variant, vus, iterations in (("missing", 1, 3), ("mixed", 1, 4), ("valid", 2, 3), ("tail", 1, 3), ("multiple", 1, 3)):
                        target.mode, target.sources = "missing" if variant == "tail" else "valid" if variant == "multiple" else variant, 0
                        runner.variant = variant
                        names = ("source", "independent", "bridge", "final") if variant == "tail" else (
                            "source", "extra", "bridge", "final", "independent") if variant == "multiple" else ("source", "bridge", "final", "independent")
                        plan = PerfPlan(engine="jmeter", targets=[PerfTarget(name=name, url=base + "/" + name,
                            headers={"X-QA-Thread": "${__threadNum}", "X-QA-Iteration": "${__groovy(vars.getIteration())}"})
                            for name in names],
                            correlations=[Correlation(extract="first", from_path="session.credential", into_header="Authorization", from_target="source", into_target="bridge", header_prefix="Bearer "),
                                Correlation(extract="second", from_path="session.credential", into_header="Authorization", from_target="bridge", into_target="final", header_prefix="Bearer ")],
                            smoke=SmokeConfig(vus=vus, iterations=iterations, expect_status=[200], expect_extract=["first", "second"]),
                            workload=PerfWorkloadConfig(virtual_users=1, hold_seconds=2, think_time_ms=25),
                            run_intent="regression", sla=SLAConfig(p99_ms=10000, error_rate=0.01))
                        if variant == "multiple":
                            plan.correlations.append(Correlation(extract="extra", from_path="session.credential", into_header="X-Second", from_target="extra", into_target="bridge"))
                            plan.smoke.expect_extract.append("extra")
                        plans[variant] = plan
                        script = adapter.build_script(plan)
                        (evidence / (variant + ".jmx")).write_text(script.script_content, encoding="utf-8")
                        write_json(evidence / (variant + "-plan.json"), plan.model_dump(mode="json"))
                        prior = len(target.events)
                        smoke = await runner.run_smoke(plan, script, adapter)
                        write_json(evidence / (variant + "-smoke.json"), smoke.model_dump(mode="json"))
                        await save_originals(variant, smoke.evidence)
                        events = target.events[prior:]
                        observations.append({"variant": variant, "events": events, "counts": dict(Counter(e["path"] for e in events)),
                            "passed": smoke.passed, "exit_code": runner.last.exit_code})
                        write_json(evidence / "observations.json", observations)
                        await cleanup(variant)
                    assert [o["counts"] for o in observations] == [
                        {"/source": 3, "/independent": 3},
                        {"/source": 4, "/bridge": 2, "/final": 2, "/independent": 4},
                        {"/source": 6, "/bridge": 6, "/final": 6, "/independent": 6},
                        {"/source": 3, "/independent": 3},
                        {"/source": 3, "/extra": 3, "/independent": 3}], observations
                    assert [o["passed"] for o in observations] == [False, False, True, False, False]
                    for observation in observations:
                        source_hashes = {(e["thread"], e["iteration"]): e["issued_sha256"] for e in observation["events"] if e["path"] == "/source" and e["issued_sha256"]}
                        bridge_hashes = {(e["thread"], e["iteration"]): e["issued_sha256"] for e in observation["events"] if e["path"] == "/bridge" and e["issued_sha256"]}
                        for event in observation["events"]:
                            assert event["thread"].isdigit() and event["iteration"].isdigit()
                            if event["path"] in {"/bridge", "/final"}:
                                assert event["accepted"] and event["credential_sha256"] == (source_hashes if event["path"] == "/bridge" else bridge_hashes)[(event["thread"], event["iteration"])]
                            else:
                                assert not event["header_present"]
                    runtime = PerformanceTestingModeRuntime(settings=settings)
                    runtime._metrics_store = PerfMetricsStore(settings)
                    for variant in ("missing", "valid"):
                        target.mode, target.sources = variant, 0
                        runner.variant = "report-" + variant
                        plan = plans[variant]
                        prior = len(target.events)
                        run = await runner.execute(plan, adapter.build_script(plan), RunOptions(is_smoke=True, smoke_vus=1, smoke_iterations=3, timeout_seconds=60), adapter)
                        await save_originals(runner.variant, run.evidence)
                        parsed = PerfResultParser().parse(adapter.parse_results(runner.last.summary_json))
                        report = PerfReportBuilder().build(parsed, plan, run)
                        output = await runtime._handle_report(PerformanceTestingState(project_id=project["id"], plan=plan, run=run, report=report))
                        write_json(evidence / (runner.variant + "-run.json"), run.model_dump(mode="json"))
                        write_json(evidence / (runner.variant + "-report.json"), report.model_dump(mode="json"))
                        write_json(evidence / (runner.variant + "-output.json"), output)
                        write_json(evidence / (runner.variant + "-events.json"), target.events[prior:])
                        assert run.exit_code == 0 and report.sla_result.passed is True
                        assert output["status"] == "completed" and output["metrics_persistence"]["status"] == "saved"
                        assert report.verdict == ("fail" if variant == "missing" else "pass")
                        assert report.metrics.samples == (6 if variant == "missing" else 12)
                        await cleanup(runner.variant)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                        receipts = [dict(row) for row in cur.fetchall()]
                        cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY created_at")
                        metrics_rows = [dict(row) for row in cur.fetchall()]
                    write_json(evidence / "postgres-cleanup.json", receipts)
                    write_json(evidence / "postgres-metrics.json", metrics_rows)
                    assert len(receipts) == 7 and all(row["status"] == "completed" for row in receipts)
                    assert [row["verdict"] for row in metrics_rows] == ["fail", "pass"]
                    assert all(str(row["project_id"]) == project["id"] for row in metrics_rows)
                    write_json(evidence / "facts.json", {"scope": "actual JMeter/owned HTTP/production runner and objects; no Agent/model/App",
                        "project_id": project["id"], "session_id": session["id"], "observations": observations})
                    assert observations[0]["passed"] is False and observations[0]["exit_code"] == 0
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

"""Actual k6 and an owned HTTP target verify response facts and header reuse."""
import asyncio
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

from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.performance.engine_adapter import RunOptions
from src.core.request_context import bind_request_context
from src.modes.performance_testing_mode.plan_state import Correlation, PerfPlan, PerfTarget, PerfWorkloadConfig, SmokeConfig, SLAConfig, PerformanceTestingState
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_CORRELATION") != "1",
    reason="actual cached k6, owned HTTP server and isolated stores")]


class Target:
    def __init__(self):
        self.events, self.credentials, self.lock = [], set(), Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                header = self.headers.get("Authorization", "")
                with owner.lock:
                    credential = header.removeprefix("Bearer ")
                    accepted = bool(header) and credential in owner.credentials
                    issued_hash = None
                    if self.path == "/login":
                        value = uuid4().hex
                        owner.credentials.add(value)
                        body, status = {"session": {"credential": value}}, 200
                        issued_hash = hashlib.sha256(value.encode()).hexdigest()
                    elif self.path == "/missing":
                        body, status = {"session": {}}, 200
                    elif self.path in {"/zero", "/false", "/null", "/empty"}:
                        values = {"/zero": 0, "/false": False, "/null": None, "/empty": ""}
                        body, status = {"session": {"credential": values[self.path]}}, 200
                    elif self.path == "/malformed":
                        body, status = "not JSON", 200
                    elif self.path == "/profile":
                        body, status = {"accepted": accepted}, 200 if accepted else 401
                    else:
                        body, status = {"ok": True}, 200
                    owner.events.append({"path": self.path, "status": status,
                        "authorization_present": bool(header), "authorization_accepted": accepted,
                        "authorization_sha256": hashlib.sha256(header.encode()).hexdigest() if header else None,
                        "credential_sha256": hashlib.sha256(credential.encode()).hexdigest() if header else None,
                        "bearer_prefix": header.startswith("Bearer "), "issued_sha256": issued_hash})
                encoded = body.encode() if isinstance(body, str) else json.dumps(body).encode()
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


async def test_actual_response_extraction_and_dependency_header(tmp_path):
    evidence = Path(os.getenv("QA_PERF_CORRELATION_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    commands, observations, cleanups = [], [], []
    target = Target()
    port = target.start()

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
            result = await super()._run_in_docker(cmd, script, name, timeout)
            self.last = result
            write_json(evidence / (self.variant + "-execution.json"), asdict(result))
            (evidence / (self.variant + "-summary.json")).write_text(result.summary_json, encoding="utf-8")
            return result

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            settings.orchestration.performance_runner_backend = "docker"
            api_port = free_port()
            api = processes.start("perf-correlation-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "perf-correlation-" + uuid4().hex[:10], "name": "响应与传值验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                adapter = K6EngineAdapter(image="sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec")
                base = f"http://127.0.0.1:{port}"

                async def cleanup(variant):
                    # Retirement keeps quota until the production cleanup receipt.
                    manager = RedisResourceLeaseManager(settings.database.redis_url,
                        cleanup_stream=settings.orchestration.cleanup_task_stream)
                    await manager.connect()
                    try:
                        before = await manager.usage_snapshot()
                        child = processes.start("perf-cleanup-" + variant, "-m",
                            "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "perf-correlation-cleanup")
                        code = await processes.wait(child)
                        after = await manager.usage_snapshot()
                        cleanups.append({"variant": variant, "exit_code": code, "before": before, "after": after})
                        write_json(evidence / "cleanup-usage.json", cleanups)
                        assert code == 0 and any(before.values()) and not any(after.values()), cleanups[-1]
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT allocation_id, container_name, container_id FROM {settings.database.postgres_resource_allocation_table}")
                            allocations = [dict(row) for row in cur.fetchall()]
                            cur.execute(f"SELECT status, worker_id, attempts, error_type, completed_at, record->>'allocation_id' AS allocation_id FROM {settings.database.postgres_resource_cleanup_table}")
                            receipts = [dict(row) for row in cur.fetchall()]
                        write_json(evidence / "postgres-allocations.json", allocations)
                        write_json(evidence / "postgres-cleanup.json", receipts)
                        assert len(allocations) == len(cleanups) == len(receipts)
                        assert all(row["status"] == "completed" for row in receipts)
                    finally:
                        await manager.close()

                with bind_request_context(project_id=project["id"], session_id=session["id"], trace_id="actual-perf-correlation"):
                    for variant, path, expected, chain in (
                        ("single", "/login", True, False), ("missing-noise", "/missing", False, False),
                        ("wrong-status", "/plain", False, False), ("chain", "/login", True, True),
                        ("zero", "/zero", True, False), ("false", "/false", True, False),
                        ("null", "/null", False, False), ("empty", "/empty", False, False),
                        ("malformed", "/malformed", False, False),
                        ("missing-chain", "/missing", False, True), ("explicit-source", "/login", True, True),
                        ("negative-status", "/profile", True, False)):
                        runner.variant = variant
                        targets = [PerfTarget(name="login", url=base + path)]
                        if chain:
                            targets.append(PerfTarget(name="profile", url=base + "/profile"))
                        correlations = [] if variant in {"wrong-status", "negative-status"} else [Correlation(
                            extract="ticket", from_path="session.credential", into_header="Authorization" if chain else "")]
                        if variant == "missing-chain":
                            targets.append(PerfTarget(name="independent", url=base + "/plain"))
                        if variant == "explicit-source":
                            targets.insert(0, PerfTarget(name="independent", url=base + "/plain"))
                            correlations[0] = Correlation(extract="ticket", from_path="session.credential",
                                from_target="login", into_target="profile", into_header="Authorization", header_prefix="Bearer ")
                        plan = PerfPlan(targets=targets, correlations=correlations,
                            smoke=SmokeConfig(iterations=3, expect_extract=[] if not correlations else ["ticket"],
                                expect_status=[201] if variant == "wrong-status" else [401] if variant == "negative-status" else [200]),
                            workload=PerfWorkloadConfig(model="closed", virtual_users=1, hold_seconds=1))
                        script = adapter.build_script(plan)
                        script.filename = variant + ".js"
                        if variant == "missing-noise":
                            # Engine emits the variable name, not an extracted response value.
                            script.script_content += '\nexport function handleSummary() { return {stdout: "ticket\\n"}; }\n'
                        (evidence / script.filename).write_text(script.script_content, encoding="utf-8")
                        write_json(evidence / (variant + "-plan.json"), plan.model_dump(mode="json"))
                        prior = len(target.events)
                        smoke = await runner.run_smoke(plan, script, adapter)
                        owned_objects.extend(a["path"] for a in smoke.evidence.get("artifacts", []))
                        write_json(evidence / (variant + "-smoke.json"), smoke.model_dump(mode="json"))
                        events = target.events[prior:]
                        observations.append({"variant": variant, "passed": smoke.passed, "expected": expected,
                            "exit_code": runner.last.exit_code, "events": events})
                        write_json(evidence / "observations.json", observations)
                        await cleanup(variant)
                    assert [item["passed"] for item in observations] == [item["expected"] for item in observations], observations
                    chain_events = next(item["events"] for item in observations if item["variant"] == "chain")
                    assert [e["path"] for e in chain_events] == ["/login", "/profile"] * 3
                    assert all(e["authorization_accepted"] for e in chain_events if e["path"] == "/profile")
                    for source, consumer in zip(chain_events[::2], chain_events[1::2]):
                        assert source["issued_sha256"] == consumer["credential_sha256"]
                    missing_events = next(item["events"] for item in observations if item["variant"] == "missing-chain")
                    assert [e["path"] for e in missing_events] == ["/missing", "/plain"] * 3
                    assert all(not e["authorization_present"] for e in missing_events)
                    explicit_events = next(item["events"] for item in observations if item["variant"] == "explicit-source")
                    assert [e["path"] for e in explicit_events] == ["/plain", "/login", "/profile"] * 3
                    for independent, source, consumer in zip(explicit_events[::3], explicit_events[1::3], explicit_events[2::3]):
                        assert not independent["authorization_present"] and consumer["authorization_accepted"] and consumer["bearer_prefix"]
                        assert source["issued_sha256"] == consumer["credential_sha256"]

                    # Load remains independently judged even with exit0 and passing SLA.
                    runtime = PerformanceTestingModeRuntime(settings, artifact_storage_service=objects)
                    loads = []
                    for variant, path, expected in (("load-chain", "/login", "pass"), ("load-missing", "/missing", "fail")):
                        runner.variant = variant
                        load_plan = PerfPlan(run_intent="regression", targets=[PerfTarget(name="login", url=base + path),
                            PerfTarget(name="profile", url=base + "/profile")], correlations=[Correlation(extract="ticket",
                                from_path="session.credential", from_target="login", into_target="profile", into_header="Authorization")],
                            smoke=SmokeConfig(expect_status=[200], expect_extract=["ticket"]),
                            workload=PerfWorkloadConfig(model="closed", virtual_users=1, hold_seconds=1, think_time_ms=100),
                            sla=SLAConfig(p99_ms=1000))
                        script = adapter.build_script(load_plan)
                        script.filename = variant + ".js"
                        (evidence / script.filename).write_text(script.script_content, encoding="utf-8")
                        prior = len(target.events)
                        run = await runner.execute(load_plan, script, RunOptions(timeout_seconds=30), adapter)
                        owned_objects.extend(a["path"] for a in run.evidence.get("artifacts", []))
                        report = PerfReportBuilder().build(PerfResultParser().parse(adapter.parse_results(runner.last.summary_json)), load_plan, run)
                        output = await runtime._handle_report(PerformanceTestingState(plan=load_plan, run=run, report=report))
                        write_json(evidence / (variant + "-run.json"), run.model_dump(mode="json"))
                        write_json(evidence / (variant + "-report.json"), report.model_dump(mode="json"))
                        write_json(evidence / (variant + "-mode-output.json"), output)
                        write_json(evidence / (variant + "-events.json"), target.events[prior:])
                        await cleanup(variant)
                        assert run.exit_code == 0 and report.sla_result.passed is True and report.verdict == expected
                        assert output["request_validation"] == report.request_validation
                        loads.append({"variant": variant, "exit_code": run.exit_code, "sla_passed": report.sla_result.passed,
                            "verdict": report.verdict, "request_validation": report.request_validation["status"]})
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table}")
                        rows = [dict(row) for row in cur.fetchall()]
                        write_json(evidence / "postgres-metrics.json", rows)
                    assert len(rows) == 2 and sorted(row["verdict"] for row in rows) == ["fail", "pass"]
                    write_json(evidence / "facts.json", {"scope": "actual k6 response/header and independent report with owned HTTP; no Agent/model invocation",
                        "project_id": project["id"], "session_id": session["id"], "observations": observations, "loads": loads})
                for record in commands:
                    if record["args"][0] == "create" and record["exit_code"] == 0:
                        code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + record["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                        assert code == 0 and not stdout.strip(), stderr
    finally:
        await target.close()
        write_json(evidence / "target-events.json", target.events)
        # Also retain absence checks when a scenario/assertion fails.
        for record in list(commands):
            if record["args"][0] == "create" and record["exit_code"] == 0:
                code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter",
                    "id=" + record["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                assert code == 0 and not stdout.strip(), stderr
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

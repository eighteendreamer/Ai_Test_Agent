"""Actual response types and downstream requests, using both cached engines."""
import asyncio
from collections import Counter
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
from src.application.performance.perf_runner_service import PerfRunnerService
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

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_TYPED_RESPONSE") != "1",
    reason="actual cached engines, owned HTTP and isolated MySQL/PG/RustFS/Redis")]

CASES = {
    "string-null": ({"value": "null"}, "value", "value", "null", True),
    "object-header": ({"value": {"first": "one", "second": "two"}}, "value", "value", None, False),
    "array-header": ({"value": ["one", "two"]}, "value", "value", None, False),
    "source-status": ({"value": "unexpected-source"}, "value", "value", None, False),
    "json-null": ({"value": None}, "value", "value", None, False),
    "zero": ({"value": 0}, "value", "value", "0", True),
    "false": ({"value": False}, "value", "value", "false", True),
    "index": ({"items": [{"value": "wrong"}, {"value": "selected"}]}, "items.1.value", "items[1].value", "selected", True),
    "filter": ({"items": [{"kind": "other", "value": "wrong"}, {"kind": "wanted", "value": "filtered"}]},
        'items.#(kind=="wanted").value', "items[?kind == 'wanted'].value | [0]", "filtered", True),
    "quoted-key": ({"account.id": "literal-dot"}, "account\\.id", '"account.id"', "literal-dot", True),
    "object-only": ({"value": {"a": 0, "b": False, "c": None}}, "value", "value", None, True),
    "array-only": ({"value": ["one", "two", None]}, "value", "value", None, True),
    "empty-array-only": ({"value": []}, "value", "value", None, True),
    "empty-array-header": ({"value": []}, "value", "value", None, False),
    "empty-string": ({"value": ""}, "value", "value", None, False),
    "missing": ({}, "value", "value", None, False),
    "malformed": ('public-not-json', "value", "value", None, False),
    "trailing-json": ('{"value":"public-prefix"} {"unexpected":"suffix"}', "value", "value", None, False),
}


class TypedTarget:
    def __init__(self):
        self.events, self.variant = [], "string-null"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                payload, _, _, wanted, _ = CASES[owner.variant]
                header = self.headers.get("X-Value")
                status = 403 if self.path == "/source" and owner.variant == "source-status" else 200
                body = payload if self.path == "/source" else {"ok": True}
                owner.events.append({"variant": owner.variant, "path": self.path, "status": status,
                    "header_present": header is not None, "header_matches": header == wanted,
                    "header_sha256": hashlib.sha256(header.encode()).hexdigest() if header is not None else None})
                encoded = (body if self.path == "/source" and isinstance(body, str) else json.dumps(body)).encode()
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


async def test_actual_typed_response_and_header_constraints(tmp_path):
    evidence = Path(os.getenv("QA_PERF_TYPED_RESPONSE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    target = TypedTarget()
    port = target.start()
    commands, observations, plans = [], [], {}
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
            api = processes.start("typed-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "perf-typed-" + uuid4().hex[:10], "name": "性能响应类型实际验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                adapters = [K6EngineAdapter(image="sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec"),
                    JMeterEngineAdapter(image="sha256:088ac52b759a198a5afa5ae13d0a6306e9f2017d71ad140ff57427f6930406f7")]

                async def originals(variant, result):
                    downloads = []
                    for artifact in result.get("artifacts", []):
                        owned_objects.append(artifact["path"])
                        content = (await objects.read_object_uri(artifact["path"]))["content"]
                        assert hashlib.sha256(content).hexdigest() == artifact["sha256"] and len(content) == artifact["size_bytes"]
                        relative, saved = artifact["relative_path"], None
                        if not relative.startswith("report/") or relative.endswith("statistics.json"):
                            saved = evidence / (variant + "-originals") / relative
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            saved.write_bytes(content)
                        downloads.append({"relative_path": relative, "stored_path": saved.relative_to(evidence).as_posix() if saved else None,
                            "sha256": artifact["sha256"], "bytes": len(content)})
                    write_json(evidence / (variant + "-downloads.json"), downloads)

                async def cleanup(variant):
                    child = processes.start("typed-cleanup-" + variant, "-m", "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "perf-typed-cleanup")
                    assert await processes.wait(child) == 0

                async def pg_facts():
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                        receipts = [dict(r) for r in cur.fetchall()]
                        # Metrics storage initializes lazily on the first report.
                        cur.execute("SELECT to_regclass(%s) AS existing", (settings.database.postgres_perf_runs_table,))
                        if cur.fetchone()["existing"]:
                            cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY created_at")
                            metrics = [dict(r) for r in cur.fetchall()]
                        else:
                            metrics = []
                    write_json(evidence / "postgres-cleanup.json", receipts)
                    write_json(evidence / "postgres-metrics.json", metrics)
                    return receipts, metrics

                with bind_request_context(project_id=project["id"], session_id=session["id"], trace_id="actual-perf-typed-response"):
                    for adapter in adapters:
                        for variant, (_, k6_path, jmeter_path, wanted, passed) in CASES.items():
                            target.variant, runner.variant = variant, adapter.engine_key + "-" + variant
                            path = k6_path if adapter.engine_key == "k6" else jmeter_path
                            only = variant.endswith("-only")
                            plan = PerfPlan(engine=adapter.engine_key,
                                targets=[PerfTarget(name=name, url=f"http://127.0.0.1:{port}/" + name) for name in (("source", "plain") if only else ("source", "consumer", "plain"))],
                                correlations=[Correlation(extract="value", from_path=path, into_header="" if only else "X-Value", from_target="source", into_target=None if only else "consumer")],
                                smoke=SmokeConfig(vus=1, iterations=3, expect_status=[200], expect_extract=["value"]),
                                workload=PerfWorkloadConfig(virtual_users=1, hold_seconds=2, think_time_ms=25),
                                run_intent="regression", sla=SLAConfig(p99_ms=10000, error_rate=0.01))
                            plans[(adapter.engine_key, variant)] = plan
                            script = adapter.build_script(plan)
                            (evidence / (runner.variant + "." + ("js" if adapter.engine_key == "k6" else "jmx"))).write_text(script.script_content, encoding="utf-8")
                            write_json(evidence / (runner.variant + "-plan.json"), plan.model_dump(mode="json"))
                            prior = len(target.events)
                            smoke = await runner.run_smoke(plan, script, adapter)
                            write_json(evidence / (runner.variant + "-smoke.json"), smoke.model_dump(mode="json"))
                            await originals(runner.variant, smoke.evidence)
                            events = target.events[prior:]
                            observations.append({"engine": adapter.engine_key, "variant": variant, "passed": smoke.passed,
                                "expected": passed, "expected_consumer": wanted is not None, "exit_code": runner.last.exit_code,
                                "events": events, "counts": dict(Counter(e["path"] for e in events))})
                            write_json(evidence / "observations.json", observations)
                            await cleanup(runner.variant)
                    await pg_facts()
                    assert all(o["passed"] == o["expected"] and o["exit_code"] == 0
                        and o["counts"] == ({"/source": 3, "/consumer": 3, "/plain": 3} if o["expected_consumer"] else {"/source": 3, "/plain": 3})
                        and all(e["header_matches"] for e in o["events"] if e["path"] == "/consumer") for o in observations), observations
                    runtime = PerformanceTestingModeRuntime(settings=settings)
                    runtime._metrics_store = PerfMetricsStore(settings)
                    for adapter in adapters:
                        for variant in ("string-null", "object-header"):
                            target.variant, runner.variant = variant, "report-" + adapter.engine_key + "-" + variant
                            plan = plans[(adapter.engine_key, variant)]
                            prior = len(target.events)
                            run = await runner.execute(plan, adapter.build_script(plan), RunOptions(is_smoke=True, smoke_vus=1, smoke_iterations=3, timeout_seconds=60), adapter)
                            await originals(runner.variant, run.evidence)
                            report = PerfReportBuilder().build(PerfResultParser().parse(adapter.parse_results(runner.last.summary_json)), plan, run)
                            output = await runtime._handle_report(PerformanceTestingState(project_id=project["id"], plan=plan, run=run, report=report))
                            write_json(evidence / (runner.variant + "-report.json"), report.model_dump(mode="json"))
                            write_json(evidence / (runner.variant + "-output.json"), output)
                            write_json(evidence / (runner.variant + "-events.json"), target.events[prior:])
                            assert run.exit_code == 0 and report.sla_result.passed and output["metrics_persistence"]["status"] == "saved"
                            assert report.verdict == ("pass" if variant == "string-null" else "fail")
                            await cleanup(runner.variant)
                    receipts, metrics = await pg_facts()
                    assert len(receipts) == len(CASES) * 2 + 4 and all(r["status"] == "completed" for r in receipts)
                    assert [r["verdict"] for r in metrics] == ["pass", "fail", "pass", "fail"]
                    assert all(str(r["project_id"]) == project["id"] for r in metrics)
                    write_json(evidence / "facts.json", {"scope": "actual two engines/typed HTTP/runner/report/PG/objects; no Agent/model/App",
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

"""Actual cached k6 image, managed HTTP target and original summary evidence."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest

from src.application.performance.engine_adapter import RunOptions
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.mock_target_runner import MockTargetRunner
from src.application.performance.perf_runner_service import PerfRunnerService
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfTarget, PerfWorkloadConfig, PerformanceTestingState, SLAConfig
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from src.runtime.docker_resource_manager import DockerResourceManager
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCKER_PERF_MEASUREMENT") != "1",
    reason="actual cached k6/http-echo, independent API and isolated stores")]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


async def test_actual_k6_measurements_thresholds_missing_and_postgres(tmp_path):
    evidence = Path(os.getenv("QA_DOCKER_PERF_MEASUREMENT_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    commands = []

    class ObservedDocker(DockerResourceManager):
        async def _command(self, args, *, timeout):
            result = await super()._command(args, timeout=timeout)
            commands.append({"args": args, "exit_code": result[0], "stdout": result[1], "stderr": result[2]})
            write_json(evidence / "docker-commands.json", commands)
            return result

    class ObservedRunner(PerfRunnerService):
        async def _run_in_docker(self, cmd, script, name, timeout):
            result = await super()._run_in_docker(cmd, script, name, timeout)
            write_json(evidence / (script.filename + "-execution.json"), asdict(result))
            (evidence / (script.filename + "-summary.json")).write_text(result.summary_json, encoding="utf-8")
            return result

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
            settings.orchestration.performance_runner_backend = "docker"
            api_port = free_port()
            api = processes.start("perf-measurement-api", "-m", "uvicorn", "src.main:app",
                "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "perf-measurement-" + uuid4().hex[:10], "name": "性能指标真实核验"})
                assert response.status_code == 201, response.text
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = ObservedDocker(settings)
                code, image, stderr = await docker._command(["image", "inspect", "grafana/k6:latest", "--format", "{{.Id}}"], timeout=20)
                assert code == 0 and image.strip().startswith("sha256:"), stderr
                code, version, stderr = await docker._command(["run", "--rm", image.strip(), "version"], timeout=20)
                assert code == 0, stderr
                write_json(evidence / "image-version.json", {"image": image.strip(), "version": version})
                target = MockTargetRunner(settings, docker_resources=docker)
                results = []
                try:
                    with bind_request_context(project_id=project["id"], session_id=session["id"]):
                        started = await target.start(port=free_port(), response_text="QA actual measurement " + uuid4().hex)
                        assert started.ok, started
                        code, address, stderr = await docker._command(["inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", started.stdout], timeout=20)
                        assert code == 0 and address.strip(), stderr
                        plan = PerfPlan(plan_id="actual-perf-" + uuid4().hex[:12], run_intent="regression",
                            targets=[PerfTarget(name="owned-target", url=f"http://{address.strip()}:5678/")],
                            workload=PerfWorkloadConfig(model="closed", mode="constant_vus", virtual_users=1,
                                hold_seconds=2, think_time_ms=50), sla=SLAConfig(p99_ms=1000))
                        adapter = K6EngineAdapter(image=image.strip())
                        runner = ObservedRunner(settings, docker_resources=docker)
                        runtime = PerformanceTestingModeRuntime(settings)
                        variants = [
                            ("default", RunOptions(timeout_seconds=30), 1000, "pass"),
                            ("smoke", RunOptions(timeout_seconds=30, is_smoke=True), 1000, "pass"),
                            ("missing", RunOptions(timeout_seconds=30, extra_args=["--summary-trend-stats=avg,min,med,max,p(90),p(95)"]), 1000, "inconclusive"),
                            ("violating", RunOptions(timeout_seconds=30), 0.001, "fail"),
                        ]
                        for variant, options, p99_limit, expected in variants:
                            variant_plan = plan.model_copy(deep=True)
                            variant_plan.sla.p99_ms = p99_limit
                            script = adapter.build_script(variant_plan)
                            script.filename = variant + ".js"
                            (evidence / script.filename).write_text(script.script_content, encoding="utf-8")
                            run = await runner.execute(variant_plan, script, options, adapter)
                            parsed = PerfResultParser().parse(adapter.parse_results((evidence / (script.filename + "-summary.json")).read_text(encoding="utf-8")))
                            report = PerfReportBuilder().build(parsed, variant_plan, run)
                            write_json(evidence / (variant + "-run.json"), run.model_dump(mode="json"))
                            write_json(evidence / (variant + "-report.json"), report.model_dump(mode="json"))
                            (evidence / (variant + "-report.md")).write_text(report.report_markdown, encoding="utf-8")
                            (evidence / (variant + "-report.html")).write_text(report.report_html, encoding="utf-8")
                            output = await runtime._handle_report(PerformanceTestingState(plan=variant_plan, run=run, report=report))
                            write_json(evidence / (variant + "-mode-output.json"), output)
                            assert parsed.metrics.samples > 0 and report.verdict == expected, report.report_markdown
                            assert run.status == ("failed" if variant == "violating" else "completed"), run
                            if variant == "missing":
                                assert parsed.metrics.p99_ms is None and report.sla_result.passed is None
                                assert report.sla_result.missing_metrics == ["p99_ms"]
                                assert "未采集" in report.report_html
                            else:
                                assert parsed.metrics.p99_ms > 0
                                assert report.sla_result.passed is (variant != "violating")
                            if variant in {"default", "violating"}:
                                assert run.engine_thresholds and report.engine_threshold_crosscheck.agree
                                assert all(v["ok"] is (variant == "default") for v in run.engine_thresholds.values())
                            if variant == "violating":
                                assert run.engine_thresholds == {"http_req_duration:p(99)<0.001": {"ok": False}}
                            assert output["verdict"] == expected and output["metrics"]["p99_ms"] == parsed.metrics.p99_ms
                            results.append((variant, run, parsed, report))
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY started_at")
                            rows = [dict(row) for row in cur.fetchall()]
                            write_json(evidence / "postgres-metrics.json", rows)
                        assert len(rows) == 4
                        for name, run, parsed, report in results:
                            row = next(row for row in rows if row["run_id"] == run.run_id)
                            assert row["metrics_json"]["p99_ms"] == parsed.metrics.p99_ms and row["verdict"] == report.verdict
                        baseline = await runtime._metrics_store.get_baseline(plan.targets[0].url)
                        assert baseline is not None and baseline.p99_ms == results[1][2].metrics.p99_ms
                        write_json(evidence / "facts.json", {"project_id": project["id"], "session_id": session["id"],
                            "scope": "production runner/parser/report with managed actual Docker; no Agent/model invocation",
                            "target": started.model_dump(), "target_url": plan.targets[0].url,
                            "variants": [{"variant": name, "parsed_p99_ms": parsed.metrics.p99_ms,
                                "report_verdict": report.verdict} for name, _, parsed, report in results]})
                finally:
                    await target.close()
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_allocation_table}")
                        write_json(evidence / "postgres-allocations.json", [dict(row) for row in cur.fetchall()])
                    for record in commands:
                        if record["args"][0] == "create" and record["exit_code"] == 0:
                            container_id = record["stdout"].strip()
                            code, existing, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + container_id, "--format", "{{.ID}}"], timeout=20)
                            assert code == 0 and not existing.strip(), stderr
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                shutil.copyfile(path, evidence / path.name)

"""Real JMeter load, ToolJob/PG report storage, and independent API recovery."""
from dataclasses import asdict
import csv
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest

from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.contracts import PHASE_PLAN_RESOLVED, STATE_METADATA_KEY
from src.modes.performance_testing_mode.plan_state import Correlation, PerfPlan, PerfTarget, PerfWorkloadConfig, PerformanceTestingState, SLAConfig
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.session import CreateSessionRequest
from src.schemas.tool_runtime import ModelToolCall
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_correlation_live import Target
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_JMETER_REPORT") != "1",
    reason="actual cached JMeter and isolated MySQL/PostgreSQL/RustFS/Redis")]


async def test_jmeter_tool_reports_restore_and_rejected_metrics_write(tmp_path):
    evidence = Path(os.getenv("QA_JMETER_REPORT_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    class LoadServer(ThreadingHTTPServer):
        # Before-snapshot JTL measured 69,312 ms in Connect, not handler work.
        # This owned target accepts bursts promptly; it is not a capacity test.
        request_queue_size = 128

        def serve_forever(self, poll_interval=0.01):
            return super().serve_forever(poll_interval=poll_interval)

    target = Target(server_factory=LoadServer)
    port = target.start()
    commands, executions, records, observations = [], [], [], []
    docker = None

    class Docker(DockerResourceManager):
        async def _command(self, args, *, timeout):
            result = await super()._command(args, timeout=timeout)
            commands.append({"args": args, "exit_code": result[0], "stdout": result[1], "stderr": result[2]})
            write_json(evidence / "docker-commands.json", commands)
            return result

    class Runner(PerfRunnerService):
        variant = ""

        async def _run_in_docker(self, cmd, script, name, timeout):
            result = await super()._run_in_docker(cmd, script, name, timeout)
            entry = {"variant": self.variant, "smoke": "-Jqa_perf.smoke=true" in cmd.command,
                "command": cmd.command, "result": asdict(result)}
            executions.append(entry)
            write_json(evidence / "executions.json", executions)
            return result

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            settings.orchestration.performance_runner_backend = "docker"
            api_port = free_port()
            api = processes.start("jmeter-report-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "jmeter-report-" + uuid4().hex[:10], "name": "JMeter报告真实验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                adapter = JMeterEngineAdapter(image="sha256:088ac52b759a198a5afa5ae13d0a6306e9f2017d71ad140ff57427f6930406f7")
                jobs = ToolJobService(PostgresToolJobStore(settings))
                await jobs.initialize()
                tools = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
                mode = tools._performance_testing_mode_runtime
                mode._runner = runner
                # Only cached-image selection is injected; guard, smoke, load,
                # analysis, persistence and result handling remain production.
                mode._create_engine_adapter = lambda engine: adapter
                assert await mode._metrics_store.initialize()
                table = settings.database.postgres_perf_runs_table
                tool = ToolDescriptor(key="performance-test-runner", name="performance", description="owned JMeter report validation", category="test")
                for variant in ("normal", "sla-failed", "pg-rejected"):
                    runner.variant = variant
                    plan = PerfPlan(plan_id="jmeter-plan-" + uuid4().hex[:8], engine="jmeter", run_intent="regression",
                        targets=[PerfTarget(name="login", url=f"http://127.0.0.1:{port}/login"),
                            PerfTarget(name="profile", url=f"http://127.0.0.1:{port}/profile")],
                        correlations=[Correlation(extract="ticket", from_path="session.credential", into_header="Authorization", header_prefix="Bearer ")],
                        workload=PerfWorkloadConfig(model="closed", virtual_users=3, hold_seconds=4, think_time_ms=100),
                        sla=SLAConfig(p99_ms=0.001 if variant == "sla-failed" else 10000, error_rate=0.01))
                    context = ToolExecutionContext(session_id=session["id"], turn_id="jmeter-" + variant,
                        trace_id="jmeter-report-" + variant, user_message="owned JMeter report check", normalized_input="performance",
                        context_bundle={"project_id": project["id"], STATE_METADATA_KEY: PerformanceTestingState(
                            session_id=session["id"], phase=PHASE_PLAN_RESOLVED, plan=plan).model_dump()})
                    if variant == "pg-rejected":
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT owned_reject_perf_write CHECK (false) NOT VALID")
                    prior, event_start = len(executions), len(target.events)
                    record = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments={}), context)
                    records.append(record)
                    write_json(evidence / f"{variant}-record.json", record.model_dump(mode="json"))
                    write_json(evidence / f"{variant}-context.json", context.context_bundle)
                    write_json(evidence / f"{variant}-events.json", target.events[event_start:])
                    owned_objects.extend(a["path"] for a in record.output.get("artifacts", []) if a.get("path", "").startswith("rustfs://"))
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {table} ORDER BY completed_at")
                        rows = [dict(row) for row in cur.fetchall()]
                    write_json(evidence / "postgres-metrics.json", rows)
                    observations.append({"variant": variant, "tool_status": record.status,
                        "verdict": record.output.get("verdict"), "phase": record.output.get("phase"),
                        "engine_runs": len(executions) - prior,
                        "metrics_row_exists": any(row["run_id"] == record.output.get("run_id") for row in rows)})
                    samples = list(csv.DictReader(io.StringIO(json.loads(executions[-1]["result"]["summary_json"])["__jtl"])))
                    observations[-1].update({"load_samples": len(samples),
                        "max_load_threads": max(int(row["allThreads"]) for row in samples),
                        "max_connect_ms": max(int(row["Connect"]) for row in samples)})
                    write_json(evidence / "observations.json", observations)
                    assert len(executions) == prior + 2 and record.output["smoke_result"]["passed"]
                    assert record.output["request_validation"]["status"] == "passed"
                    assert observations[-1]["max_load_threads"] == 3 and len(samples) > 6
                    assert record.output["verdict"] == ("fail" if variant == "sla-failed" else "pass")
                    for _ in range(2):
                        child = processes.start(f"cleanup-{variant}-{_}", "-m", "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "jmeter-report-cleanup")
                        assert await processes.wait(child) == 0
                    if variant == "pg-rejected":
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT owned_reject_perf_write")
                        assert record.status == "partial" and record.output["phase"] == "analyzed", observations[-1]
                        assert record.output["metrics_persistence"] == {"status": "failed", "retryable": True}
                        count = len(executions)
                        restored = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
                        restored._performance_testing_mode_runtime._runner = runner
                        restored._performance_testing_mode_runtime._create_engine_adapter = lambda engine: adapter
                        resumed = await restored.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments={}), context)
                        assert resumed.status == "completed" and len(executions) == count
                        assert resumed.output["metrics_persistence"] == {"status": "saved", "retryable": False}
                        assert (await jobs.get_job(record.job_id)).status.value == "partial"
                        assert resumed.output["run_id"] == record.output["run_id"]
                        records.append(resumed)
                        write_json(evidence / "pg-resumed-record.json", resumed.model_dump(mode="json"))
                        owned_objects.extend(a["path"] for a in resumed.output.get("artifacts", []) if a.get("path", "").startswith("rustfs://"))
                count = len(executions)
                for record in records:
                    replay = await tools.record_from_job(await jobs.get_job(record.job_id))
                    assert replay.output == record.output and len(executions) == count
                await processes.stop(api)
                api = processes.start("jmeter-report-api-restarted", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
                await wait_api(client, api)
                downloads = []
                for record in records:
                    response = await client.get(f"/sessions/{session['id']}/tool-jobs/{record.job_id}")
                    response.raise_for_status()
                    detail = response.json()
                    assert detail["status"] == record.status
                    assert detail["output_payload"] == {k: v for k, v in record.output.items() if k not in {"download_urls", "download_markdown"}}
                    for artifact in detail["artifacts"]:
                        response = await client.get(f"/sessions/{session['id']}/artifacts/{artifact['id']}/content")
                        assert response.status_code == 200
                        content = response.content
                        assert all(value.encode() not in content for value in target.credentials)
                        if artifact["metadata"].get("sha256"):
                            assert hashlib.sha256(content).hexdigest() == artifact["metadata"]["sha256"]
                            assert len(content) == artifact["metadata"]["size_bytes"]
                        # Preserve request/log/report originals; static dashboard
                        # assets are verified by hash rather than copied again.
                        relative = artifact["metadata"].get("relative_path", "")
                        saved = None
                        if not relative.startswith("report/") or relative == "report/statistics.json":
                            path = evidence / "downloads" / artifact["id"] / Path(artifact["metadata"].get("filename") or "artifact").name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(content)
                            saved = path.relative_to(evidence).as_posix()
                        downloads.append({"artifact_id": artifact["id"], "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "saved": saved})
                write_json(evidence / "downloads.json", downloads)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {table} ORDER BY completed_at")
                    rows = [dict(row) for row in cur.fetchall()]
                    cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                    receipts = [dict(row) for row in cur.fetchall()]
                write_json(evidence / "postgres-metrics-final.json", rows)
                write_json(evidence / "postgres-cleanup.json", receipts)
                assert len(rows) == 3 and len(receipts) == 6 and all(row["status"] == "completed" for row in receipts)
                assert len(executions) == 6
                write_json(evidence / "facts.json", {"scope": "actual JMeter/production guard, tool, PG/RustFS/API restart; fixed plan/cached image; no Agent/model/App",
                    "observations": observations, "replay_added_engines": len(executions) - count,
                    "pg_resume_added_engines": 0, "download_count": len(downloads)})
    finally:
        await target.close()
        write_json(evidence / "target-events.json", target.events)
        if docker is not None:
            for record in list(commands):
                if record["args"][0] == "create" and record["exit_code"] == 0:
                    code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + record["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                    assert code == 0 and not stdout.strip(), stderr
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

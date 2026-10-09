"""Two actual projects and PG metrics reproduce baseline/owner isolation."""
from dataclasses import asdict
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest

from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.mock_target_runner import MockTargetRunner
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.contracts import PHASE_PLAN_RESOLVED, STATE_METADATA_KEY
from src.modes.performance_testing_mode.plan_state import PerfMetrics, PerfRun, PerfPlan, PerfTarget, PerfWorkloadConfig, PerformanceTestingState, SLAConfig
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.session import CreateSessionRequest
from src.schemas.tool_runtime import ModelToolCall
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_PROJECT_BASELINE") != "1",
    reason="two formal projects and isolated actual MySQL/PG/RustFS/Redis/API")]


async def test_project_baseline_and_run_owner_isolation(tmp_path):
    evidence = Path(os.getenv("QA_PERF_PROJECT_BASELINE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    # Measurements come from the prior actual JMeter run, not idealized values.
    source = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-jmeter-report-verified/normal-record.json"
    captured = json.loads(source.read_text(encoding="utf-8"))["output"]
    run = PerfRun.model_validate(captured["run"])
    metrics = PerfMetrics.model_validate(captured["metrics"])
    url = "http://127.0.0.1:19876/shared-owned-service"
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            port = free_port()
            api = processes.start("perf-project-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                projects = []
                for name in ("A", "B"):
                    response = await client.post("/projects", json={"project_key": "perf-scope-" + uuid4().hex[:10], "name": "性能项目" + name})
                    response.raise_for_status()
                    projects.append(response.json())
                a, b = (p["id"] for p in projects)
                store = PerfMetricsStore(settings)
                assert await store.initialize()
                # Explicit ownership is supplied by the production mode state.
                with bind_request_context(project_id=a):
                    assert await store.save_run(run, metrics, "pass", url, "regression", project_id=a)
                with bind_request_context(project_id=b):
                    baseline = await store.get_baseline(url, project_id=b)
                    listing = await store.list_runs(project_id=b)
                    accepted = await store.save_run(run, metrics, "fail", url, "regression", project_id=b)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY run_id")
                    rows = [dict(row) for row in cur.fetchall()]
                facts = {"scope": "actual API projects/PG store; prior actual measurements; no new engine/model/App invocation",
                    "project_a": a, "project_b": b, "target_url": url,
                    "b_baseline": baseline.model_dump() if baseline else None,
                    "b_listing": listing, "b_overwrite_accepted": accepted, "rows": rows}
                write_json(evidence / "facts.json", facts)
                assert baseline is None and not listing and accepted is False, facts
                assert rows[0]["verdict"] == "pass" and str(rows[0]["project_id"]) == a
                assert await store.save_run(run, metrics, "pass", url, "regression", project_id=a)
                assert not await store.save_run(run.model_copy(update={"plan_id": "different-plan"}), metrics, "fail", url, "regression", project_id=a)
                other = json.loads((source.parent / "sla-failed-record.json").read_text(encoding="utf-8"))["output"]
                b_run, b_metrics = PerfRun.model_validate(other["run"]), PerfMetrics.model_validate(other["metrics"])
                assert await store.save_run(b_run, b_metrics, "pass", url, "regression", project_id=b)
                # Existing successful-baseline eligibility remains unchanged;
                # assigning pass here tests repository ownership, not SLA.
                assert (await store.get_baseline(url, project_id=a)).model_dump() == metrics.model_dump()
                assert (await store.get_baseline(url, project_id=b)).model_dump() == b_metrics.model_dump()
                assert await store.get_baseline(url) is None and await store.list_runs() == []
                assert {item["run_id"] for item in await store.list_runs(project_id=a)} == {run.run_id}
                assert {item["run_id"] for item in await store.list_runs(url, project_id=b)} == {b_run.run_id}
                unknown = run.model_copy(update={"run_id": "unknown-project-run"})
                assert not await store.save_run(unknown, metrics, "pass", url, "regression", project_id=str(uuid4()))
                assert not await store.save_run(unknown, metrics, "pass", url, "regression", project_id="free-project-name")
                await processes.stop(api)
                api = processes.start("perf-project-api-restarted", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                await wait_api(client, api)
                restored = PerfMetricsStore(settings)
                assert (await restored.get_baseline(url, project_id=a)).model_dump() == metrics.model_dump()
                assert (await restored.get_baseline(url, project_id=b)).model_dump() == b_metrics.model_dump()
                racing = run.model_copy(update={"run_id": "racing-project-run"})
                accepted_race = await asyncio.gather(
                    restored.save_run(racing, metrics, "pass", url, "regression", project_id=a),
                    restored.save_run(racing, metrics, "pass", url, "regression", project_id=b))
                assert sum(accepted_race) == 1
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY run_id")
                    final = [dict(row) for row in cur.fetchall()]
                assert len(final) == 3
                race_owner = str(next(row["project_id"] for row in final if row["run_id"] == racing.run_id))
                assert race_owner == (a if accepted_race[0] else b)
                write_json(evidence / "final.json", {"rows": final, "restart_scoped_reads": True,
                    "duplicate_saved": True, "cross_project_overwrite": False, "unknown_project_saved": False,
                    "concurrent_same_id_accepted": accepted_race, "race_owner": race_owner,
                    "unscoped_baseline": None, "unscoped_listing": []})
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)


async def test_actual_k6_project_mode_baselines_and_restart(tmp_path):
    evidence = Path(os.getenv("QA_PERF_PROJECT_BASELINE_DIR", str(tmp_path / "evidence"))) / "mode"
    evidence.mkdir(parents=True, exist_ok=True)
    commands, executions, records = [], [], []
    docker = None

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
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            port = free_port()
            api = processes.start("perf-mode-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                sessions = []
                for name in ("A", "B"):
                    response = await client.post("/projects", json={"project_key": "perf-mode-" + uuid4().hex[:10], "name": "实际性能" + name})
                    response.raise_for_status()
                    project = response.json()
                    response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                    response.raise_for_status()
                    sessions.append(response.json())
                docker = Docker(settings)
                target = MockTargetRunner(settings, docker_resources=docker)
                try:
                    with bind_request_context(project_id=sessions[0]["project_id"], session_id=sessions[0]["id"]):
                        started = await target.start(port=free_port(), response_text="owned project baseline")
                    assert started.ok
                    code, address, stderr = await docker._command(["inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", started.stdout], timeout=20)
                    assert code == 0 and address.strip(), stderr
                    url = f"http://{address.strip()}:5678/"
                    runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                    adapter = K6EngineAdapter(image="sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec")
                    jobs = ToolJobService(PostgresToolJobStore(settings))
                    await jobs.initialize()
                    tools = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
                    mode = tools._performance_testing_mode_runtime
                    mode._runner = runner
                    mode._create_engine_adapter = lambda engine: adapter
                    descriptor = ToolDescriptor(key="performance-test-runner", name="performance", description="owned project baseline", category="test")
                    for index, session in enumerate((sessions[0], sessions[1], sessions[0])):
                        plan = PerfPlan(plan_id="project-plan-" + uuid4().hex[:8], run_intent="probe" if index == 0 else "regression",
                            targets=[PerfTarget(name="shared-target", url=url)],
                            workload=PerfWorkloadConfig(model="closed", mode="constant_vus", virtual_users=1, hold_seconds=2, think_time_ms=50),
                            sla=SLAConfig(p99_ms=1000, error_rate=0.01))
                        ctx = ToolExecutionContext(session_id=session["id"], turn_id=f"baseline-{index}", trace_id=f"baseline-{index}",
                            user_message="owned project baseline check", normalized_input="performance", context_bundle={
                                "project_id": session["project_id"], STATE_METADATA_KEY: PerformanceTestingState(
                                    session_id=session["id"], phase=PHASE_PLAN_RESOLVED, plan=plan).model_dump()})
                        record = await tools.execute(descriptor, ModelToolCall(id=str(uuid4()), name=descriptor.key, arguments={}), ctx)
                        records.append((session, record))
                        write_json(evidence / f"record-{index}.json", record.model_dump(mode="json"))
                        owned_objects.extend(a["path"] for a in record.output.get("artifacts", []) if a.get("path", "").startswith("rustfs://"))
                        assert record.status == "completed" and record.output["metrics_persistence"]["status"] == "saved"
                        assert record.output["project_id"] == session["project_id"]
                        assert record.output["smoke_result"]["passed"] and record.output["request_validation"]["status"] == "passed"
                        if index < 2:
                            assert record.output["baseline_comparison"] is None
                            assert record.output["verdict"] == ("baseline" if index == 0 else "pass")
                        else:
                            old_p95 = records[0][1].output["metrics"]["p95_ms"]
                            expected = round((record.output["metrics"]["p95_ms"] - old_p95) / old_p95 * 100, 2)
                            assert record.output["baseline_comparison"]["p95_delta_pct"] == expected
                    assert len(executions) == 6
                    await processes.stop(api)
                    api = processes.start("perf-mode-api-restarted", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                    await wait_api(client, api)
                    downloads = []
                    for session, record in records:
                        assert (await tools.record_from_job(await jobs.get_job(record.job_id))).output == record.output
                        response = await client.get(f"/sessions/{session['id']}/tool-jobs/{record.job_id}")
                        response.raise_for_status()
                        detail = response.json()
                        assert detail["output_payload"]["project_id"] == session["project_id"]
                        for artifact in detail["artifacts"]:
                            response = await client.get(f"/sessions/{session['id']}/artifacts/{artifact['id']}/content")
                            assert response.status_code == 200
                            content = response.content
                            meta = artifact["metadata"]
                            if meta.get("sha256"):
                                assert hashlib.sha256(content).hexdigest() == meta["sha256"] and len(content) == meta["size_bytes"]
                            path = evidence / "downloads" / artifact["id"] / Path(meta.get("filename") or "artifact").name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(content)
                            downloads.append({"path": path.relative_to(evidence).as_posix(), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
                    assert len(executions) == 6
                    a, b = (session["project_id"] for session in sessions)
                    store = PerfMetricsStore(settings)
                    assert len(await store.list_runs(project_id=a)) == 2
                    assert len(await store.list_runs(project_id=b)) == 1
                    assert (await store.get_baseline(url, project_id=b)).model_dump() == records[1][1].output["metrics"]
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY created_at")
                        rows = [dict(row) for row in cur.fetchall()]
                    assert [str(row["project_id"]) for row in rows] == [a, b, a]
                    write_json(evidence / "postgres-metrics.json", rows)
                    write_json(evidence / "downloads.json", downloads)
                    write_json(evidence / "facts.json", {"scope": "actual k6/production guard, ToolRuntime/PG/RustFS/API restart; fixed plans/cached image; no Agent/model/App",
                        "target_url": url, "project_a": a, "project_b": b, "engine_runs": len(executions), "replay_added_engines": 0,
                        "b_comparison": records[1][1].output["baseline_comparison"], "a_comparison": records[2][1].output["baseline_comparison"],
                        "download_count": len(downloads)})
                finally:
                    await target.close()
                    for index in range(7):
                        worker = processes.start(f"perf-cleanup-{index}", "-m", "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "perf-project-cleanup")
                        assert await processes.wait(worker) == 0
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_cleanup_table}")
                        receipts = [dict(row) for row in cur.fetchall()]
                    write_json(evidence / "postgres-cleanup.json", receipts)
                    assert len(receipts) == 7 and all(row["status"] == "completed" for row in receipts)
    finally:
        if docker:
            for command in list(commands):
                if command["args"][0] == "create" and command["exit_code"] == 0:
                    code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + command["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                    assert code == 0 and not stdout.strip(), stderr
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)


async def test_existing_perf_table_additive_project_migration(tmp_path):
    evidence = Path(os.getenv("QA_PERF_PROJECT_BASELINE_DIR", str(tmp_path / "evidence"))) / "migration"
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            port = free_port()
            api = processes.start("perf-migration-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "perf-migration-" + uuid4().hex[:10], "name": "迁移项目"})
                response.raise_for_status()
                project = response.json()["id"]
                table = settings.database.postgres_perf_runs_table
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute("SELECT to_regclass(%s) AS name", (table,))
                    assert cur.fetchone()["name"] is None
                    # Exact old shape read from 1ae7af26's store DDL; no DROP.
                    cur.execute(f"""CREATE TABLE {table} (
                        run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                        engine TEXT NOT NULL DEFAULT 'k6', backend TEXT NOT NULL DEFAULT 'docker',
                        status TEXT NOT NULL DEFAULT 'pending', run_intent TEXT NOT NULL DEFAULT 'probe',
                        target_url TEXT, metrics_json JSONB, verdict TEXT,
                        started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW())""")
                    cur.execute(f"INSERT INTO {table}(run_id,plan_id,status,target_url,metrics_json,verdict,completed_at) "
                                "VALUES('legacy','legacy-plan','completed','http://owned/legacy',%s::jsonb,'pass',NOW())",
                                (json.dumps({"samples": 87, "p95_ms": 6, "historical_custom": {"false": False, "zero": 0, "null": None}}),))
                    cur.execute(f"SELECT * FROM {table}")
                    before = dict(cur.fetchone())
                store = PerfMetricsStore(settings)
                assert await store.initialize() and await PerfMetricsStore(settings).initialize()
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {table}")
                    after = dict(cur.fetchone())
                    cur.execute("SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_name=%s", (table,))
                    columns = [dict(row) for row in cur.fetchall()]
                assert after.pop("project_id") is None and after == before
                assert {"column_name": "project_id", "data_type": "uuid", "is_nullable": "YES"} in columns
                assert await store.get_baseline("http://owned/legacy", project_id=project) is None
                assert await store.list_runs(project_id=project) == []
                assert not await store.save_run(PerfRun(run_id="legacy", plan_id="legacy-plan", status="completed"),
                    PerfMetrics(samples=1, p95_ms=1), "pass", "http://owned/legacy", project_id=project)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {table}")
                    final = dict(cur.fetchone())
                assert final.pop("project_id") is None and final == before
                write_json(evidence / "facts.json", {"legacy_before": before, "legacy_after": final,
                    "columns": columns, "repeated_initialize": True, "adopt_legacy": False,
                    "scoped_baseline": None, "scoped_history": []})
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

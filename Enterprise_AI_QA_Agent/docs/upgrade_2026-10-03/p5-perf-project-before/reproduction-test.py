"""Two actual projects and PG metrics reproduce baseline/owner isolation."""
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest

from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.plan_state import PerfMetrics, PerfRun
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
                # Before-fix methods had no project_id argument. Reproduction
                # uses the same bound context as the production mode runtime.
                with bind_request_context(project_id=a):
                    assert await store.save_run(run, metrics, "pass", url, "regression")
                with bind_request_context(project_id=b):
                    baseline = await store.get_baseline(url)
                    listing = await store.list_runs()
                    accepted = await store.save_run(run, metrics, "fail", url, "regression")
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {settings.database.postgres_perf_runs_table} ORDER BY run_id")
                    rows = [dict(row) for row in cur.fetchall()]
                facts = {"scope": "actual API projects/PG store; prior actual measurements; no new engine/model/App invocation",
                    "project_a": a, "project_b": b, "target_url": url,
                    "b_baseline": baseline.model_dump() if baseline else None,
                    "b_listing": listing, "b_overwrite_accepted": accepted, "rows": rows}
                write_json(evidence / "facts.json", facts)
                assert baseline is None and not listing and accepted is False, facts
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

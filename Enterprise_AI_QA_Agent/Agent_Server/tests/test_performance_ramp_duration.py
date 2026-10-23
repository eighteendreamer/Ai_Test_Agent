"""Regression for captured zero-duration and truncated default k6 ramps."""
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Thread
from uuid import uuid4

import pytest

from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.modes.performance_testing_mode.plan_state import PerfLimits, PerfPlan, PerfRun, PerfTarget, PerfWorkloadConfig, RampStage, SLAConfig
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.workload_modeler import WorkloadModeler, build_default_ramp_stages


def milliseconds(duration):
    value, unit = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s)", duration).groups()
    return Decimal(value) * (1000 if unit == "s" else 1)


@pytest.mark.parametrize("seconds", [1, 2, 3, 5, 10, 60, 300])
def test_default_stages_preserve_short_duration_and_remainder(seconds):
    stages = build_default_ramp_stages(4, seconds)
    assert [stage.target for stage in stages] == [2, 4, 4]
    assert all(milliseconds(stage.duration) > 0 for stage in stages)
    assert sum(milliseconds(stage.duration) for stage in stages) == seconds * 1000
    modeled = WorkloadModeler().from_api_docs([{"path": "/owned"}], total_rate_rps=4, duration_seconds=seconds)
    assert modeled.workload_config.ramp_stages == stages


@pytest.mark.parametrize("seconds", [0, -1])
def test_default_ramp_rejects_nonpositive_total_duration(seconds):
    with pytest.raises(ValueError, match="持续时长必须大于0"):
        build_default_ramp_stages(4, seconds)


@pytest.mark.parametrize(("model", "mode"), [("open", "ramping_arrival_rate"), ("closed", "ramping_vus")])
def test_explicit_ramp_is_not_regenerated(model, mode):
    stages = [RampStage(target=0, duration="0s"), RampStage(target=2, duration="2s")]
    plan = PerfPlan(workload=PerfWorkloadConfig(model=model, mode=mode, hold_seconds=9, ramp_stages=stages))
    script = K6EngineAdapter().build_script(plan).script_content
    assert '{ target: 0, duration: "0s" }' in script
    assert '{ target: 2, duration: "2s" }' in script
    assert plan.workload.ramp_stages == stages


@contextmanager
def owned_target():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append({"method": self.command, "path": self.path})
            body = b'{"owned":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://host.docker.internal:{server.server_port}/owned-ramp", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_RAMP") != "1", reason="requires cached k6 image and owned local HTTP target")
@pytest.mark.parametrize("source", ["open-default", "closed-default", "api-doc-model"])
@pytest.mark.parametrize("seconds", [2, 5])
def test_actual_k6_ramp_duration_requests_metrics_and_report(tmp_path, source, seconds):
    evidence = Path(os.getenv("QA_PERF_RAMP_DIR", str(tmp_path / "evidence"))) / f"{source}-{seconds}s"
    evidence.mkdir(parents=True, exist_ok=False)
    commands, names = [], []
    def docker(*args, timeout=30):
        completed = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        commands.append({"args": args, "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
        (evidence / "docker.json").write_text(json.dumps(commands, indent=2), encoding="utf-8")
        return completed
    try:
        image = docker("image", "inspect", "grafana/k6:latest", "--format", "{{.Id}}")
        assert image.returncode == 0 and image.stdout.strip().startswith("sha256:"), image.stderr
        with owned_target() as (url, observed):
            if source == "api-doc-model":
                workload = WorkloadModeler().from_api_docs([{"path": "/owned-ramp"}], total_rate_rps=4,
                    duration_seconds=seconds).workload_config
                workload.virtual_users = 2
            else:
                workload = PerfWorkloadConfig(model="closed" if source == "closed-default" else "open",
                    mode="ramping_vus" if source == "closed-default" else "ramping_arrival_rate",
                    target_rate_rps=4, virtual_users=2, hold_seconds=seconds)
            workload.think_time_ms = 50
            plan = PerfPlan(plan_id="owned-ramp-" + uuid4().hex[:12], run_intent="regression",
                targets=[PerfTarget(name="owned-http", url=url)], workload=workload,
                limits=PerfLimits(max_vus=2), sla=SLAConfig(p99_ms=10000, error_rate=0.1))
            original = plan.model_dump(mode="json")
            script = K6EngineAdapter(rewrite_localhost=False).build_script(plan)
            (evidence / "script.js").write_text(script.script_content, encoding="utf-8")
            def launch(*args, offline=False):
                name = "qa-perf-ramp-" + uuid4().hex[:12]
                names.append(name)
                return docker("run", "--rm", "--name", name, *(["--network", "none"] if offline else []),
                    "-v", str(evidence.resolve()) + ":/work", image.stdout.strip(), *args)
            inspected = launch("inspect", "--execution-requirements", "/work/script.js", offline=True)
            assert inspected.returncode == 0, inspected.stderr
            requirements = json.loads(inspected.stdout)
            scenario = next(iter(requirements["scenarios"].values()))
            assert sum(milliseconds(stage["duration"]) for stage in scenario["stages"]) == seconds * 1000
            assert all(milliseconds(stage["duration"]) > 0 for stage in scenario["stages"])
            executed = launch("run", "--summary-export", "/work/summary.json", "/work/script.js")
            assert executed.returncode == 0, executed.stderr
            assert "No script iterations fully finished" not in executed.stderr
            raw = K6EngineAdapter().parse_results((evidence / "summary.json").read_text(encoding="utf-8"))
            parsed = PerfResultParser().parse(raw)
            report = PerfReportBuilder().build(parsed, plan, PerfRun(run_id=plan.plan_id,
                status="completed", engine_thresholds=raw.thresholds))
            assert raw.samples == len(observed) and raw.samples > 0
            assert all(item == {"method": "GET", "path": "/owned-ramp"} for item in observed)
            assert raw.error_rate == 0
            assert all(value is not None for value in (raw.throughput_tps, raw.p50_ms, raw.p95_ms, raw.p99_ms))
            assert report.verdict == "pass", report.report_markdown
            assert plan.model_dump(mode="json") == original
            (evidence / "verification.json").write_text(json.dumps({"source": source, "requested_seconds": seconds,
                "plan": original, "scenario": scenario, "observed_requests": observed,
                "report": report.model_dump(mode="json"), "default_ai_invoked": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for name in names:
            check = docker("ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}")
            if check.stdout.strip():
                removed = docker("rm", "--force", name)
                assert removed.returncode == 0, removed.stderr
            assert check.returncode == 0, check.stderr
        (evidence / "cleanup.json").write_text(json.dumps({"container_names": names, "all_runs_returned": True}, indent=2), encoding="utf-8")

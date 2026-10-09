"""Actual cached JMeter, owned HTTP, and production persistence/cleanup."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest

from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.perf_runner_service import PerfRunnerService
from src.core.request_context import bind_request_context
from src.runtime.docker_resource_manager import DockerResourceManager
from src.modes.performance_testing_mode.plan_state import Correlation, PerfPlan, PerfTarget, PerfWorkloadConfig, SmokeConfig
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_correlation_live import Target
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_JMETER_RESPONSE") != "1",
    reason="cached JMeter Docker image and isolated MySQL/PG/RustFS/Redis")]


async def test_actual_jmeter_smoke_status_and_response_chain(tmp_path):
    evidence = Path(os.getenv("QA_JMETER_RESPONSE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    target = Target()
    port = target.start()
    commands, observations = [], []

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
            api = processes.start("jmeter-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "jmeter-response-" + uuid4().hex[:10], "name": "JMeter真实响应验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                adapter = JMeterEngineAdapter(image="sha256:088ac52b759a198a5afa5ae13d0a6306e9f2017d71ad140ff57427f6930406f7")
                base = f"http://127.0.0.1:{port}"
                with bind_request_context(project_id=project["id"], session_id=session["id"], trace_id="actual-jmeter-response"):
                    for variant, path, expected in (("normal", "/plain", True), ("wrong-status", "/plain", False),
                        ("negative", "/profile", True), ("missing", "/missing", False), ("chain", "/login", True)):
                        runner.variant = variant
                        targets = [PerfTarget(name="login", url=base + path)]
                        correlations = []
                        if variant in {"chain", "missing"}:
                            targets.append(PerfTarget(name="profile", url=base + "/profile"))
                            correlations = [Correlation(extract="ticket", from_path="session.credential", into_header="Authorization")]
                        plan = PerfPlan(engine="jmeter", targets=targets, correlations=correlations,
                            smoke=SmokeConfig(vus=1, iterations=3, expect_status=[201] if variant == "wrong-status" else [401] if variant == "negative" else [200],
                                expect_extract=["ticket"] if correlations else []),
                            workload=PerfWorkloadConfig(model="closed", virtual_users=3, hold_seconds=1, think_time_ms=100))
                        script = adapter.build_script(plan)
                        (evidence / (variant + ".jmx")).write_text(script.script_content, encoding="utf-8")
                        write_json(evidence / (variant + "-plan.json"), plan.model_dump(mode="json"))
                        prior = len(target.events)
                        smoke = await runner.run_smoke(plan, script, adapter)
                        owned_objects.extend(a["path"] for a in smoke.evidence.get("artifacts", []))
                        write_json(evidence / (variant + "-smoke.json"), smoke.model_dump(mode="json"))
                        original_dir = evidence / (variant + "-originals")
                        original_dir.mkdir()
                        downloads = []
                        for artifact in smoke.evidence.get("artifacts", []):
                            original = await objects.read_object_uri(artifact["path"])
                            content = original["content"]
                            assert all(value.encode() not in content for value in target.credentials), artifact["relative_path"]
                            assert hashlib.sha256(content).hexdigest() == artifact["sha256"] and len(content) == artifact["size_bytes"]
                            saved = original_dir / artifact["relative_path"]
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            saved.write_bytes(content)
                            downloads.append({"relative_path": saved.relative_to(evidence).as_posix(), "sha256": artifact["sha256"], "bytes": len(content)})
                        write_json(evidence / (variant + "-downloads.json"), downloads)
                        events = target.events[prior:]
                        observations.append({"variant": variant, "passed": smoke.passed, "expected": expected,
                            "exit_code": runner.last.exit_code, "events": events, "request_count": len(events)})
                        write_json(evidence / "observations.json", observations)
                        child = processes.start("jmeter-cleanup-" + variant, "-m", "src.cli.run_resource_cleanup_worker", "--once", "--worker-id", "jmeter-response-cleanup")
                        assert await processes.wait(child) == 0
                    assert [o["passed"] for o in observations] == [o["expected"] for o in observations], observations
                    assert [o["request_count"] for o in observations] == [3, 3, 3, 3, 6], observations
                    assert all(e["path"] == "/missing" for e in observations[3]["events"])
                    events = observations[-1]["events"]
                    assert [e["path"] for e in events] == ["/login", "/profile"] * 3
                    for source, consumer in zip(events[::2], events[1::2]):
                        assert consumer["authorization_accepted"] and consumer["credential_sha256"] == source["issued_sha256"]
                    write_json(evidence / "facts.json", {"scope": "actual JMeter/owned HTTP/production runner and objects; no Agent/model",
                        "project_id": project["id"], "session_id": session["id"], "observations": observations})
    finally:
        await target.close()
        write_json(evidence / "target-events.json", target.events)
        for record in list(commands):
            if record["args"][0] == "create" and record["exit_code"] == 0:
                code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + record["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                assert code == 0 and not stdout.strip(), stderr
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

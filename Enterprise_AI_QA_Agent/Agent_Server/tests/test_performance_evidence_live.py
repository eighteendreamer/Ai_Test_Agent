"""Actual k6 outputs exercise smoke failure and durable evidence contracts."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import httpx
import pytest

from src.application.performance.engine_adapter import EngineCommand, RunOptions, ScriptArtifact
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.mock_target_runner import MockTargetRunner
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfTarget, PerfWorkloadConfig, PerformanceTestingState, SLAConfig
from src.modes.performance_testing_mode.contracts import PHASE_ANALYZED, PHASE_GUARD_PASSED, STATE_METADATA_KEY
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import CreateSessionRequest
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from test_performance_docker_measurement import write_json

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_EVIDENCE") != "1",
    reason="actual k6, independent API and isolated MySQL/PG/RustFS/Redis")]


async def test_actual_smoke_evidence_and_nonzero_exit(tmp_path):
    await actual_performance_evidence(tmp_path)


async def actual_performance_evidence(tmp_path, report_probe=None):
    evidence = Path(os.getenv("QA_PERF_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    commands, observations, records = [], [], []

    class Docker(DockerResourceManager):
        async def _command(self, args, *, timeout):
            result = await super()._command(args, timeout=timeout)
            commands.append({"args": args, "exit_code": result[0], "stdout": result[1], "stderr": result[2]})
            write_json(evidence / "docker-commands.json", commands)
            return result

    class Runner(PerfRunnerService):
        count = 0
        last = None

        async def _run_in_docker(self, cmd, script, name, timeout):
            result = await super()._run_in_docker(cmd, script, name, timeout)
            self.count += 1
            self.last = result
            prefix = script.filename + "-" + str(self.count)
            write_json(evidence / (prefix + "-execution.json"), asdict(result))
            (evidence / (prefix + "-summary.json")).write_text(result.summary_json, encoding="utf-8")
            return result

    class Adapter(K6EngineAdapter):
        variant = "normal"

        def build_script(self, plan):
            script = super().build_script(plan)
            script.filename = self.variant + ".js"
            if self.variant == "malformed":
                script.script_content += '\nexport function handleSummary() { return {"/work/summary.json": "{broken summary"}; }\n'
            elif self.variant == "teardown":
                script.script_content += '\nexport function teardown() { throw new Error("controlled teardown failure"); }\n'
            (evidence / script.filename).write_text(script.script_content, encoding="utf-8")
            return script

        def run_command(self, script, opts):
            cmd = super().run_command(script, opts)
            if self.variant == "missing":
                # Discard the export in this owned container, producing real exit0/no file.
                cmd.command[3] = "/dev/null"
            elif self.variant == "malformed":
                cmd.command[2:4] = []
            return cmd

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned_objects):
            settings.orchestration.performance_runner_backend = "docker"
            api_port = free_port()
            api = processes.start("perf-evidence-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)
                response = await client.post("/projects", json={"project_key": "perf-evidence-" + uuid4().hex[:10], "name": "性能原件验收"})
                response.raise_for_status()
                project = response.json()
                response = await client.post("/sessions", json=CreateSessionRequest(project_id=project["id"], mode_key="performance_testing").model_dump(mode="json"))
                response.raise_for_status()
                session = response.json()
                docker = Docker(settings)
                target = MockTargetRunner(settings, docker_resources=docker)
                try:
                    with bind_request_context(project_id=project["id"], session_id=session["id"], turn_id="perf-evidence-turn"):
                        started = await target.start(port=free_port(), response_text="owned evidence target")
                        assert started.ok, started
                        code, address, stderr = await docker._command(["inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", started.stdout], timeout=20)
                        assert code == 0 and address.strip(), stderr
                        plan = PerfPlan(run_intent="regression", targets=[PerfTarget(name="owned-target", url=f"http://{address.strip()}:5678/")],
                            workload=PerfWorkloadConfig(model="closed", mode="constant_vus", virtual_users=1, hold_seconds=2, think_time_ms=50), sla=SLAConfig(p99_ms=1000))
                        adapter = Adapter(image="sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec")
                        runner = Runner(settings, docker_resources=docker, artifact_storage_service=objects)
                        jobs = ToolJobService(PostgresToolJobStore(settings))
                        await jobs.initialize()
                        tools = ToolRuntimeService(settings=settings, tool_job_service=jobs, artifact_storage_service=objects)
                        mode = tools._performance_testing_mode_runtime
                        assert mode._runner._objects is objects
                        mode._runner = runner
                        mode._create_engine_adapter = lambda engine: adapter
                        tool = ToolDescriptor(key="performance-test-runner", name="performance", description="owned actual performance tool", category="test")
                        for variant in ("normal", "missing", "malformed", "teardown"):
                            adapter.variant = variant
                            context = ToolExecutionContext(session_id=session["id"], turn_id="perf-" + variant,
                                trace_id="perf-trace-" + variant, user_message="controlled evidence validation", normalized_input="performance",
                                context_bundle={"project_id": project["id"], STATE_METADATA_KEY: PerformanceTestingState(
                                    session_id=session["id"], phase=PHASE_GUARD_PASSED, plan=plan).model_dump()})
                            prior = runner.count
                            record = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments={}), context)
                            records.append(record)
                            write_json(evidence / (variant + "-tool-record.json"), record.model_dump(mode="json"))
                            smoke = record.output["smoke_result"]
                            assert runner.count == prior + (2 if variant == "normal" else 1)
                            observations.append({"variant": variant, "passed": smoke["passed"], "detail": smoke["detail"],
                                "tool_job_id": record.job_id, "tool_status": record.status, "engine_runs": runner.count - prior})
                            assert smoke["evidence"]["artifacts"]
                            for artifact in record.output["artifacts"]:
                                if artifact.get("evidence_schema") != "performance-execution-v1":
                                    continue
                                owned_objects.append(artifact["path"])
                                stored = await objects.read_object_uri(artifact["path"])
                                assert len(stored["content"]) == artifact["size_bytes"]
                                assert hashlib.sha256(stored["content"]).hexdigest() == artifact["sha256"]
                                assert artifact["owner"]["project_id"] == project["id"] and artifact["owner"]["session_id"] == session["id"]
                                assert artifact["owner"]["trace_id"] == record.trace_id
                            if variant == "normal":
                                assert record.status == "completed" and record.output["verdict"] == "pass"
                                assert record.output["run"]["smoke_result"] == smoke
                                assert len({a["execution_id"] for a in record.output["artifacts"] if a.get("execution_id")}) == 2
                            else:
                                assert record.status == "failed" and "run" not in record.output
                            if report_probe is not None:
                                await report_probe(stage=variant, client=client, session=session, record=record,
                                    records=records, evidence=evidence, api_port=api_port, runner=runner, jobs=jobs, tool=tool)
                        assert [x["passed"] for x in observations] == [True, False, False, False], observations

                        # Valid samples from a real failed engine cannot be promoted to pass.
                        adapter.variant = "teardown"
                        script = adapter.build_script(plan)
                        run = await runner.execute(plan, script, RunOptions(timeout_seconds=30), adapter)
                        owned_objects.extend(a["path"] for a in run.evidence["artifacts"])
                        report = PerfReportBuilder().build(PerfResultParser().parse(adapter.parse_results(runner.last.summary_json)), plan, run)
                        write_json(evidence / "teardown-load-run.json", run.model_dump(mode="json"))
                        write_json(evidence / "teardown-load-report.json", report.model_dump(mode="json"))
                        assert run.exit_code == 107 and report.sla_result.passed is True and report.verdict == "inconclusive"
                        async def publish_actual_report(stage, actual_run, actual_report):
                            # Reuse the production analyzed checkpoint; this saves the
                            # actual run's report without calling an engine again.
                            prior = runner.count
                            state = PerformanceTestingState(session_id=session["id"], project_id=project["id"],
                                phase=PHASE_ANALYZED, plan=plan, run=actual_run, report=actual_report)
                            context = ToolExecutionContext(session_id=session["id"], turn_id=stage, trace_id=stage,
                                user_message="persist actual performance report", normalized_input="performance report",
                                context_bundle={"project_id": project["id"], STATE_METADATA_KEY: state.model_dump(mode="json")})
                            saved = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments={}), context)
                            assert runner.count == prior and saved.output["verdict"] == "inconclusive"
                            assert saved.output["metrics_persistence"]["status"] == "saved"
                            records.append(saved)
                            write_json(evidence / (stage + "-tool-record.json"), saved.model_dump(mode="json"))
                            await report_probe(stage=stage, client=client, session=session, record=saved,
                                records=records, evidence=evidence, api_port=api_port, runner=runner, jobs=jobs, tool=tool)
                        if report_probe is not None:
                            await publish_actual_report("load-inconclusive", run, report)

                        # Actual local subprocess timeout must retain already-emitted streams.
                        local_script = ScriptArtifact(engine="k6", filename="local.py", script_content='import sys,time\nprint("captured stdout",flush=True)\nprint("captured stderr",file=sys.stderr,flush=True)\ntime.sleep(5)\n')
                        local = await runner._run_local(EngineCommand(image="", command=[sys.executable, "-u", "/work/local.py"]), local_script, 1)
                        owned_objects.extend(a["path"] for a in local.evidence["artifacts"])
                        write_json(evidence / "local-timeout-execution.json", asdict(local))
                        assert local.timed_out and "captured stdout" in local.stdout and "captured stderr" in local.stderr
                        assert local.evidence["status"] == "partial" and local.evidence["missing_artifacts"] == ["engine_summary"]

                        # Real first RustFS upload, then an explicitly injected upload failure.
                        # Do not interrupt the shared object-storage service or repeat workload.
                        class InterruptedUploads:
                            enabled = True
                            calls = 0

                            async def store_uploaded_file(self, **kwargs):
                                self.calls += 1
                                if self.calls == 2:
                                    raise RuntimeError("controlled upload interruption after one saved original")
                                return await objects.store_uploaded_file(**kwargs)

                        interrupted = InterruptedUploads()
                        adapter.variant = "normal"
                        runner._objects = interrupted
                        prior = runner.count
                        try:
                            partial = await runner.execute(plan, adapter.build_script(plan), RunOptions(timeout_seconds=30), adapter)
                        finally:
                            runner._objects = objects
                        owned_objects.extend(a["path"] for a in partial.evidence["artifacts"])
                        partial_report = PerfReportBuilder().build(PerfResultParser().parse(adapter.parse_results(runner.last.summary_json)), plan, partial)
                        write_json(evidence / "partial-storage-run.json", partial.model_dump(mode="json"))
                        write_json(evidence / "partial-storage-report.json", partial_report.model_dump(mode="json"))
                        assert interrupted.calls == 2 and runner.count == prior + 1
                        assert partial.status == "completed" and partial.evidence["status"] == "partial"
                        assert len(partial.evidence["artifacts"]) == 1 and partial_report.verdict == "inconclusive"
                        saved = partial.evidence["artifacts"][0]
                        original = await objects.read_object_uri(saved["path"])
                        assert hashlib.sha256(original["content"]).hexdigest() == saved["sha256"]
                        if report_probe is not None:
                            await publish_actual_report("storage-inconclusive", partial, partial_report)

                        before_replay = runner.count
                        replayed = await tools.record_from_job(await jobs.get_job(records[0].job_id))
                        assert replayed.output == records[0].output and runner.count == before_replay

                        # Independent production API restart must preserve PG references and RustFS bytes.
                        if report_probe is not None:
                            await report_probe(stage="restarting", client=client, session=session, record=records[-1],
                                records=records, evidence=evidence, api_port=api_port, runner=runner, jobs=jobs, tool=tool)
                        await processes.stop(api)
                        api = processes.start("perf-evidence-api-restarted", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
                        await wait_api(client, api)
                        downloads = []
                        for record in records:
                            response = await client.get(f"/sessions/{session['id']}/tool-jobs/{record.job_id}")
                            response.raise_for_status()
                            detail = response.json()
                            # _record_output adds presentation links after business output is saved.
                            assert detail["output_payload"] == {
                                key: value for key, value in record.output.items()
                                if key not in {"download_urls", "download_markdown"}
                            }
                            expected_links = [{"artifact_id": artifact["id"],
                                "label": artifact["label"] or artifact["path"],
                                "artifact_type": artifact["artifact_type"],
                                "url": f"/api/v1/sessions/{session['id']}/artifacts/{artifact['id']}/content"}
                                for artifact in detail["artifacts"]]
                            assert record.output["download_urls"] == expected_links
                            assert record.output["download_markdown"] == "\n".join(
                                f"[点击下载 {link['label']}]({link['url']})" for link in expected_links)
                            for artifact in detail["artifacts"]:
                                if artifact["metadata"].get("evidence_schema") != "performance-execution-v1":
                                    continue
                                url = f"/sessions/{session['id']}/artifacts/{artifact['id']}/content"
                                response = await client.get(url)
                                assert response.status_code == 200, response.text
                                digest = hashlib.sha256(response.content).hexdigest()
                                assert digest == artifact["metadata"]["sha256"] and len(response.content) == artifact["metadata"]["size_bytes"]
                                original_path = evidence / "download-originals" / artifact["id"] / Path(artifact["metadata"]["filename"]).name
                                original_path.parent.mkdir(parents=True, exist_ok=True)
                                original_path.write_bytes(response.content)
                                downloads.append({"artifact_id": artifact["id"], "uri": artifact["path"], "sha256": digest,
                                    "bytes": len(response.content), "url": url, "saved_relative_path": original_path.relative_to(evidence).as_posix()})
                        write_json(evidence / "downloads.json", downloads)
                        response = await client.post("/projects", json={"project_key": "perf-other-" + uuid4().hex[:10], "name": "隔离项目"})
                        response.raise_for_status()
                        other = response.json()
                        response = await client.post("/sessions", json=CreateSessionRequest(project_id=other["id"]).model_dump(mode="json"))
                        response.raise_for_status()
                        other_session = response.json()
                        response = await client.get(f"/sessions/{other_session['id']}/artifacts/{downloads[0]['artifact_id']}/content")
                        assert response.status_code == 404

                        target_artifact = downloads[0]
                        original = await objects.read_object_uri(target_artifact["uri"])
                        damaged = bytes([original["content"][0] ^ 1]) + original["content"][1:]
                        await objects.store_uploaded_bytes(content=damaged, filename="owned-integrity-probe", object_prefix="",
                            bucket_name=original["bucket"], object_name=original["object_name"], content_type=original["content_type"])
                        response = await client.get(target_artifact["url"])
                        assert response.status_code == 409, response.text
                        await objects.store_uploaded_bytes(content=original["content"], filename="owned-integrity-restored", object_prefix="",
                            bucket_name=original["bucket"], object_name=original["object_name"], content_type=original["content_type"])
                        response = await client.get(target_artifact["url"])
                        assert response.status_code == 200 and response.content == original["content"]
                        if report_probe is not None:
                            await report_probe(stage="restarted", client=client, session=session, record=records[-1],
                                records=records, evidence=evidence, api_port=api_port, runner=runner, jobs=jobs, tool=tool)
                        write_json(evidence / "facts.json", {"project_id": project["id"], "session_id": session["id"],
                            "scope": "actual engine/tool/PG/RustFS/API restart and download; starts at previously validated guard phase; no Agent/model invocation",
                            "observations": observations, "api_restart_persisted": True, "cross_project_download_status": 404,
                            "tampered_same_size_status": 409, "restored_bytes_equal": True, "replay_added_engine_runs": runner.count - before_replay,
                            "storage_fault_scope": "injected second upload failure after real first RustFS upload",
                            "storage_fault_engine_runs": 1, "storage_fault_preserved_originals": 1,
                            "storage_fault_verdict": partial_report.verdict})
                finally:
                    await target.close()
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_resource_allocation_table}")
                        write_json(evidence / "postgres-allocations.json", [dict(row) for row in cur.fetchall()])
                    for record in commands:
                        if record["args"][0] == "create" and record["exit_code"] == 0:
                            code, stdout, stderr = await docker._command(["ps", "--all", "--no-trunc", "--filter", "id=" + record["stdout"].strip(), "--format", "{{.ID}}"], timeout=20)
                            assert code == 0 and not stdout.strip(), stderr
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)

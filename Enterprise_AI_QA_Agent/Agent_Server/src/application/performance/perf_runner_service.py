"""Performance runner service.

Manages backend detection (local/docker), container lifecycle,
smoke validation, concurrency gating, and execution orchestration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.resource_lease_manager import ResourceUnavailable
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfRun, SmokeResult
from .engine_adapter import EngineCommand, PerfEngineAdapter, RawMetrics, RunOptions, ScriptArtifact
from .engine_adapter import metric_number
from .evidence import store_execution_evidence
from .request_validation import request_validation

LOGGER = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    summary_json: str = ""
    timed_out: bool = False
    container_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


class PerfRunnerService:
    """Orchestrates performance test execution with backend detection and safety gates."""

    def __init__(self, settings: Settings, *, docker_resources=None, artifact_storage_service=None):
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.orchestration.performance_max_concurrent_runs)
        self._container_prefix = settings.orchestration.performance_runner_docker_container_prefix
        self._workdir = settings.orchestration.performance_runner_docker_workdir
        self._docker_resources = docker_resources or DockerResourceManager(settings)
        self._objects = artifact_storage_service

    def detect_backend(self, engine_key: str = "k6") -> str:
        configured = self._settings.orchestration.performance_runner_backend
        if configured != "auto":
            return configured

        executable = "jmeter" if engine_key == "jmeter" else "k6"
        if shutil.which(executable):
            return "local"
        if shutil.which("docker"):
            return "docker"
        raise RuntimeError(
            "无法探测到可用的压测后端。\n"
            "以下任一工具：\n"
            "  - k6: https://grafana.com/docs/k6/latest/set-up/install-k6/\n"
            "  - Docker: https://docs.docker.com/get-docker/\n"
            "或在配置中设置 PERFORMANCE_RUNNER_BACKEND=local|docker"
        )

    async def execute(
        self,
        plan: PerfPlan,
        script: ScriptArtifact,
        run_opts: RunOptions,
        engine_adapter: PerfEngineAdapter,
    ) -> PerfRun:
        backend = self.detect_backend(plan.engine)
        run_id = f"perf-run-{uuid.uuid4().hex[:8]}"
        container_name = f"{self._container_prefix}-{run_id}"

        run = PerfRun(
            run_id=run_id,
            plan_id=plan.plan_id,
            engine=plan.engine,
            backend=backend,
            container_or_cluster=container_name if backend == "docker" else "local",
            status="running",
            started_at=datetime.utcnow().isoformat(),
        )

        async with self._semaphore:
            try:
                cmd = engine_adapter.run_command(script, run_opts)
                if backend == "docker":
                    result = await self._run_in_docker(cmd, script, container_name, run_opts.timeout_seconds)
                else:
                    result = await self._run_local(cmd, script, run_opts.timeout_seconds)

                run.exit_code = result.exit_code
                run.evidence = result.evidence
                if result.container_id:
                    run.container_or_cluster = result.container_id
                run.stdout_tail = result.stdout[-2000:] if result.stdout else ""
                run.status = "timed_out" if result.timed_out else ("completed" if result.exit_code == 0 else "failed")

                if result.summary_json:
                    run.result_artifact = next((a["path"] for a in result.evidence.get("artifacts", [])
                        if a.get("relative_path") in {"summary.json", "report/statistics.json"}), "")
                    raw_metrics = engine_adapter.parse_results(result.summary_json)
                    run.raw_metrics = asdict(raw_metrics)
                    run.engine_thresholds = raw_metrics.thresholds
                    LOGGER.info("performance_metrics_parsed", extra={
                        "perf_run_id": run_id, "engine": plan.engine,
                        "missing_metrics": [key for key, value in run.raw_metrics.items() if value is None],
                        "engine_threshold_count": len(run.engine_thresholds),
                    })

            except ResourceUnavailable:
                raise
            except Exception as e:
                LOGGER.exception("performance_execution_failed", extra={"perf_run_id": run_id, "backend": backend})
                run.status = "failed"
                run.stdout_tail = str(e)
            finally:
                run.completed_at = datetime.utcnow().isoformat()

        return run

    async def run_smoke(
        self,
        plan: PerfPlan,
        script: ScriptArtifact,
        engine_adapter: PerfEngineAdapter,
    ) -> SmokeResult:
        smoke_opts = engine_adapter.build_smoke_options(plan)
        backend = self.detect_backend(plan.engine)
        container_name = f"{self._container_prefix}-smoke-{uuid.uuid4().hex[:6]}"

        try:
            cmd = engine_adapter.run_command(script, smoke_opts)
            if backend == "docker":
                result = await self._run_in_docker(cmd, script, container_name, smoke_opts.timeout_seconds)
            else:
                result = await self._run_local(cmd, script, smoke_opts.timeout_seconds)

            smoke = self._validate_smoke(result, plan)
            return smoke.model_copy(update={"evidence": result.evidence})
        except ResourceUnavailable:
            raise
        except Exception as e:
            LOGGER.exception("performance_smoke_failed", extra={"backend": backend})
            return SmokeResult(passed=False, detail=f"冒烟执行异常: {e}")

    def _validate_smoke(self, result: ExecutionResult, plan: PerfPlan) -> SmokeResult:
        if result.timed_out:
            return SmokeResult(passed=False, detail="冒烟验证超时")

        if result.exit_code != 0:
            return SmokeResult(
                passed=False,
                detail=f"冒烟执行失败 (exit_code={result.exit_code}): {result.stderr[:500]}",
            )

        checked_status: list[int] = []
        extracted: dict[str, bool] = {}

        if not result.summary_json:
            return SmokeResult(passed=False, detail="冒烟验证缺少引擎汇总，无法确认完成请求")
        try:
            data = json.loads(result.summary_json)
            count, failed_rate = self._extract_smoke_counts(data)
            validation = request_validation(data, plan)
            checked_status = validation["checked_status"]
            extracted = validation["extracted"]

            if count is None or count <= 0:
                diagnostics = self._format_execution_diagnostics(result)
                detail = "冒烟验证无请求完成"
                if diagnostics:
                    detail = f"{detail}: {diagnostics}"
                return SmokeResult(passed=False, detail=detail)

            if failed_rate is None:
                return SmokeResult(passed=False, detail="冒烟验证缺少请求失败率")
            if not 0 <= failed_rate <= 1 or count % 1:
                raise ValueError("汇总中的请求数或失败率非法")
            if failed_rate > 0.5:
                return SmokeResult(passed=False, checked_status=checked_status,
                    extracted=extracted, request_validation=validation,
                    detail=f"冒烟验证失败率过高: {failed_rate*100:.0f}%（预期状态码可能不匹配）")
        except (ValueError, TypeError, KeyError) as exc:
            LOGGER.warning("performance_smoke_summary_invalid", extra={"engine": plan.engine, "error": str(exc)})
            return SmokeResult(passed=False, detail=f"冒烟汇总解析失败: {exc}")

        if result.evidence and result.evidence.get("status") != "saved":
            return SmokeResult(passed=False, detail="冒烟原件未完整保存: " + str(result.evidence.get("error") or result.evidence.get("missing_artifacts")))

        if validation["status"] in {"failed", "missing"} or not all(extracted.values()):
            LOGGER.warning("performance_smoke_request_invalid", extra={"engine": plan.engine,
                "validation_status": validation["status"], "missing_checks": validation["missing_checks"],
                "failed_checks": validation["failed_checks"]})
            return SmokeResult(passed=False, checked_status=checked_status, extracted=extracted,
                request_validation=validation, detail="冒烟响应核验未通过: " + str(
                    validation["failed_checks"] or validation["missing_checks"] or list(extracted)))

        return SmokeResult(passed=True, checked_status=checked_status, extracted=extracted,
            request_validation=validation, detail="冒烟验证通过")

    def _format_execution_diagnostics(self, result: ExecutionResult) -> str:
        parts: list[str] = []
        if result.exit_code:
            parts.append(f"exit_code={result.exit_code}")
        if result.stderr:
            parts.append(f"stderr={result.stderr.strip()[-500:]}")
        if result.stdout:
            parts.append(f"stdout={result.stdout.strip()[-500:]}")
        return "; ".join(parts)

    @staticmethod
    def _metric_values(metric: Any) -> dict[str, Any]:
        if not isinstance(metric, dict):
            return {}
        values = metric.get("values")
        if isinstance(values, dict):
            return values
        return metric

    def _extract_smoke_counts(self, data: dict[str, Any]) -> tuple[float | None, float | None]:
        """Return completed request count and failed rate for k6 or JMeter summaries."""
        if not isinstance(data, dict):
            raise ValueError("汇总必须是JSON对象")
        metrics = data.get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            values = self._metric_values(metrics.get("http_reqs", {}))
            count = metric_number(values, "count")
            failed_values = self._metric_values(metrics.get("http_req_failed", {}))
            failed_rate = metric_number(failed_values, "rate" if "rate" in failed_values else "value")
            return count, failed_rate

        total = data.get("Total", {})
        if isinstance(total, dict):
            count = metric_number(total, "sampleCount")
            error_pct = metric_number(total, "errorPct")
            return count, error_pct / 100.0 if error_pct is not None else None

        return None, None

    async def _run_in_docker(
        self,
        cmd: EngineCommand,
        script: ScriptArtifact,
        container_name: str,
        timeout_seconds: int,
    ) -> ExecutionResult:
        with tempfile.TemporaryDirectory(prefix="perf-") as tmpdir:
            script_path = Path(tmpdir) / script.filename
            script_path.write_text(script.script_content, encoding="utf-8")
            self._materialize_data_files(script, Path(tmpdir))

            summary_path = Path(tmpdir) / "summary.json"

            docker_cmd = [
                "--entrypoint=",
                "--add-host", "host.docker.internal:host-gateway",
                "--workdir", cmd.workdir,
                "-v", f"{tmpdir}:{cmd.workdir}",
            ]

            if cmd.cpus:
                docker_cmd.extend(["--cpus", cmd.cpus])
            if cmd.memory:
                docker_cmd.extend(["--memory", cmd.memory])

            for k, v in cmd.env.items():
                docker_cmd.extend(["-e", f"{k}={v}"])

            docker_cmd.append(cmd.image)
            docker_cmd.extend(cmd.command)

            result = await self._docker_resources.execute(
                docker_cmd, name_prefix=container_name, timeout_seconds=timeout_seconds,
            )
            captured = ExecutionResult(
                exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr,
                summary_json=self._read_summary_artifact(Path(tmpdir), summary_path),
                timed_out=result.timed_out, container_id=result.container_id,
            )
            captured.evidence = await store_execution_evidence(objects=self._objects, command=cmd,
                script=script, result=captured, workdir=Path(tmpdir))
            return captured

    async def _run_local(
        self,
        cmd: EngineCommand,
        script: ScriptArtifact,
        timeout_seconds: int,
    ) -> ExecutionResult:
        with tempfile.TemporaryDirectory(prefix="perf-") as tmpdir:
            script_path = Path(tmpdir) / script.filename
            script_path.write_text(script.script_content, encoding="utf-8")
            self._materialize_data_files(script, Path(tmpdir))

            local_cmd = []
            for part in cmd.command:
                local_cmd.append(part.replace("/work/", f"{tmpdir}/"))

            summary_path = Path(tmpdir) / "summary.json"

            env = dict(cmd.env)

            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    local_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    cwd=tmpdir,
                    env={**os.environ, **env},
                )
                summary_json = self._read_summary_artifact(Path(tmpdir), summary_path)

                captured = ExecutionResult(
                    exit_code=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    summary_json=summary_json,
                )
            except subprocess.TimeoutExpired as exc:
                def text_output(value):
                    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
                captured = ExecutionResult(exit_code=-1, stdout=text_output(exc.output),
                    stderr=text_output(exc.stderr), timed_out=True,
                    summary_json=self._read_summary_artifact(Path(tmpdir), summary_path))
            captured.evidence = await store_execution_evidence(objects=self._objects, command=cmd,
                script=script, result=captured, workdir=Path(tmpdir), executed_command=local_cmd)
            return captured

    @staticmethod
    def _read_summary_artifact(tmpdir: Path, summary_path: Path) -> str:
        if summary_path.exists():
            return summary_path.read_text(encoding="utf-8")

        jmeter_stats = tmpdir / "report" / "statistics.json"
        if jmeter_stats.exists():
            # Keep the dashboard metrics and the raw JTL together so the
            # shared request validator can inspect actual status/variables.
            data = json.loads(jmeter_stats.read_text(encoding="utf-8"))
            jtl = tmpdir / "result.jtl"
            if jtl.exists():
                data["__jtl"] = jtl.read_text(encoding="utf-8")
            return json.dumps(data, ensure_ascii=False)

        return ""

    @staticmethod
    def _materialize_data_files(script: ScriptArtifact, tmpdir: Path) -> None:
        for relative_path, source_path in script.data_files.items():
            source = Path(source_path)
            if not source.exists() or not source.is_file():
                continue
            target = tmpdir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

"""Performance runner service.

Manages backend detection (local/docker), container lifecycle,
smoke validation, concurrency gating, and execution orchestration.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfRun, SmokeResult
from .engine_adapter import EngineCommand, PerfEngineAdapter, RawMetrics, RunOptions, ScriptArtifact


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    summary_json: str = ""
    timed_out: bool = False


class PerfRunnerService:
    """Orchestrates performance test execution with backend detection and safety gates."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.orchestration.performance_max_concurrent_runs)
        self._container_prefix = settings.orchestration.performance_runner_docker_container_prefix
        self._workdir = settings.orchestration.performance_runner_docker_workdir
        self._ephemeral = settings.orchestration.performance_runner_ephemeral

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
                run.stdout_tail = result.stdout[-2000:] if result.stdout else ""
                run.status = "timed_out" if result.timed_out else ("completed" if result.exit_code == 0 else "failed")

                if result.summary_json:
                    run.result_artifact = f"inline:summary.json"
                    raw_metrics = engine_adapter.parse_results(result.summary_json)
                    run.raw_metrics = asdict(raw_metrics)
                    run.engine_thresholds = raw_metrics.thresholds

            except Exception as e:
                run.status = "failed"
                run.stdout_tail = str(e)
            finally:
                run.completed_at = datetime.utcnow().isoformat()
                if backend == "docker" and self._ephemeral:
                    await self._cleanup_container(container_name)

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

            return self._validate_smoke(result, plan)
        except Exception as e:
            return SmokeResult(passed=False, detail=f"冒烟执行异常: {e}")
        finally:
            if backend == "docker" and self._ephemeral:
                await self._cleanup_container(container_name)

    def _validate_smoke(self, result: ExecutionResult, plan: PerfPlan) -> SmokeResult:
        if result.timed_out:
            return SmokeResult(passed=False, detail="冒烟验证超时")

        if result.exit_code != 0 and not result.summary_json:
            return SmokeResult(
                passed=False,
                detail=f"冒烟执行失败 (exit_code={result.exit_code}): {result.stderr[:500]}",
            )

        checked_status: list[int] = []
        extracted: dict[str, bool] = {}

        if result.summary_json:
            try:
                data = json.loads(result.summary_json)
                count, failed_rate = self._extract_smoke_counts(data)

                if count == 0:
                    diagnostics = self._format_execution_diagnostics(result)
                    detail = "冒烟验证无请求完成"
                    if diagnostics:
                        detail = f"{detail}: {diagnostics}"
                    return SmokeResult(passed=False, detail=detail)

                if failed_rate > 0.5:
                    return SmokeResult(
                        passed=False,
                        checked_status=checked_status,
                        detail=f"冒烟验证失败率过高: {failed_rate*100:.0f}%（预期状态码可能不匹配）",
                    )
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        for var in plan.smoke.expect_extract:
            found = var.lower() in result.stdout.lower()
            extracted[var] = found
            if not found:
                return SmokeResult(
                    passed=False,
                    extracted=extracted,
                    detail=f"关联变量 '{var}' 未在响应中提取成功",
                )

        return SmokeResult(passed=True, checked_status=checked_status, extracted=extracted, detail="冒烟验证通过")

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

    def _extract_smoke_counts(self, data: dict[str, Any]) -> tuple[int, float]:
        """Return completed request count and failed rate for k6 or JMeter summaries."""
        metrics = data.get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            values = self._metric_values(metrics.get("http_reqs", {}))
            count = int(values.get("count", 0))
            failed_values = self._metric_values(metrics.get("http_req_failed", {}))
            failed_rate = float(failed_values.get("rate", failed_values.get("value", 1)))
            return count, failed_rate

        total = data.get("Total", {})
        if isinstance(total, dict):
            count = int(total.get("sampleCount", 0))
            error_pct = float(total.get("errorPct", 100.0))
            return count, error_pct / 100.0

        return 0, 1.0

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
                "docker", "run", "--rm",
                "--entrypoint=",
                "--name", container_name,
                "--add-host", "host.docker.internal:host-gateway",
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

            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    docker_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                )
                summary_json = self._read_summary_artifact(Path(tmpdir), summary_path)

                return ExecutionResult(
                    exit_code=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    summary_json=summary_json,
                )
            except subprocess.TimeoutExpired:
                await self._cleanup_container(container_name)
                return ExecutionResult(exit_code=-1, stdout="", stderr="timeout", timed_out=True)

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

                return ExecutionResult(
                    exit_code=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    summary_json=summary_json,
                )
            except subprocess.TimeoutExpired:
                return ExecutionResult(exit_code=-1, stdout="", stderr="timeout", timed_out=True)

    async def _cleanup_container(self, container_name: str) -> None:
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass

    @staticmethod
    def _read_summary_artifact(tmpdir: Path, summary_path: Path) -> str:
        if summary_path.exists():
            return summary_path.read_text(encoding="utf-8")

        jmeter_stats = tmpdir / "report" / "statistics.json"
        if jmeter_stats.exists():
            return jmeter_stats.read_text(encoding="utf-8")

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

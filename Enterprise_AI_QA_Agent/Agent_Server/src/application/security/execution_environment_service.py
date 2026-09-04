"""Execution environment for security command profiles.

The security runner can execute tools either on the local host or inside a
Docker container. Docker mode mirrors PentAGI's controlled command pattern:
start a pentest image, run ``docker exec sh -lc`` for the approved command
profile, then either keep the container for reuse or remove it as a temporary
runner.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

logger = logging.getLogger("uvicorn.error.security_testing_mode.execution_environment")


@dataclass
class SecurityCommandExecutionResult:
    """Result returned by a security command execution backend."""

    backend: str
    command: str
    argv: list[str]
    cwd: str
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    started_at: datetime
    completed_at: datetime
    container_name: str = ""
    output_artifacts: list[dict[str, Any]] = field(default_factory=list)
    job_id: str = ""
    detached: bool = False
    is_running: bool = False


@dataclass
class _DetachedJobRecord:
    job_id: str
    container_name: str
    command: str
    argv: list[str]
    cwd: str
    host_workdir: Path
    log_path: str
    pid_path: str
    exit_path: str
    started_at: datetime
    cleanup_after_run: bool
    completed_result: SecurityCommandExecutionResult | None = None


class SecurityExecutionEnvironmentService:
    """Run security commands in a configured execution environment."""

    def __init__(
        self,
        *,
        settings: Any = None,
        workspace_root: Path | str | None = None,
    ) -> None:
        self._settings = settings
        self._workspace_root = Path(workspace_root or Path.cwd())
        self._detached_jobs: dict[str, _DetachedJobRecord] = {}
        self._detached_jobs_lock = threading.Lock()

    async def execute(
        self,
        *,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any = None,
    ) -> SecurityCommandExecutionResult:
        # S6 defense-in-depth: refuse commands whose high-confidence network
        # target (URL / literal IP) falls outside the configured allowlist.
        # The precise per-task gate lives in the coordinator; this is the last
        # chokepoint that every worker-initiated command must pass through.
        self._enforce_target_allowlist(command_args)
        backend = self._get_str("security_runner_backend", "SECURITY_RUNNER_BACKEND", "local").lower()
        if backend in {"docker", "container"}:
            command, command_args = self._rewrite_localhost_for_docker(command, command_args)
        logger.info(
            "security_runner_execute_start backend=%s executable=%s timeout_seconds=%s session_id=%s turn_id=%s",
            backend,
            command_args[0] if command_args else "",
            timeout_seconds,
            getattr(context, "session_id", "") or "",
            getattr(context, "turn_id", "") or "",
        )
        if backend in {"docker", "container"}:
            result = await asyncio.to_thread(
                self._run_in_docker,
                command,
                command_args,
                timeout_seconds,
                artifact_dir,
                context,
            )
        elif backend in {"local", "host"}:
            result = await asyncio.to_thread(
                self._run_local,
                command,
                command_args,
                timeout_seconds,
            )
        else:
            raise ValueError(f"Unsupported security runner backend: {backend}")
        logger.info(
            "security_runner_execute_complete backend=%s executable=%s exit_code=%s timed_out=%s container=%s stdout_bytes=%s stderr_bytes=%s",
            result.backend,
            command_args[0] if command_args else "",
            result.exit_code,
            result.timed_out,
            result.container_name,
            len(result.stdout.encode("utf-8", errors="replace")),
            len(result.stderr.encode("utf-8", errors="replace")),
        )
        return result

    async def execute_detached(
        self,
        *,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any = None,
    ) -> SecurityCommandExecutionResult:
        """Start a Docker-backed command and return immediately with a job id.

        The command writes its complete combined stdout/stderr and final exit
        code beneath ``<container_workdir>/.jobs``. ``poll_execution`` reads
        those files without losing partial output. Detached execution is kept
        Docker-only because a host-side background process would escape the
        security runner's isolation and cleanup contract.
        """
        self._enforce_target_allowlist(command_args)
        backend = self._get_str("security_runner_backend", "SECURITY_RUNNER_BACKEND", "local").lower()
        if backend not in {"docker", "container"}:
            raise ValueError("Detached security execution requires the Docker runner backend.")
        command, command_args = self._rewrite_localhost_for_docker(command, command_args)
        return await asyncio.to_thread(
            self._start_detached_in_docker,
            command,
            command_args,
            timeout_seconds,
            artifact_dir,
            context,
        )

    async def poll_execution(self, job_id: str) -> SecurityCommandExecutionResult:
        """Return all output produced by a detached job and its running state."""
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("Detached security execution job_id is required.")
        return await asyncio.to_thread(self._poll_detached_execution, normalized_job_id)

    async def put_file(
        self,
        *,
        local_path: Path | str,
        container_path: str,
        artifact_dir: Path,
        context: Any = None,
        container_name: str = "",
    ) -> str:
        """Copy a host file into the security container and return its name."""
        return await asyncio.to_thread(
            self._put_file_in_docker,
            Path(local_path),
            container_path,
            artifact_dir,
            context,
            container_name,
        )

    async def get_file(
        self,
        *,
        container_path: str,
        local_path: Path | str,
        artifact_dir: Path,
        context: Any = None,
        container_name: str = "",
    ) -> Path:
        """Copy a file out of the security container into ``artifact_dir``."""
        return await asyncio.to_thread(
            self._get_file_from_docker,
            container_path,
            Path(local_path),
            artifact_dir,
            context,
            container_name,
        )

    async def cleanup_container(self, container_name: str) -> None:
        """Explicitly destroy a security runner container.

        File-transfer callers that create a container without running a normal
        foreground command use this method to satisfy the ephemeral-runner
        lifecycle contract.
        """
        normalized_name = str(container_name or "").strip()
        if not normalized_name:
            return
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        error = await asyncio.to_thread(
            self._remove_container,
            docker=docker,
            container_name=normalized_name,
        )
        if error:
            raise RuntimeError(error)

    async def create_persistent_container(
        self,
        *,
        campaign_id: str,
        artifact_dir: Path,
    ) -> str:
        """Create or reuse the dedicated Docker container for one campaign."""
        return await asyncio.to_thread(
            self._create_persistent_container,
            campaign_id,
            artifact_dir,
        )

    async def execute_in_container(
        self,
        *,
        container_name: str,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any = None,
    ) -> SecurityCommandExecutionResult:
        """Execute one validated command in an existing campaign container."""
        self._enforce_target_allowlist(command_args)
        command, command_args = self._rewrite_localhost_for_docker(command, command_args)
        return await asyncio.to_thread(
            self._run_in_existing_container,
            container_name,
            command,
            command_args,
            timeout_seconds,
            artifact_dir,
            context,
        )

    async def container_heartbeat(self, container_name: str) -> bool:
        """Return whether a campaign container is still running."""
        return await asyncio.to_thread(self._container_is_running, container_name)

    def _enforce_target_allowlist(self, command_args: list[str]) -> None:
        """Reject commands targeting hosts outside the allowlist (S6).

        No-op when no allowlist is configured. Only high-confidence targets
        (URLs and literal IPs) are inspected so a misparse never wrongly blocks
        a legitimate command (``宁可漏过、不可误杀``).
        """
        from src.application.security.target_guard import SecurityTargetGuard

        guard = SecurityTargetGuard(self._settings)
        if not guard.has_allowlist:
            return
        result = guard.evaluate_command(command_args)
        if result.ok:
            return
        logger.warning(
            "security_runner target denied by allowlist: %s (hosts=%s)",
            result.reason,
            result.checked_hosts,
        )
        raise ValueError(f"security_target_allowlist_denied: {result.reason}")

    def _rewrite_localhost_for_docker(
        self,
        command: str,
        command_args: list[str],
    ) -> tuple[str, list[str]]:
        """Map host-loopback targets to Docker Desktop's host gateway.

        A security profile targeting ``localhost`` must reach the authorized
        host application, not the Kali container itself. The allowlist gate is
        evaluated before this transport-only rewrite so authorization remains
        expressed in the user's original target coordinates.
        """
        enabled = self._get_bool(
            "security_runner_rewrite_localhost",
            "SECURITY_RUNNER_REWRITE_LOCALHOST",
            True,
        )
        if not enabled:
            return command, list(command_args)

        pattern = re.compile(
            r"(?<![A-Za-z0-9_.-])(?:localhost|127\.0\.0\.1|\[::1\])(?=[:/\s'\"]|$)",
            re.IGNORECASE,
        )
        rewritten_command = pattern.sub("host.docker.internal", str(command or ""))
        rewritten_args = [pattern.sub("host.docker.internal", str(item)) for item in command_args]
        if rewritten_command != command or rewritten_args != command_args:
            logger.info(
                "security_runner_localhost_rewritten replacement=host.docker.internal executable=%s",
                rewritten_args[0] if rewritten_args else "",
            )
        return rewritten_command, rewritten_args

    def _run_local(
        self,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
    ) -> SecurityCommandExecutionResult:
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")

        executable = command_args[0]
        resolved_executable = shutil.which(executable)
        if resolved_executable is None:
            raise FileNotFoundError(f"Security tool '{executable}' is not installed or not on PATH.")

        started_at = _utc_now()
        try:
            completed = subprocess.run(
                [resolved_executable, *command_args[1:]],
                cwd=str(self._workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
            exit_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout_text = exc.stdout or ""
            stderr_text = exc.stderr or ""
            exit_code = -1
            timed_out = True

        return SecurityCommandExecutionResult(
            backend="local",
            command=command,
            argv=[resolved_executable, *command_args[1:]],
            cwd=str(self._workspace_root),
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            timed_out=timed_out,
            started_at=started_at,
            completed_at=_utc_now(),
        )

    def _run_in_docker(
        self,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any,
    ) -> SecurityCommandExecutionResult:
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")

        image = self._get_str(
            "security_runner_docker_image",
            "SECURITY_RUNNER_DOCKER_IMAGE",
            "vxcontrol/kali-linux",
        )
        workdir = self._get_str(
            "security_runner_docker_workdir",
            "SECURITY_RUNNER_DOCKER_WORKDIR",
            "/work",
        )
        container_name = self._container_name(context)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        host_workdir = self._host_workdir_for_container(artifact_dir)
        host_workdir.mkdir(parents=True, exist_ok=True)
        cleanup_after_run = self._cleanup_after_run_enabled()

        self._ensure_container(
            docker=docker,
            image=image,
            container_name=container_name,
            host_workdir=host_workdir,
            container_workdir=workdir,
        )

        started_at = _utc_now()
        shell_command = self._wrap_with_shell_timeout(command, timeout_seconds)
        docker_args = [
            docker,
            "exec",
            "-w",
            workdir,
            container_name,
            "sh",
            "-lc",
            shell_command,
        ]
        cleanup_error = ""
        try:
            try:
                completed = subprocess.run(
                    docker_args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds + 5,
                )
                stdout_text = completed.stdout or ""
                stderr_text = completed.stderr or ""
                exit_code = completed.returncode
                timed_out = exit_code == 124
            except subprocess.TimeoutExpired as exc:
                stdout_text = exc.stdout or ""
                stderr_text = exc.stderr or ""
                exit_code = -1
                timed_out = True
        finally:
            if cleanup_after_run:
                cleanup_error = self._remove_container(docker=docker, container_name=container_name)
        if cleanup_error:
            stderr_text = _append_stderr(stderr_text, cleanup_error)

        return SecurityCommandExecutionResult(
            backend="docker",
            command=command,
            argv=docker_args,
            cwd=workdir,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            timed_out=timed_out,
            started_at=started_at,
            completed_at=_utc_now(),
            container_name=container_name,
            output_artifacts=self._collect_output_artifacts(host_workdir),
        )

    def _create_persistent_container(self, campaign_id: str, artifact_dir: Path) -> str:
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        normalized_campaign_id = str(campaign_id or "").strip()
        if not normalized_campaign_id:
            raise ValueError("Persistent security container requires campaign_id.")
        image = self._get_str(
            "security_runner_docker_image",
            "SECURITY_RUNNER_DOCKER_IMAGE",
            "vxcontrol/kali-linux",
        )
        workdir = self._get_str(
            "security_runner_docker_workdir",
            "SECURITY_RUNNER_DOCKER_WORKDIR",
            "/work",
        )
        prefix = self._get_str(
            "security_runner_docker_container_prefix",
            "SECURITY_RUNNER_DOCKER_CONTAINER_PREFIX",
            "qa-security-runner",
        )
        container_name = f"{_slug(prefix)}-attack-{_slug(normalized_campaign_id)}"[:120].strip("-")
        host_workdir = artifact_dir / "_security_attack_session_work"
        host_workdir.mkdir(parents=True, exist_ok=True)
        self._ensure_persistent_container(
            docker=docker,
            image=image,
            container_name=container_name,
            host_workdir=host_workdir,
            container_workdir=workdir,
        )
        logger.info(
            "security_attack_container_ready campaign_id=%s container=%s",
            normalized_campaign_id,
            container_name,
        )
        return container_name

    def _run_in_existing_container(
        self,
        container_name: str,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any,
    ) -> SecurityCommandExecutionResult:
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        normalized_name = str(container_name or "").strip()
        if not normalized_name:
            raise ValueError("Persistent security container_name is required.")
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")
        if not self._container_is_running(normalized_name):
            raise RuntimeError(f"Security runner container is not running: {normalized_name}")
        workdir = self._get_str(
            "security_runner_docker_workdir",
            "SECURITY_RUNNER_DOCKER_WORKDIR",
            "/work",
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        started_at = _utc_now()
        shell_command = self._wrap_with_shell_timeout(command, timeout_seconds)
        docker_args = [
            docker,
            "exec",
            "-w",
            workdir,
            normalized_name,
            "sh",
            "-lc",
            shell_command,
        ]
        logger.info(
            "security_attack_session_exec_start container=%s timeout_seconds=%s session_id=%s turn_id=%s",
            normalized_name,
            timeout_seconds,
            getattr(context, "session_id", "") or "",
            getattr(context, "turn_id", "") or "",
        )
        try:
            completed = subprocess.run(
                docker_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds + 5,
            )
            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
            exit_code = completed.returncode
            timed_out = exit_code == 124
        except subprocess.TimeoutExpired as exc:
            stdout_text = exc.stdout or ""
            stderr_text = exc.stderr or ""
            exit_code = -1
            timed_out = True
        result = SecurityCommandExecutionResult(
            backend="docker",
            command=command,
            argv=docker_args,
            cwd=workdir,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            timed_out=timed_out,
            started_at=started_at,
            completed_at=_utc_now(),
            container_name=normalized_name,
            output_artifacts=self._collect_output_artifacts(
                artifact_dir / "_security_attack_session_work"
            ),
        )
        logger.info(
            "security_attack_session_exec_complete container=%s exit_code=%s timed_out=%s stdout_bytes=%s stderr_bytes=%s",
            normalized_name,
            exit_code,
            timed_out,
            len(stdout_text.encode("utf-8", errors="replace")),
            len(stderr_text.encode("utf-8", errors="replace")),
        )
        return result

    def _container_is_running(self, container_name: str) -> bool:
        docker = shutil.which("docker")
        if docker is None:
            return False
        inspect = subprocess.run(
            [docker, "inspect", "-f", "{{.State.Running}}", str(container_name)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return inspect.returncode == 0 and (inspect.stdout or "").strip().lower() == "true"

    def _ensure_persistent_container(
        self,
        *,
        docker: str,
        image: str,
        container_name: str,
        host_workdir: Path,
        container_workdir: str,
    ) -> None:
        if self._container_is_running(container_name):
            return
        inspect = subprocess.run(
            [docker, "inspect", container_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if inspect.returncode == 0:
            remove_error = self._remove_container(docker=docker, container_name=container_name)
            if remove_error:
                raise RuntimeError(remove_error)
        self._pull_image_if_needed(docker, image)
        run_args = [
            docker,
            "run",
            "-d",
            "--name",
            container_name,
            "--workdir",
            container_workdir,
        ]
        if self._get_bool("security_runner_docker_net_raw", "SECURITY_RUNNER_DOCKER_NET_RAW", True):
            run_args.extend(["--cap-add", "NET_RAW"])
        if self._get_bool("security_runner_docker_net_admin", "SECURITY_RUNNER_DOCKER_NET_ADMIN", False):
            run_args.extend(["--cap-add", "NET_ADMIN"])
        network = self._get_str("security_runner_docker_network", "SECURITY_RUNNER_DOCKER_NETWORK", "")
        if network:
            run_args.extend(["--network", network])
        run_args.extend(
            ["-v", f"{host_workdir.resolve()}:{container_workdir}", image, "tail", "-f", "/dev/null"]
        )
        run = subprocess.run(
            run_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if run.returncode != 0:
            raise RuntimeError(run.stderr.strip() or run.stdout.strip() or "Failed to create security Docker container.")

    def _start_detached_in_docker(
        self,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any,
    ) -> SecurityCommandExecutionResult:
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")

        image = self._get_str(
            "security_runner_docker_image",
            "SECURITY_RUNNER_DOCKER_IMAGE",
            "vxcontrol/kali-linux",
        )
        workdir = self._get_str(
            "security_runner_docker_workdir",
            "SECURITY_RUNNER_DOCKER_WORKDIR",
            "/work",
        )
        container_name = self._container_name(context)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        host_workdir = self._host_workdir_for_container(artifact_dir)
        host_workdir.mkdir(parents=True, exist_ok=True)
        cleanup_after_run = self._cleanup_after_run_enabled()
        self._ensure_container(
            docker=docker,
            image=image,
            container_name=container_name,
            host_workdir=host_workdir,
            container_workdir=workdir,
        )

        job_id = f"security-{uuid4().hex}"
        jobs_dir = self._validated_container_path(".jobs", workdir)
        log_path = self._validated_container_path(f".jobs/{job_id}.log", workdir)
        pid_path = self._validated_container_path(f".jobs/{job_id}.pid", workdir)
        exit_path = self._validated_container_path(f".jobs/{job_id}.exit", workdir)
        wrapped_command = self._wrap_with_shell_timeout(command, timeout_seconds)
        launch_script = (
            f"mkdir -p {shlex.quote(jobs_dir)} && {{ "
            f"( {wrapped_command}; rc=$?; printf '%s' \"$rc\" > {shlex.quote(exit_path)} ) "
            f"> {shlex.quote(log_path)} 2>&1 & "
            f"pid=$!; printf '%s' \"$pid\" > {shlex.quote(pid_path)}; }}"
        )
        docker_args = [
            docker,
            "exec",
            "-d",
            "-w",
            workdir,
            container_name,
            "sh",
            "-lc",
            launch_script,
        ]
        started_at = _utc_now()
        launch = subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if launch.returncode != 0:
            cleanup_error = ""
            if cleanup_after_run:
                cleanup_error = self._remove_container(docker=docker, container_name=container_name)
            detail = (launch.stderr or launch.stdout or "Failed to start detached security command.").strip()
            if cleanup_error:
                detail = _append_stderr(detail, cleanup_error)
            raise RuntimeError(detail)

        record = _DetachedJobRecord(
            job_id=job_id,
            container_name=container_name,
            command=command,
            argv=docker_args,
            cwd=workdir,
            host_workdir=host_workdir,
            log_path=log_path,
            pid_path=pid_path,
            exit_path=exit_path,
            started_at=started_at,
            cleanup_after_run=cleanup_after_run,
        )
        with self._detached_jobs_lock:
            self._detached_jobs[job_id] = record
        logger.info(
            "security detached execution started: job=%s container=%s",
            job_id,
            container_name,
        )
        return SecurityCommandExecutionResult(
            backend="docker",
            command=command,
            argv=docker_args,
            cwd=workdir,
            stdout="",
            stderr=(launch.stderr or ""),
            exit_code=-1,
            timed_out=False,
            started_at=started_at,
            completed_at=_utc_now(),
            container_name=container_name,
            job_id=job_id,
            detached=True,
            is_running=True,
        )

    def _poll_detached_execution(self, job_id: str) -> SecurityCommandExecutionResult:
        with self._detached_jobs_lock:
            record = self._detached_jobs.get(job_id)
        if record is None:
            raise KeyError(f"Unknown detached security execution job: {job_id}")
        if record.completed_result is not None:
            return record.completed_result

        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        status_script = (
            f"if [ -f {shlex.quote(record.exit_path)} ]; then "
            f"printf 'exit:'; cat {shlex.quote(record.exit_path)}; "
            f"elif [ -f {shlex.quote(record.pid_path)} ] && "
            f"kill -0 \"$(cat {shlex.quote(record.pid_path)})\" 2>/dev/null; then "
            "printf 'running'; else printf 'lost'; fi"
        )
        status = subprocess.run(
            [docker, "exec", record.container_name, "sh", "-lc", status_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        log_read = subprocess.run(
            [docker, "exec", record.container_name, "sh", "-lc", f"cat {shlex.quote(record.log_path)} 2>/dev/null || true"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        stdout_text = log_read.stdout or ""
        stderr_text = "\n".join(
            item.strip()
            for item in (status.stderr or "", log_read.stderr or "")
            if item.strip()
        )
        status_text = (status.stdout or "").strip()
        is_running = status.returncode == 0 and status_text == "running"
        if is_running:
            return SecurityCommandExecutionResult(
                backend="docker",
                command=record.command,
                argv=record.argv,
                cwd=record.cwd,
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=-1,
                timed_out=False,
                started_at=record.started_at,
                completed_at=_utc_now(),
                container_name=record.container_name,
                job_id=record.job_id,
                detached=True,
                is_running=True,
            )

        if status.returncode != 0:
            status_detail = status.stderr or status.stdout or "Detached runner container is unavailable."
            stderr_text = _append_stderr(stderr_text, status_detail.strip())
            exit_code = 255
        elif status_text.startswith("exit:"):
            try:
                exit_code = int(status_text.split(":", 1)[1].strip())
            except ValueError:
                exit_code = 255
                stderr_text = _append_stderr(stderr_text, f"Invalid detached exit status: {status_text}")
        else:
            exit_code = 255
            stderr_text = _append_stderr(
                stderr_text,
                "Detached security command is no longer running and did not write an exit status.",
            )

        if record.cleanup_after_run:
            cleanup_error = self._remove_container(docker=docker, container_name=record.container_name)
            if cleanup_error:
                stderr_text = _append_stderr(stderr_text, cleanup_error)
        result = SecurityCommandExecutionResult(
            backend="docker",
            command=record.command,
            argv=record.argv,
            cwd=record.cwd,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            timed_out=exit_code == 124,
            started_at=record.started_at,
            completed_at=_utc_now(),
            container_name=record.container_name,
            output_artifacts=self._collect_output_artifacts(record.host_workdir),
            job_id=record.job_id,
            detached=True,
            is_running=False,
        )
        record.completed_result = result
        logger.info(
            "security detached execution completed: job=%s exit_code=%s container=%s",
            job_id,
            exit_code,
            record.container_name,
        )
        return result

    def _put_file_in_docker(
        self,
        local_path: Path,
        container_path: str,
        artifact_dir: Path,
        context: Any,
        container_name: str,
    ) -> str:
        docker, workdir, resolved_container_name = self._prepare_file_container(
            artifact_dir=artifact_dir,
            context=context,
            container_name=container_name,
        )
        source = local_path.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Security runner input file does not exist: {source}")
        destination = self._validated_container_path(container_path, workdir)
        mkdir = subprocess.run(
            [docker, "exec", resolved_container_name, "mkdir", "-p", str(PurePosixPath(destination).parent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if mkdir.returncode != 0:
            raise RuntimeError(mkdir.stderr.strip() or mkdir.stdout.strip() or "Failed to create container directory.")
        copied = subprocess.run(
            [docker, "cp", str(source), f"{resolved_container_name}:{destination}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if copied.returncode != 0:
            raise RuntimeError(copied.stderr.strip() or copied.stdout.strip() or "Failed to copy file into security container.")
        return resolved_container_name

    def _get_file_from_docker(
        self,
        container_path: str,
        local_path: Path,
        artifact_dir: Path,
        context: Any,
        container_name: str,
    ) -> Path:
        docker, workdir, resolved_container_name = self._prepare_file_container(
            artifact_dir=artifact_dir,
            context=context,
            container_name=container_name,
        )
        source = self._validated_container_path(container_path, workdir)
        artifact_root = artifact_dir.expanduser().resolve()
        destination = local_path.expanduser()
        if not destination.is_absolute():
            destination = artifact_root / destination
        destination = destination.resolve()
        if destination != artifact_root and artifact_root not in destination.parents:
            raise ValueError("Security runner output path must stay inside artifact_dir.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        copied = subprocess.run(
            [docker, "cp", f"{resolved_container_name}:{source}", str(destination)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if copied.returncode != 0:
            raise RuntimeError(copied.stderr.strip() or copied.stdout.strip() or "Failed to copy file from security container.")
        return destination

    def _prepare_file_container(
        self,
        *,
        artifact_dir: Path,
        context: Any,
        container_name: str,
    ) -> tuple[str, str, str]:
        backend = self._get_str("security_runner_backend", "SECURITY_RUNNER_BACKEND", "local").lower()
        if backend not in {"docker", "container"}:
            raise ValueError("Security runner file transfer requires the Docker runner backend.")
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        workdir = self._get_str(
            "security_runner_docker_workdir",
            "SECURITY_RUNNER_DOCKER_WORKDIR",
            "/work",
        )
        resolved_container_name = str(container_name or "").strip() or self._container_name(context)
        if not container_name:
            image = self._get_str(
                "security_runner_docker_image",
                "SECURITY_RUNNER_DOCKER_IMAGE",
                "vxcontrol/kali-linux",
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            host_workdir = self._host_workdir_for_container(artifact_dir)
            host_workdir.mkdir(parents=True, exist_ok=True)
            self._ensure_container(
                docker=docker,
                image=image,
                container_name=resolved_container_name,
                host_workdir=host_workdir,
                container_workdir=workdir,
            )
        else:
            inspect = subprocess.run(
                [docker, "inspect", "-f", "{{.State.Running}}", resolved_container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if inspect.returncode != 0 or (inspect.stdout or "").strip().lower() != "true":
                raise RuntimeError(f"Security runner container is not running: {resolved_container_name}")
        return docker, workdir, resolved_container_name

    def _validated_container_path(self, path: str, workdir: str) -> str:
        root = PurePosixPath(workdir)
        candidate = PurePosixPath(str(path or "").strip())
        if not str(candidate):
            raise ValueError("Security runner container path is required.")
        if ".." in candidate.parts:
            raise ValueError("Security runner container path traversal is not allowed.")
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate == root:
            return str(candidate)
        if root not in candidate.parents:
            raise ValueError(f"Security runner container path must stay inside {root}.")
        return str(candidate)

    def _cleanup_after_run_enabled(self) -> bool:
        return self._get_bool(
            "security_runner_docker_cleanup_after_run",
            "SECURITY_RUNNER_DOCKER_CLEANUP_AFTER_RUN",
            not self._container_reuse_enabled(),
        )

    def _container_reuse_enabled(self) -> bool:
        return self._get_bool("security_runner_container_reuse", "SECURITY_RUNNER_CONTAINER_REUSE", False)

    def _remove_container(self, *, docker: str, container_name: str) -> str:
        try:
            remove = subprocess.run(
                [docker, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return f"Failed to remove security Docker container {container_name}: cleanup timed out."
        if remove.returncode == 0:
            return ""
        detail = (remove.stderr or remove.stdout or "").strip()
        return f"Failed to remove security Docker container {container_name}: {detail or 'unknown error'}"

    def _ensure_container(
        self,
        *,
        docker: str,
        image: str,
        container_name: str,
        host_workdir: Path,
        container_workdir: str,
    ) -> None:
        inspect = subprocess.run(
            [docker, "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if inspect.returncode == 0:
            if (inspect.stdout or "").strip().lower() == "true":
                return
            if self._container_reuse_enabled():
                start = subprocess.run(
                    [docker, "start", container_name],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if start.returncode != 0:
                    raise RuntimeError(
                        start.stderr.strip() or start.stdout.strip() or "Failed to start security Docker container."
                    )
                return
            self._remove_container(docker=docker, container_name=container_name)

        self._pull_image_if_needed(docker, image)
        run_args = [
            docker,
            "run",
            "-d",
            "--name",
            container_name,
            "--workdir",
            container_workdir,
        ]
        if self._get_bool("security_runner_docker_net_raw", "SECURITY_RUNNER_DOCKER_NET_RAW", True):
            run_args.extend(["--cap-add", "NET_RAW"])
        if self._get_bool("security_runner_docker_net_admin", "SECURITY_RUNNER_DOCKER_NET_ADMIN", False):
            run_args.extend(["--cap-add", "NET_ADMIN"])

        network = self._get_str("security_runner_docker_network", "SECURITY_RUNNER_DOCKER_NETWORK", "")
        if network:
            run_args.extend(["--network", network])

        mount_spec = f"{host_workdir.resolve()}:{container_workdir}"
        run_args.extend(["-v", mount_spec, image, "tail", "-f", "/dev/null"])
        run = subprocess.run(
            run_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if run.returncode != 0:
            raise RuntimeError(run.stderr.strip() or run.stdout.strip() or "Failed to create security Docker container.")

    def _pull_image_if_needed(self, docker: str, image: str) -> None:
        policy = self._get_str(
            "security_runner_docker_pull_policy",
            "SECURITY_RUNNER_DOCKER_PULL_POLICY",
            "never",
        ).lower()
        if policy == "never":
            return
        if policy == "missing":
            inspect = subprocess.run(
                [docker, "image", "inspect", image],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if inspect.returncode == 0:
                return
        if policy not in {"always", "missing"}:
            return
        pull = subprocess.run(
            [docker, "pull", image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if pull.returncode != 0:
            raise RuntimeError(pull.stderr.strip() or pull.stdout.strip() or f"Failed to pull Docker image {image}.")

    def _wrap_with_shell_timeout(self, command: str, timeout_seconds: float) -> str:
        if not self._get_bool("security_runner_wrap_timeout", "SECURITY_RUNNER_WRAP_TIMEOUT", True):
            return command
        inner_timeout = max(1, int(timeout_seconds) - 2)
        return f"timeout {inner_timeout}s sh -c {shlex.quote(command)}"

    def _container_name(self, context: Any) -> str:
        prefix = self._get_str(
            "security_runner_docker_container_prefix",
            "SECURITY_RUNNER_DOCKER_CONTAINER_PREFIX",
            "qa-security-runner",
        )
        session_id = getattr(context, "session_id", "") or "session"
        turn_id = getattr(context, "turn_id", "") or "turn"
        reuse = self._container_reuse_enabled()
        suffix = f"{_slug(session_id)}-{_slug(turn_id)}"
        if not reuse:
            suffix = f"{suffix}-{_utc_now().strftime('%Y%m%d%H%M%S')}"
        return f"{_slug(prefix)}-{suffix}"[:120].strip("-") or "qa-security-runner"

    def _host_workdir_for_container(self, artifact_dir: Path) -> Path:
        if self._container_reuse_enabled():
            return artifact_dir.parent / "_security_runner_work"
        return artifact_dir

    def _collect_output_artifacts(self, artifact_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for path in sorted(artifact_dir.rglob("*")):
            if not path.is_file():
                continue
            if ".jobs" in path.relative_to(artifact_dir).parts:
                continue
            artifacts.append(
                {
                    "type": "security_runner_output",
                    "label": path.name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        return artifacts

    def _get_str(self, attr: str, env_name: str, default: str) -> str:
        # Check nested security namespace first (D1a refactoring), then flat attribute.
        if self._settings is not None:
            security_ns = getattr(self._settings, "security", None)
            if security_ns is not None:
                value = getattr(security_ns, attr, None)
                if value not in (None, ""):
                    return str(value).strip()
            value = getattr(self._settings, attr, None)
        else:
            value = None
        if value not in (None, ""):
            return str(value).strip()
        return str(os.getenv(env_name, default) or default).strip()

    def _get_bool(self, attr: str, env_name: str, default: bool) -> bool:
        # Check nested security namespace first (D1a refactoring), then flat attribute.
        value = None
        if self._settings is not None:
            security_ns = getattr(self._settings, "security", None)
            if security_ns is not None:
                value = getattr(security_ns, attr, None)
            if value is None:
                value = getattr(self._settings, attr, None)
        if value is None:
            value = os.getenv(env_name)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip().lower())
    return cleaned.strip("-_.") or "default"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _append_stderr(stderr_text: str, extra: str) -> str:
    if not stderr_text:
        return extra
    return f"{stderr_text.rstrip()}\n{extra}"

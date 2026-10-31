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
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from src.core.config import get_settings
from src.core.request_context import bind_request_context, get_request_context
from src.application.runtime.tool_job_service import ToolJobService
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.runtime.docker_resource_manager import DockerCommandTimeout, DockerResourceManager
from src.runtime.docker_resource_session import DockerResourceSession
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_job import ToolJobStatus

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
    # Detached execution is tri-state. ToolJob termination and a cancelled
    # Docker CLI cannot prove that the process inside its container stopped.
    is_running: bool | None = False
    execution_observation: str = "unknown"


@dataclass
class _DetachedJobRecord:
    result: SecurityCommandExecutionResult
    container: _ManagedSecurityContainer
    log_path: Path
    cleanup_after_run: bool
    identity: _DetachedExecutionIdentity
    task: asyncio.Task | None = None
    stopping: bool = False
    operation_started: bool = False


@dataclass(frozen=True)
class _DetachedExecutionIdentity:
    job_id: str
    container_id: str

    @classmethod
    def from_result(cls, result: SecurityCommandExecutionResult) -> _DetachedExecutionIdentity | None:
        return cls.from_snapshot({"job_id": result.job_id, "argv": result.argv,
            "cwd": result.cwd, "backend": result.backend, "detached": result.detached})

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> _DetachedExecutionIdentity | None:
        # Read the exact persisted argv produced by _start_detached_in_docker,
        # including pre-observation snapshots. Do not infer identity from a
        # command string, a container name, or arbitrary Docker argv variants.
        if not isinstance(snapshot, dict):
            return None
        argv = snapshot.get("argv")
        if (snapshot.get("backend") != "docker" or snapshot.get("detached") is not True or not snapshot.get("job_id")
            or not isinstance(argv, list) or len(argv) != 8
            or argv[:3] != ["docker", "exec", "-w"] or argv[3] != snapshot.get("cwd")
            or argv[5:7] != ["sh", "-lc"] or not isinstance(argv[4], str) or not isinstance(argv[7], str)
            or not re.fullmatch(r"[a-f0-9]{64}", argv[4])):
            return None
        return cls(snapshot["job_id"], argv[4])


@dataclass
class _ManagedSecurityContainer:
    session: DockerResourceSession
    owner_session_id: str
    host_workdir: Path
    workdir: str


@dataclass
class _ReusableSecurityScope:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    container_name: str = ""
    callers: int = 0


class SecurityExecutionEnvironmentService:
    """Run security commands in a configured execution environment."""

    def __init__(
        self,
        *,
        settings: Any = None,
        workspace_root: Path | str | None = None,
        docker_resources=None,
        tool_jobs: ToolJobService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._workspace_root = Path(workspace_root or Path.cwd())
        self._docker = docker_resources or DockerResourceManager(self._settings)
        self._background_jobs = tool_jobs or ToolJobService(PostgresToolJobStore(self._settings))
        self._detached_jobs: dict[str, _DetachedJobRecord] = {}
        self._managed_containers: dict[str, _ManagedSecurityContainer] = {}
        self._managed_sessions: set[DockerResourceSession] = set()
        self._reusable_scopes: dict[tuple[str, ...], _ReusableSecurityScope] = {}
        self._closed = False

    def set_tool_job_service(self, service: ToolJobService) -> None:
        self._background_jobs = service

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
        if self.owns_foreground_docker_lifecycle():
            bundle = getattr(context, "context_bundle", None) or {}
            with bind_request_context(session_id=getattr(context, "session_id", None),
                turn_id=getattr(context, "turn_id", None), trace_id=getattr(context, "trace_id", None),
                project_id=bundle.get("project_id")):
                if self._cleanup_after_run_enabled():
                    result = await self._run_in_docker(command, command_args, timeout_seconds, artifact_dir, context)
                else:
                    result = await self._run_reusable_in_docker(command, command_args, timeout_seconds, artifact_dir, context)
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

        The owned supervisor runs the command independently of client polling.
        Partial output remains in the mounted log; ToolJob stores the result.
        """
        self._enforce_target_allowlist(command_args)
        backend = self._get_str("security_runner_backend", "SECURITY_RUNNER_BACKEND", "local").lower()
        if backend not in {"docker", "container"}:
            raise ValueError("Detached security execution requires the Docker runner backend.")
        command, command_args = self._rewrite_localhost_for_docker(command, command_args)
        bundle = getattr(context, "context_bundle", None) or {}
        with bind_request_context(session_id=getattr(context, "session_id", None),
            turn_id=getattr(context, "turn_id", None), trace_id=getattr(context, "trace_id", None),
            project_id=bundle.get("project_id")):
            # Public views must not expose the supervisor's owned mutable state.
            return deepcopy(await self._start_detached_in_docker(command, command_args, timeout_seconds, artifact_dir, context))

    async def poll_execution(self, job_id: str, *, context: Any = None) -> SecurityCommandExecutionResult:
        """Read output without granting execution or settlement authority.

        ``True`` means this worker still supervises the command; ``False``
        requires an observed successful return, proven non-dispatch or exact
        container absence.
        ``None`` means its external state is unknown, not permission to retry.
        None of these states proves that past external effects were undone.
        """
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("Detached security execution job_id is required.")
        job = await self._background_jobs.get_job(normalized_job_id)
        if job is None or job.tool_key != "security-background-command":
            raise KeyError(f"Unknown detached security execution job: {normalized_job_id}")
        if job.session_id != self._owner_session_id(context):
            raise ValueError("Detached security job does not belong to this session")
        identity = _DetachedExecutionIdentity(normalized_job_id, job.metadata.get("container_id", ""))
        origin_identity = _DetachedExecutionIdentity.from_snapshot(job.metadata.get("initial_result"))
        record = self._detached_jobs.get(normalized_job_id)
        if record is not None and record.result.is_running and job.status == ToolJobStatus.running:
            result = replace(record.result, stdout=await asyncio.to_thread(record.log_path.read_text,
                encoding="utf-8", errors="replace"), completed_at=_utc_now())
            active_identity = record.identity if (
                not record.stopping and record.container.session.active
                and record.task is not None and not record.task.done()
            ) else None
            return deepcopy(await self._observe_detached_execution(result, identity=identity,
                origin_identity=origin_identity, supervised_identity=active_identity))
        payload = job.output_payload or job.metadata.get("initial_result", {})
        if not payload:
            raise RuntimeError("Detached security job has no execution snapshot")
        result = SecurityCommandExecutionResult(**{**payload,
            "started_at": ensure_utc_datetime(payload["started_at"]),
            "completed_at": ensure_utc_datetime(payload["completed_at"])})
        if job.status not in {ToolJobStatus.queued, ToolJobStatus.running} and result.is_running:
            # A dead Worker may leave only its initial snapshot. Recovery must
            # never turn that into a successful result or repeat the command.
            result = replace(result, is_running=None, exit_code=255,
                stderr=job.error_message or f"Background job requires recovery: {job.status.value}")
        return deepcopy(await self._observe_detached_execution(result, identity=identity, origin_identity=origin_identity))

    async def _observe_detached_execution(
        self,
        result: SecurityCommandExecutionResult,
        *,
        identity: _DetachedExecutionIdentity,
        origin_identity: _DetachedExecutionIdentity | None,
        supervised_identity: _DetachedExecutionIdentity | None = None,
    ) -> SecurityCommandExecutionResult:
        """One projection for live, recovered and historical command snapshots."""
        fields = {"job_id": identity.job_id, "container_id": identity.container_id}
        material = _DetachedExecutionIdentity.from_result(result)
        if material is None or origin_identity is None:
            reason = "execution_identity_unavailable"
        elif material != identity or origin_identity != identity or (
            supervised_identity is not None and supervised_identity != identity
        ):
            reason = "execution_identity_mismatch"
        else:
            if result.is_running is False and result.execution_observation in {
                "command_returned", "container_absent", "not_dispatched",
            }:
                return result
            if supervised_identity is not None:
                return replace(result, is_running=True, execution_observation="supervised")
            try:
                # Same read-only absence check as ToolBootstrapService. A live
                # container alone cannot tell us whether this command is live.
                code, stdout, _ = await self._docker._command(
                    ["ps", "--all", "--no-trunc", "--filter", f"id={identity.container_id}", "--format", "{{.ID}}"],
                    timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
                if code == 0 and not stdout.strip():
                    logger.info("security_background_container_absence_observed", extra=fields)
                    return replace(result, is_running=False, execution_observation="container_absent")
                reason = "container_present" if code == 0 and stdout.strip() == identity.container_id else "target_query_unconfirmed"
                fields["query_exit_code"] = code
            except Exception as exc:
                reason = "target_query_failed"
                fields["error_type"] = type(exc).__name__
        logger.warning("security_background_state_unknown", extra={**fields, "reason": reason})
        return replace(result, is_running=None, execution_observation="unknown")

    async def put_file(
        self, *, local_path: Path | str, container_path: str, artifact_dir: Path,
        context: Any = None, container_name: str = "",
    ) -> str:
        """Upload into an owned container, creating a leased one if needed."""
        self._require_file_backend()
        source = Path(local_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Security runner input file does not exist: {source}")
        record = self._require_managed_container(container_name, context) if container_name else None
        workdir = record.workdir if record else self._get_str(
            "security_runner_docker_workdir", "SECURITY_RUNNER_DOCKER_WORKDIR", "/work")
        destination = self._validated_container_path(container_path, workdir)
        created = record is None
        if created:
            container_name = await self._start_managed_container(
                artifact_dir / "_security_transfer" / uuid4().hex[:12], context)
            record = self._require_managed_container(container_name, context)
        try:
            container_id = record.session.container.container_id
            for args in (["exec", container_id, "mkdir", "-p", str(PurePosixPath(destination).parent)],
                         ["cp", str(source), f"{container_id}:{destination}"]):
                code, _, stderr = await record.session.command(args,
                    timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
                if code != 0:
                    raise RuntimeError(f"Security runner file upload failed: {stderr}")
            logger.info("security_file_uploaded", extra={"session_id": record.owner_session_id,
                "container_id": container_id})
            return container_name
        except BaseException as exc:
            logger.warning("security_file_upload_failed", extra={"session_id": record.owner_session_id,
                "error_type": type(exc).__name__, "container_id": record.session.container.container_id})
            # A failed first upload never returns its handle, so nobody else can
            # close it. Existing caller-owned sessions retain normal cp errors.
            if created:
                await record.session.close()
            raise

    async def get_file(
        self, *, container_path: str, local_path: Path | str, artifact_dir: Path,
        context: Any = None, container_name: str = "",
    ) -> Path:
        """Download from an explicitly owned container into artifact_dir."""
        self._require_file_backend()
        artifact_root = artifact_dir.expanduser().resolve()
        destination = Path(local_path).expanduser()
        if not destination.is_absolute():
            destination = artifact_root / destination
        destination = destination.resolve()
        if destination != artifact_root and artifact_root not in destination.parents:
            raise ValueError("Security runner output path must stay inside artifact_dir.")
        if not container_name:
            raise ValueError("Security file download requires the upload or execution container handle")
        record = self._require_managed_container(container_name, context)
        source = self._validated_container_path(container_path, record.workdir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            code, _, stderr = await record.session.command(
                ["cp", f"{record.session.container.container_id}:{source}", str(destination)],
                timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
            if code != 0:
                raise RuntimeError(f"Security runner file download failed: {stderr}")
            logger.info("security_file_downloaded", extra={"session_id": record.owner_session_id,
                "container_id": record.session.container.container_id})
            return destination
        except BaseException as exc:
            logger.warning("security_file_download_failed", extra={"session_id": record.owner_session_id,
                "error_type": type(exc).__name__, "container_id": record.session.container.container_id})
            raise

    def _require_file_backend(self) -> None:
        backend = self._get_str("security_runner_backend", "SECURITY_RUNNER_BACKEND", "local").lower()
        if backend not in {"docker", "container"}:
            raise ValueError("Security runner file transfer requires the Docker runner backend.")

    async def cleanup_container(self, container_name: str, *, context: Any = None) -> None:
        """Close only a durably owned container, including from another Worker."""
        normalized_name = str(container_name or "").strip()
        if not normalized_name:
            return
        await self._docker.stop_for_session(normalized_name, session_id=self._owner_session_id(context))
        record = self._managed_containers.get(normalized_name)
        if record is not None:
            await record.session.close()

    async def create_persistent_container(
        self,
        *,
        campaign_id: str,
        artifact_dir: Path,
        context: Any = None,
    ) -> str:
        """Create one leased container; callers retain its handle across commands."""
        if self._closed:
            raise RuntimeError("Security execution environment is closed")
        if not str(campaign_id or "").strip():
            raise ValueError("Persistent security container requires campaign_id.")
        name = await self._start_managed_container(artifact_dir / "_security_attack_session_work", context)
        logger.info("security_persistent_container_ready", extra={"session_id": self._owner_session_id(context),
            "campaign_id": campaign_id, "container_id": self._managed_containers[name].session.container.container_id})
        return name

    async def _start_managed_container(self, host_workdir: Path, context: Any) -> str:
        if self._closed:
            raise RuntimeError("Security execution environment is closed")
        owner = self._owner_session_id(context)
        if not owner:
            raise ValueError("Persistent security container requires a session owner")
        host_workdir.mkdir(parents=True, exist_ok=True)
        workdir, create_args = self._managed_create_args(host_workdir)
        lifetime = float(self._get_str("security_attack_session_timeout_seconds", "SECURITY_ATTACK_SESSION_TIMEOUT_SECONDS", "900"))
        if lifetime <= 0:
            raise ValueError("Security container lifetime must be positive")
        session = DockerResourceSession(self._docker,
            command_timeout=self._settings.orchestration.resource_docker_command_timeout_seconds,
            on_closed=self._managed_session_closed)
        self._managed_sessions.add(session)
        bundle = getattr(context, "context_bundle", None) or {}
        try:
            with bind_request_context(session_id=owner, turn_id=getattr(context, "turn_id", None),
                trace_id=getattr(context, "trace_id", None), project_id=bundle.get("project_id")):
                await session.start(create_args, name_prefix="qa-security-session", lifetime_seconds=lifetime)
            name = session.container.container_name
            self._managed_containers[name] = _ManagedSecurityContainer(session, owner, host_workdir, workdir)
            logger.info("security_managed_container_ready", extra={"session_id": owner,
                "container_id": session.container.container_id})
            return name
        except BaseException:
            self._managed_sessions.discard(session)
            await session.close()
            raise

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
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")
        return await self._execute_managed_command(container_name=container_name, command=command,
            timeout_seconds=timeout_seconds, context=context)

    async def _execute_managed_command(self, *, container_name: str, command: str,
                                       timeout_seconds: float, context: Any) -> SecurityCommandExecutionResult:
        """Execute after the public entry point validates and translates the target."""
        record = self._require_managed_container(container_name, context)
        started_at = _utc_now()
        args = ["exec", "-w", record.workdir, record.session.container.container_id, "sh", "-lc",
                self._wrap_with_shell_timeout(command, timeout_seconds)]
        logger.info("security_persistent_command_started", extra={"session_id": record.owner_session_id,
            "container_id": record.session.container.container_id})
        try:
            code, stdout, stderr = await record.session.command(args, timeout=timeout_seconds + 5)
            timed_out = code == 124
            if timed_out:
                await record.session.close()
        except DockerCommandTimeout as exc:
            code, stdout, stderr, timed_out = -1, exc.stdout, exc.stderr, True
        logger.info("security_persistent_command_completed", extra={"session_id": record.owner_session_id,
            "container_id": record.session.container.container_id, "exit_code": code, "timed_out": timed_out})
        return SecurityCommandExecutionResult(backend="docker", command=command, argv=["docker", *args],
            cwd=record.workdir, stdout=stdout, stderr=stderr, exit_code=code, timed_out=timed_out,
            started_at=started_at, completed_at=_utc_now(), container_name=container_name,
            output_artifacts=self._collect_output_artifacts(record.host_workdir))

    async def container_heartbeat(self, container_name: str, *, context: Any = None) -> bool:
        """Return whether a campaign container is still running."""
        record = self._managed_containers.get(container_name)
        if record is None:
            return False
        record = self._require_managed_container(container_name, context)
        code, stdout, _ = await record.session.command(
            ["inspect", "--format", "{{.State.Running}}", record.session.container.container_id],
            timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
        return code == 0 and stdout.strip() == "true"

    @staticmethod
    def _owner_session_id(context) -> str:
        return str(getattr(context, "session_id", None) or getattr(get_request_context(), "session_id", None) or "")

    def _require_managed_container(self, name, context) -> _ManagedSecurityContainer:
        record = self._managed_containers.get(name)
        if record is None or not record.session.active:
            raise RuntimeError("Security container has no active supervisor on this worker")
        if record.owner_session_id != self._owner_session_id(context):
            raise ValueError("Security container does not belong to this session")
        return record

    def _managed_session_closed(self, session) -> None:
        self._managed_sessions.discard(session)
        if session.container is not None:
            self._managed_containers.pop(session.container.container_name, None)
            for key, scope in list(self._reusable_scopes.items()):
                if scope.container_name == session.container.container_name and scope.callers == 0:
                    self._reusable_scopes.pop(key, None)

    async def close(self) -> None:
        self._closed = True
        records = list(self._detached_jobs.values())
        tasks = [record.task for record in records if record.task is not None]
        for record in records:
            if record.task is not None and not record.task.done() and not record.stopping:
                record.stopping = True
                record.task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
            for record in records:
                if record.task is not None and record.task.cancelled():
                    # A cancelled task may never have started, or may have been
                    # interrupted again during cleanup. Only the former proves
                    # non-dispatch; the latter still needs target observation.
                    await record.container.session.close()
                    result = replace(record.result, is_running=False if not record.operation_started else None,
                        execution_observation="not_dispatched" if not record.operation_started else "unknown",
                        exit_code=130, stderr="Background supervisor stopped", completed_at=_utc_now())
                    result = await self._observe_detached_execution(result,
                        identity=record.identity, origin_identity=record.identity)
                    await self._background_jobs.finish_execution(record.identity.job_id, status=ToolJobStatus.cancelled,
                        summary=result.stderr, error_message=result.stderr, output_payload=asdict(result))
        finally:
            await asyncio.gather(*(session.close() for session in list(self._managed_sessions)))

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

    def owns_foreground_docker_lifecycle(self) -> bool:
        """Whether this configured foreground path acquires its own Docker slot.

        TestRun uses the same decision to avoid reserving capacity twice.
        Temporary and reusable foreground containers both own their admission.
        """
        backend = self._get_str("security_runner_backend", "SECURITY_RUNNER_BACKEND", "local").lower()
        return backend in {"docker", "container"}

    async def _run_in_docker(
        self,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any,
    ) -> SecurityCommandExecutionResult:
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")
        docker = shutil.which("docker")
        if docker is None:
            raise FileNotFoundError("Docker CLI is not installed or not on PATH.")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        workdir, create_args = self._managed_create_args(artifact_dir)

        async def execute(container):
            code, _, stderr = await self._docker._command(["start", container.container_id],
                timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
            if code != 0:
                raise RuntimeError(f"Security runner start failed: {stderr}")
            started_at = _utc_now()
            args = ["exec", "-w", workdir, container.container_id, "sh", "-lc",
                    self._wrap_with_shell_timeout(command, timeout_seconds)]
            try:
                code, stdout, stderr = await self._docker._command(args, timeout=timeout_seconds + 5)
                timed_out = code == 124
            except DockerCommandTimeout as exc:
                code, stdout, stderr, timed_out = -1, exc.stdout, exc.stderr, True
            return SecurityCommandExecutionResult(
                backend="docker", command=command, argv=[docker, *args], cwd=workdir,
                stdout=stdout, stderr=stderr, exit_code=code, timed_out=timed_out,
                started_at=started_at, completed_at=_utc_now(), container_name=container.container_name,
            )

        result = await self._docker.run(create_args, name_prefix=self._container_name(context), operation=execute)
        result.output_artifacts = self._collect_output_artifacts(artifact_dir)
        return result

    def _managed_create_args(self, host_workdir: Path) -> tuple[str, list[str]]:
        image = self._get_str("security_runner_docker_image", "SECURITY_RUNNER_DOCKER_IMAGE", "vxcontrol/kali-linux")
        workdir = self._get_str("security_runner_docker_workdir", "SECURITY_RUNNER_DOCKER_WORKDIR", "/work")
        policy = self._get_str("security_runner_docker_pull_policy", "SECURITY_RUNNER_DOCKER_PULL_POLICY", "never").lower()
        args = ["--workdir", workdir, "--pull", policy, "-v", f"{host_workdir.resolve()}:{workdir}"]
        if self._get_bool("security_runner_docker_net_raw", "SECURITY_RUNNER_DOCKER_NET_RAW", True):
            args.extend(["--cap-add", "NET_RAW"])
        if self._get_bool("security_runner_docker_net_admin", "SECURITY_RUNNER_DOCKER_NET_ADMIN", False):
            args.extend(["--cap-add", "NET_ADMIN"])
        network = self._get_str("security_runner_docker_network", "SECURITY_RUNNER_DOCKER_NETWORK", "")
        if network:
            args.extend(["--network", network])
        return workdir, [*args, image, "tail", "-f", "/dev/null"]

    async def _run_reusable_in_docker(
        self,
        command: str,
        command_args: list[str],
        timeout_seconds: float,
        artifact_dir: Path,
        context: Any,
    ) -> SecurityCommandExecutionResult:
        # The existing DockerResourceSession owns the whole lifetime. Keep only
        # a scoped handle here; unrelated Attempts never share it or its mount.
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")
        current = get_request_context()
        key = tuple(str(getattr(current, name, None) or "") for name in
            ("project_id", "session_id", "turn_id", "run_id", "run_item_id", "attempt_id"))
        reuse = self._container_reuse_enabled()
        scope = self._reusable_scopes.setdefault(key, _ReusableSecurityScope()) if reuse else _ReusableSecurityScope()
        scope.callers += 1
        try:
            # Include creation in serialization so concurrent calls cannot both
            # allocate for one scope. Other scopes have independent locks.
            async with scope.lock:
                if self._closed:
                    raise RuntimeError("Security execution environment is closed")
                record = self._managed_containers.get(scope.container_name)
                if record is None or not record.session.active:
                    workdir = artifact_dir / "_security_runner_work" / uuid4().hex[:12]
                    scope.container_name = await self._start_managed_container(workdir, context)
                else:
                    logger.info("security_container_reused", extra={"session_id": record.owner_session_id,
                        "container_id": record.session.container.container_id,
                        "attempt_id": getattr(current, "attempt_id", None)})
                return await self._execute_managed_command(container_name=scope.container_name,
                    command=command, timeout_seconds=timeout_seconds, context=context)
        finally:
            scope.callers -= 1
            if reuse and scope.callers == 0 and scope.container_name not in self._managed_containers:
                self._reusable_scopes.pop(key, None)

    async def _start_detached_in_docker(
        self, command: str, command_args: list[str], timeout_seconds: float,
        artifact_dir: Path, context: Any,
    ) -> SecurityCommandExecutionResult:
        if not command_args:
            raise ValueError("Security command rendered an empty argv.")
        interval = float(self._get_str("security_runner_detach_poll_interval_seconds",
            "SECURITY_RUNNER_DETACH_POLL_INTERVAL_SECONDS", "3"))
        if interval <= 0 or timeout_seconds <= 0:
            raise ValueError("Background command timeout and supervision interval must be positive")
        stale_seconds = float(getattr(self._settings.orchestration, "tool_job_heartbeat_timeout_seconds", 90))
        interval = min(interval, max(.1, stale_seconds / 3))
        # Detached commands always own a container: sharing one would let one
        # completion or timeout destroy another command's execution environment.
        name = await self._start_managed_container(artifact_dir / "_security_background" / uuid4().hex[:12], context)
        container = self._managed_containers[name]
        job = None
        try:
            correlation = get_request_context()
            job = await self._background_jobs.create_job(
                ToolDescriptor(key="security-background-command", name="Security background command",
                    description="Supervised Docker command", category="security"),
                call_id=f"security-{uuid4().hex}", session_id=container.owner_session_id,
                turn_id=getattr(correlation, "turn_id", None) or "",
                trace_id=getattr(correlation, "trace_id", None) or "",
                input_payload={"command": command, "timeout_seconds": timeout_seconds},
                metadata={name: getattr(correlation, name, None) for name in
                    ("request_id", "project_id", "run_id", "run_item_id", "attempt_id", "worker_id")},
            )
            log_path = container.host_workdir / ".jobs" / f"{job.id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch()
            command_log = str(PurePosixPath(container.workdir) / ".jobs" / f"{job.id}.log")
            # Use an attached exec owned by the background coroutine, not a
            # daemonized process whose lifecycle depends on polling a PID file.
            script = f"(\n{self._wrap_with_shell_timeout(command, timeout_seconds)}\n) > {shlex.quote(command_log)} 2>&1"
            args = ["exec", "-w", container.workdir, container.session.container.container_id, "sh", "-lc", script]
            result = SecurityCommandExecutionResult(backend="docker", command=command, argv=["docker", *args],
                cwd=container.workdir, stdout="", stderr="", exit_code=-1, timed_out=False,
                started_at=_utc_now(), completed_at=_utc_now(), container_name=name,
                job_id=job.id, detached=True, is_running=True, execution_observation="supervised")
            job.metadata["initial_result"] = asdict(result)
            job.metadata["container_id"] = container.session.container.container_id
            await self._background_jobs.mark_running(job)
            record = _DetachedJobRecord(result, container, log_path, self._cleanup_after_run_enabled(),
                _DetachedExecutionIdentity(job.id, container.session.container.container_id))
            self._detached_jobs[job.id] = record
            record.task = asyncio.create_task(self._supervise_detached(record, args, timeout_seconds, interval))
            record.task.add_done_callback(lambda task: self._detached_finished(job.id, task))
            logger.info("security_background_started", extra={"job_id": job.id,
                "session_id": container.owner_session_id, "container_id": container.session.container.container_id})
            return result
        except BaseException:
            await container.session.close()
            if job is not None:
                await self._background_jobs.mark_failed(job.id, summary="Background command did not start",
                    error_message="background_start_failed")
            raise

    async def _supervise_detached(self, record, args, timeout_seconds, interval) -> None:
        record.operation_started = True
        operation = asyncio.create_task(record.container.session.command(args, timeout=timeout_seconds + 5))
        code, stderr, timed_out, cancelled = 255, "", False, False
        successful_return = False
        try:
            try:
                while True:
                    done, _ = await asyncio.wait({operation}, timeout=interval)
                    if done:
                        break
                    job = await self._background_jobs.heartbeat_execution(record.identity.job_id)
                    if job is None:
                        job = await self._background_jobs.get_job(record.identity.job_id)
                    if job is None:
                        raise RuntimeError("Background ToolJob disappeared")
                    if job.status == ToolJobStatus.cancelled:
                        raise asyncio.CancelledError()
                    if job.status != ToolJobStatus.running:
                        raise RuntimeError("Background ToolJob is no longer running")
                code, _, stderr = await operation
                timed_out = code == 124
                # Nonzero CLI exits may also mean a transport failure. Keep
                # the original result, but require target absence for those.
                successful_return = code == 0
            except DockerCommandTimeout as exc:
                code, stderr, timed_out = -1, exc.stderr, True
            except asyncio.CancelledError:
                code, stderr, cancelled = 130, "Background command cancelled", True
            except Exception as exc:
                stderr = f"Background execution stopped: {type(exc).__name__}"
                logger.error("security_background_execution_failed", extra={
                    "job_id": record.identity.job_id, "error_type": type(exc).__name__})
            finally:
                record.stopping = True
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            if record.cleanup_after_run or code != 0 or timed_out:
                await record.container.session.close()
            stdout = await asyncio.to_thread(record.log_path.read_text, encoding="utf-8", errors="replace")
            record.result = replace(record.result, stdout=stdout, stderr=stderr, exit_code=code,
                timed_out=timed_out, is_running=False if successful_return else None,
                execution_observation="command_returned" if successful_return else "unknown", completed_at=_utc_now(),
                output_artifacts=[*self._collect_output_artifacts(record.container.host_workdir),
                    {"type": "security_runner_log", "label": record.log_path.name, "path": str(record.log_path)}])
            record.result = await self._observe_detached_execution(record.result,
                identity=record.identity, origin_identity=record.identity)
            payload = asdict(record.result)
            status = (ToolJobStatus.cancelled if cancelled else
                ToolJobStatus.completed if code == 0 and not timed_out else ToolJobStatus.failed)
            saved = await self._background_jobs.finish_execution(record.identity.job_id, status=status,
                summary=f"Background command {status.value}", output_payload=payload,
                error_message=None if status == ToolJobStatus.completed else stderr or f"exit_code={code}")
            if saved is None:
                # Another instance may cancel after the last heartbeat but before
                # settlement. Preserve that decision, stop the container, and
                # save a cancelled snapshot only if cancellation still owns it.
                code = 130
                cancelled_result = replace(record.result, exit_code=130, stderr="Background settlement superseded")
                await self._background_jobs.finish_execution(record.identity.job_id, status=ToolJobStatus.cancelled,
                    summary="Background cancellation preserved", output_payload=asdict(cancelled_result),
                    error_message=cancelled_result.stderr)
                logger.warning("security_background_settlement_superseded", extra={"job_id": record.identity.job_id})
            logger.info("security_background_completed", extra={"job_id": record.identity.job_id,
                "exit_code": code, "timed_out": timed_out, "cancelled": cancelled})
        except Exception as exc:
            # Persistence errors are visible; never publish an in-memory success
            # as a durable result. The existing stale-ToolJob recovery owns this.
            code = 255
            logger.error("security_background_result_uncommitted", extra={
                "job_id": record.identity.job_id, "error_type": type(exc).__name__})
            raise
        finally:
            if record.cleanup_after_run or code != 0 or timed_out:
                await record.container.session.close()

    def _detached_finished(self, job_id: str, task: asyncio.Task) -> None:
        self._detached_jobs.pop(job_id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("security_background_supervisor_failed", extra={
                "job_id": job_id, "error_type": type(task.exception()).__name__})


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

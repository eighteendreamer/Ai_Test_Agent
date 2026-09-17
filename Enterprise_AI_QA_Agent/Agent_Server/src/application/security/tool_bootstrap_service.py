"""P4 approval-scoped temporary security-tool readiness service.

This service intentionally does not expose arbitrary shell or package-manager
arguments.  It validates one server-owned installation plan, probes the tool
inside a short-lived container, optionally executes one fixed APT template,
then always destroys the container and records an auditable manifest.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.config import get_settings
from src.runtime.docker_resource_manager import DockerCommandTimeout, DockerResourceManager
from src.runtime.resource_lease_manager import ResourceUnavailable

logger = logging.getLogger("uvicorn.error.security_testing_mode.tool_bootstrap")


@dataclass(frozen=True)
class ToolBootstrapPlan:
    tool_name: str
    package_name: str
    executable: str
    profile_keys: tuple[str, ...]
    repository_id: str = "kali-rolling"
    command_template_id: str = "apt-get-v1"


@dataclass
class ToolBootstrapManifest:
    bootstrap_id: str
    campaign_id: str
    target_allowlist: list[str]
    profile_key: str
    tool_name: str
    package_name: str
    requested_version: str
    image_ref: str
    repository_id: str
    network_name: str
    approval_scope_hash: str
    status: str = "requested"
    resolved_version: str = ""
    image_digest: str = ""
    container_name: str = ""
    command_template_id: str = ""
    readiness_command: str = ""
    install_command: str = ""
    profile_command: str = ""
    readiness_exit_code: int | None = None
    install_exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    failure_category: str = ""
    failure_reason: str = ""
    created_at: str = field(default_factory=lambda: _utc_now().isoformat())
    completed_at: str = ""
    cleanup_complete: bool = False
    manifest_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolBootstrapService:
    """Install an allowlisted missing tool only inside a fresh Docker runner."""

    # Server-owned repository definitions.  An allowlisted repository ID is
    # not enough on its own because a base image may include unrelated APT
    # source parts.  P4 writes only these fixed source lines into the
    # temporary container and disables the image's sourceparts directory.
    _REPOSITORY_SOURCE_LINES = {
        "kali-rolling": (
            "deb https://kali.download/kali kali-rolling main contrib non-free non-free-firmware",
        ),
    }

    _PLANS = {
        "tcpdump": ToolBootstrapPlan(
            tool_name="tcpdump",
            package_name="tcpdump",
            executable="tcpdump",
            profile_keys=("tcpdump_timed_capture",),
        ),
        "searchsploit": ToolBootstrapPlan(
            tool_name="searchsploit",
            package_name="exploitdb",
            executable="searchsploit",
            profile_keys=("searchsploit_lookup", "searchsploit_exploit_lookup"),
        ),
        "msfconsole": ToolBootstrapPlan(
            tool_name="msfconsole",
            package_name="metasploit-framework",
            executable="msfconsole",
            profile_keys=("msf_module_info",),
        ),
    }

    def __init__(self, *, settings: Any = None, workspace_root: Path | str | None = None,
                 docker_resources=None) -> None:
        self._settings = settings or get_settings()
        self._workspace_root = Path(workspace_root or Path.cwd())
        self._docker = docker_resources or DockerResourceManager(self._settings)

    def is_enabled(self) -> bool:
        return self._get_bool(
            "security_tool_bootstrap_enabled",
            "SECURITY_TOOL_BOOTSTRAP_ENABLED",
            False,
        )

    def resolve_plan(self, *, profile_key: str, tool_name: str = "") -> ToolBootstrapPlan | None:
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool:
            plan = self._PLANS.get(normalized_tool)
            if plan is not None and profile_key in plan.profile_keys:
                return plan
            return None
        for plan in self._PLANS.values():
            if profile_key in plan.profile_keys:
                return plan
        return None

    def build_approval_arguments(
        self,
        *,
        campaign_id: str,
        target_allowlist: list[str],
        profile_key: str,
        plan: ToolBootstrapPlan,
        requested_version: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        image_ref = self._image_ref()
        return {
            "bootstrap_mode": "security_tool_bootstrap",
            "campaign_id": str(campaign_id or "").strip(),
            "target_allowlist": sorted({str(item).strip() for item in target_allowlist if str(item).strip()}),
            "profile_key": profile_key,
            "tool_name": plan.tool_name,
            "package_name": plan.package_name,
            "requested_version": str(requested_version or "").strip(),
            "image_ref": image_ref,
            "repository_id": plan.repository_id,
            "network_name": self._network_name(),
            "command_template_id": plan.command_template_id,
            "timeout_seconds": int(max(1, min(timeout_seconds, 1800))),
        }

    async def run(
        self,
        *,
        campaign_id: str,
        target_allowlist: list[str],
        profile_key: str,
        tool_name: str,
        requested_version: str = "",
        approval_scope_hash: str = "",
        approval_granted: bool = False,
        artifact_dir: Path,
        timeout_seconds: float | None = None,
    ) -> ToolBootstrapManifest:
        plan = self.resolve_plan(profile_key=profile_key, tool_name=tool_name)
        bootstrap_id = f"bootstrap_{uuid4().hex[:20]}"
        manifest = ToolBootstrapManifest(
            bootstrap_id=bootstrap_id,
            campaign_id=str(campaign_id or "").strip(),
            target_allowlist=[str(item).strip() for item in target_allowlist if str(item).strip()],
            profile_key=str(profile_key or "").strip(),
            tool_name=str(tool_name or "").strip(),
            package_name=plan.package_name if plan else "",
            requested_version=str(requested_version or "").strip(),
            image_ref=self._image_ref(),
            repository_id=plan.repository_id if plan else "",
            network_name=self._network_name(),
            approval_scope_hash=str(approval_scope_hash or "").strip(),
            command_template_id=plan.command_template_id if plan else "",
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        manifest.manifest_path = str(artifact_dir / f"{bootstrap_id}.json")
        self._persist_manifest(manifest)

        validation_error = self._validate_request(manifest=manifest, plan=plan)
        if validation_error:
            manifest.status = "failed"
            manifest.failure_category = validation_error
            manifest.failure_reason = _failure_message(validation_error)
            manifest.completed_at = _utc_now().isoformat()
            self._persist_manifest(manifest)
            self._log("security.tool_bootstrap.rejected", manifest)
            return manifest
        if not approval_granted:
            manifest.status = "waiting_approval"
            manifest.failure_category = "approval_required"
            manifest.failure_reason = "A P4-specific approval is required before temporary tool readiness can run."
            manifest.completed_at = _utc_now().isoformat()
            self._persist_manifest(manifest)
            self._log("security.tool_bootstrap.approval_required", manifest)
            return manifest

        effective_timeout = self._resolve_timeout(timeout_seconds)
        docker = shutil.which("docker")
        if docker is None:
            manifest.status = "failed"
            manifest.failure_category = "execution_environment_not_available"
            manifest.failure_reason = "Docker CLI is not installed or not on PATH."
            manifest.completed_at = _utc_now().isoformat()
            self._persist_manifest(manifest)
            self._log("security.tool_bootstrap.failed", manifest)
            return manifest

        manifest.image_digest = await asyncio.to_thread(self._image_digest, docker, manifest.image_ref)
        if not manifest.image_digest:
            manifest.status = "failed"
            manifest.failure_category = "image_digest_unavailable"
            manifest.failure_reason = "The configured bootstrap image has no local immutable RepoDigest."
            manifest.completed_at = _utc_now().isoformat()
            self._persist_manifest(manifest)
            self._log("security.tool_bootstrap.failed", manifest)
            return manifest
        if not self._image_allowed(manifest.image_ref, manifest.image_digest):
            manifest.status = "failed"
            manifest.failure_category = "image_not_allowlisted"
            manifest.failure_reason = "The configured image reference or digest is not allowlisted for P4."
            manifest.completed_at = _utc_now().isoformat()
            self._persist_manifest(manifest)
            self._log("security.tool_bootstrap.rejected", manifest)
            return manifest

        container_id = ""

        async def prepare(container):
            nonlocal container_id
            container_id = container.container_id
            manifest.container_name = container.container_name
            self._persist_manifest(manifest)
            self._log("security.tool_bootstrap.started", manifest)
            code, _, stderr = await self._docker._command(["start", container_id],
                timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
            if code != 0:
                raise RuntimeError(f"Failed to start tool bootstrap container: {stderr}")
            manifest.readiness_command = self._readiness_command(plan)
            ready = await self._exec(
                container_id,
                manifest.readiness_command,
                effective_timeout,
            )
            manifest.readiness_exit_code = ready.returncode
            manifest.stdout = _join_output(manifest.stdout, ready.stdout)
            manifest.stderr = _join_output(manifest.stderr, ready.stderr)
            manifest.resolved_version = _readiness_package_version(ready.stdout)
            if self._ready_for_requested_version(
                exit_code=ready.returncode,
                resolved_version=manifest.resolved_version,
                requested_version=manifest.requested_version,
            ):
                manifest.status = "already_available"
            else:
                manifest.install_command = self._install_command(plan, manifest.requested_version)
                installed = await self._exec(
                    container_id,
                    manifest.install_command,
                    effective_timeout,
                )
                manifest.install_exit_code = installed.returncode
                manifest.stdout = _join_output(manifest.stdout, installed.stdout)
                manifest.stderr = _join_output(manifest.stderr, installed.stderr)
                if installed.returncode != 0:
                    manifest.status = "failed"
                    manifest.failure_category = "tool_bootstrap_failed"
                    manifest.failure_reason = (
                        f"Fixed installation template returned exit code {installed.returncode}."
                    )
                else:
                    verified = await self._exec(
                        container_id,
                        manifest.readiness_command,
                        effective_timeout,
                    )
                    manifest.readiness_exit_code = verified.returncode
                    manifest.stdout = _join_output(manifest.stdout, verified.stdout)
                    manifest.stderr = _join_output(manifest.stderr, verified.stderr)
                    manifest.resolved_version = _readiness_package_version(verified.stdout)
                    if not self._ready_for_requested_version(
                        exit_code=verified.returncode,
                        resolved_version=manifest.resolved_version,
                        requested_version=manifest.requested_version,
                    ):
                        manifest.status = "failed"
                        if verified.returncode != 0:
                            manifest.failure_category = "tool_not_ready_after_install"
                            manifest.failure_reason = (
                                "Tool was not available after the fixed installation template completed."
                            )
                        else:
                            manifest.failure_category = "requested_version_not_ready"
                            manifest.failure_reason = (
                                "The requested package version was not active after the fixed "
                                "installation template completed."
                            )
                    else:
                        manifest.status = "completed"

        try:
            await self._docker.run(self._create_args(manifest),
                name_prefix=self._container_name(manifest.bootstrap_id), operation=prepare)
        except ResourceUnavailable as exc:
            manifest.status = "waiting_resource"
            manifest.failure_category = exc.reason
            manifest.failure_reason = f"Tool bootstrap waiting for resource: {exc.reason}"
        except asyncio.CancelledError:
            manifest.status = "cancelled"
            manifest.failure_category = "execution_cancelled"
            manifest.failure_reason = "Tool bootstrap execution was cancelled."
            raise
        except Exception as exc:
            # This boundary now owns PostgreSQL allocation and Redis lease errors
            # as well as Docker failures. Always persist a terminal manifest;
            # cancellation and capacity waiting have separate branches above.
            logger.error("tool_bootstrap_execution_failed", extra={
                "bootstrap_id": manifest.bootstrap_id, "campaign_id": manifest.campaign_id,
                "error_type": type(exc).__name__,
            })
            manifest.status = "failed"
            manifest.failure_category = "execution_environment_error"
            manifest.failure_reason = str(exc)
            manifest.stderr = _join_output(manifest.stderr, str(exc))
            if isinstance(exc, DockerCommandTimeout):
                manifest.stdout = _join_output(manifest.stdout, exc.stdout)
                manifest.stderr = _join_output(manifest.stderr, exc.stderr)
        finally:
            manifest.cleanup_complete = bool(container_id) and await self._confirmed_removed(container_id)
            if container_id and not manifest.cleanup_complete:
                if manifest.status != "cancelled":
                    manifest.status = "failed"
                manifest.failure_category = manifest.failure_category or "cleanup_failed"
                manifest.failure_reason = f"{manifest.failure_reason}; Container cleanup pending verification.".strip("; ")
            manifest.completed_at = "" if manifest.status == "waiting_resource" else _utc_now().isoformat()
            self._persist_manifest(manifest)
            event = ("completed" if manifest.status in {"already_available", "completed"}
                     else manifest.status if manifest.status in {"waiting_resource", "cancelled"} else "failed")
            self._log(f"security.tool_bootstrap.{event}", manifest)
            self._log("security.tool_bootstrap.cleaned" if manifest.cleanup_complete
                      else "security.tool_bootstrap.cleanup_unconfirmed", manifest)
        return manifest

    def _validate_request(
        self,
        *,
        manifest: ToolBootstrapManifest,
        plan: ToolBootstrapPlan | None,
    ) -> str:
        if not self.is_enabled():
            return "bootstrap_disabled"
        if not self._cleanup_required():
            return "cleanup_policy_required"
        if plan is None:
            return "package_not_allowlisted"
        if not manifest.campaign_id or not manifest.target_allowlist:
            return "invalid_scope"
        if not manifest.approval_scope_hash:
            return "approval_scope_missing"
        if manifest.package_name != plan.package_name:
            return "package_not_allowlisted"
        if not self._package_allowed(plan, manifest.requested_version):
            return "package_not_allowlisted"
        if not self._repository_allowed(plan.repository_id):
            return "network_egress_denied"
        if not self._repository_source_lines(plan.repository_id):
            return "repository_source_not_configured"
        return ""

    def _package_allowed(self, plan: ToolBootstrapPlan, requested_version: str) -> bool:
        """Match the strict P4 package/version allowlist.

        An unversioned entry permits only the server-owned package without an
        explicit version request. Version pinning is opt-in and must name the
        exact requested version (``package@version`` or
        ``tool:package@version``). This prevents a later model/request change
        from silently turning a floating package approval into a pinned install.
        """
        entries = _split_csv(
            self._get_str(
                "security_tool_bootstrap_package_allowlist",
                "SECURITY_TOOL_BOOTSTRAP_PACKAGE_ALLOWLIST",
                "",
            )
        )
        if not entries:
            return False
        requested = str(requested_version or "").strip()
        if requested:
            candidates = {
                f"{plan.package_name}@{requested}",
                f"{plan.tool_name}:{plan.package_name}@{requested}",
            }
        else:
            candidates = {
                plan.package_name,
                f"{plan.tool_name}:{plan.package_name}",
            }
        return any(item in candidates for item in entries)

    def _image_allowed(self, image_ref: str, image_digest: str) -> bool:
        entries = _split_csv(
            self._get_str(
                "security_tool_bootstrap_image_allowlist",
                "SECURITY_TOOL_BOOTSTRAP_IMAGE_ALLOWLIST",
                "",
            )
        )
        return bool(entries) and (image_ref in entries or image_digest in entries)

    def _repository_allowed(self, repository_id: str) -> bool:
        entries = _split_csv(
            self._get_str(
                "security_tool_bootstrap_repository_allowlist",
                "SECURITY_TOOL_BOOTSTRAP_REPOSITORY_ALLOWLIST",
                "",
            )
        )
        return bool(entries) and repository_id in entries

    def _readiness_command(self, plan: ToolBootstrapPlan) -> str:
        executable = shlex.quote(plan.executable)
        package = shlex.quote(plan.package_name)
        return (
            f"set -eu; command -v {executable}; "
            f"dpkg-query -W -f='${{Version}}\\n' {package} 2>/dev/null || true"
        )

    def _install_command(self, plan: ToolBootstrapPlan, requested_version: str) -> str:
        package_spec = plan.package_name
        if requested_version:
            package_spec = f"{package_spec}={requested_version}"
        source_lines = self._repository_source_lines(plan.repository_id)
        source_file = f"/work/.qa-security-p4-{_slug(plan.repository_id)}.list"
        source_setup = " ".join(
            [
                f"source_file={shlex.quote(source_file)};",
                ': > "$source_file";',
                *[
                    f"printf '%s\\n' {shlex.quote(line)} >> \"$source_file\";"
                    for line in source_lines
                ],
            ]
        )
        apt_options = (
            "-o APT::Sandbox::User=root "
            '-o Dir::Etc::sourcelist="$source_file" '
            "-o Dir::Etc::sourceparts=-"
        )
        return (
            f"set -eu; export DEBIAN_FRONTEND=noninteractive; {source_setup} "
            f"apt-get {apt_options} update; "
            f"apt-get {apt_options} install --no-install-recommends -y "
            f"{shlex.quote(package_spec)}"
        )

    @classmethod
    def _repository_source_lines(cls, repository_id: str) -> tuple[str, ...]:
        return tuple(cls._REPOSITORY_SOURCE_LINES.get(str(repository_id or "").strip(), ()))

    def _create_args(self, manifest: ToolBootstrapManifest) -> list[str]:
        return [
            "--network",
            manifest.network_name,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--workdir",
            "/work",
            manifest.image_digest,
            "tail",
            "-f",
            "/dev/null",
        ]

    async def _exec(self, container_id: str, command: str, timeout_seconds: float) -> subprocess.CompletedProcess:
        args = ["exec", "-w", "/work", container_id, "sh", "-lc", command]
        code, stdout, stderr = await self._docker._command(args, timeout=max(2.0, timeout_seconds + 5.0))
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    async def _confirmed_removed(self, container_id: str) -> bool:
        try:
            code, stdout, stderr = await self._docker._command(
                ["ps", "--all", "--no-trunc", "--filter", f"id={container_id}", "--format", "{{.ID}}"],
                timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
            if code == 0 and not stdout.strip():
                return True
            logger.warning("tool_bootstrap_cleanup_unconfirmed", extra={"container_id": container_id,
                "exit_code": code, "error_message": stderr[:2000]})
        except Exception as exc:
            logger.warning("tool_bootstrap_cleanup_check_failed", extra={"container_id": container_id,
                "error_type": type(exc).__name__})
        return False

    def _image_digest(self, docker: str, image_ref: str) -> str:
        completed = subprocess.run(
            [docker, "image", "inspect", image_ref, "--format", "{{json .RepoDigests}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if completed.returncode != 0:
            return ""
        try:
            digests = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError:
            return ""
        if not isinstance(digests, list):
            return ""
        return str(digests[0] or "").strip() if digests else ""

    def _persist_manifest(self, manifest: ToolBootstrapManifest) -> None:
        path = Path(manifest.manifest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _log(self, event: str, manifest: ToolBootstrapManifest) -> None:
        logger.info(
            "%s %s",
            event,
            json.dumps(
                {
                    "bootstrap_id": manifest.bootstrap_id,
                    "campaign_id": manifest.campaign_id,
                    "profile_key": manifest.profile_key,
                    "tool_name": manifest.tool_name,
                    "package_name": manifest.package_name,
                    "requested_version": manifest.requested_version,
                    "image_ref": manifest.image_ref,
                    "image_digest": manifest.image_digest,
                    "repository_id": manifest.repository_id,
                    "network_name": manifest.network_name,
                    "status": manifest.status,
                    "failure_category": manifest.failure_category,
                    "readiness_exit_code": manifest.readiness_exit_code,
                    "install_exit_code": manifest.install_exit_code,
                    "container_name": manifest.container_name,
                    "cleanup_complete": manifest.cleanup_complete,
                    "manifest_path": manifest.manifest_path,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    def _image_ref(self) -> str:
        return self._get_str(
            "security_runner_docker_image",
            "SECURITY_RUNNER_DOCKER_IMAGE",
            "vxcontrol/kali-linux",
        )

    def _network_name(self) -> str:
        return self._get_str(
            "security_runner_docker_network",
            "SECURITY_RUNNER_DOCKER_NETWORK",
            "",
        ) or "none"

    def _container_name(self, bootstrap_id: str) -> str:
        prefix = self._get_str(
            "security_runner_docker_container_prefix",
            "SECURITY_RUNNER_DOCKER_CONTAINER_PREFIX",
            "qa-security-runner",
        )
        return f"{_slug(prefix)}-bootstrap-{bootstrap_id[-12:]}"[:120]

    def _resolve_timeout(self, requested: float | None) -> float:
        configured = self._get_float(
            "security_tool_bootstrap_timeout_seconds",
            "SECURITY_TOOL_BOOTSTRAP_TIMEOUT_SECONDS",
            300.0,
        )
        if requested is None:
            return configured
        return max(1.0, min(float(requested), configured, 1800.0))

    @staticmethod
    def _ready_for_requested_version(
        *,
        exit_code: int,
        resolved_version: str,
        requested_version: str,
    ) -> bool:
        if exit_code != 0:
            return False
        requested = str(requested_version or "").strip()
        return not requested or resolved_version == requested

    def _cleanup_required(self) -> bool:
        return self._get_bool(
            "security_tool_bootstrap_cleanup_required",
            "SECURITY_TOOL_BOOTSTRAP_CLEANUP_REQUIRED",
            True,
        )

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

    def _get_float(self, attr: str, env_name: str, default: float) -> float:
        # Check nested security namespace first (D1a refactoring), then flat attribute.
        value = None
        if self._settings is not None:
            security_ns = getattr(self._settings, "security", None)
            if security_ns is not None:
                value = getattr(security_ns, attr, None)
            if value in (None, ""):
                value = getattr(self._settings, attr, None)
        if value in (None, ""):
            value = os.getenv(env_name)
        try:
            return max(1.0, min(float(value if value not in (None, "") else default), 1800.0))
        except (TypeError, ValueError):
            return default


def _split_csv(value: str) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-_.") or "qa-security-runner"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _join_output(existing: str, next_value: str) -> str:
    next_text = str(next_value or "")
    if not existing:
        return next_text[-24000:]
    if not next_text:
        return existing[-24000:]
    return f"{existing.rstrip()}\n{next_text}"[-24000:]


def _readiness_package_version(value: str) -> str:
    """Read the dpkg version from the fixed readiness probe output.

    The probe prints executable location first and package version second.
    It deliberately returns an empty string when dpkg cannot provide a
    version, so a version-pinned request can never be mistaken for ready.
    """
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return lines[-1][:300] if len(lines) >= 2 else ""


def _failure_message(code: str) -> str:
    return {
        "bootstrap_disabled": "P4 tool bootstrap is disabled by server configuration.",
        "package_not_allowlisted": "Requested tool/package/version is not allowlisted for P4.",
        "invalid_scope": "P4 bootstrap requires a campaign and a non-empty authorized target allowlist.",
        "approval_scope_missing": "P4 bootstrap requires a server-generated dedicated approval scope.",
        "network_egress_denied": "The requested package repository is not allowlisted for P4 egress.",
        "repository_source_not_configured": "The allowlisted P4 repository has no server-owned APT source definition.",
        "cleanup_policy_required": "P4 bootstrap is blocked unless mandatory container cleanup is enabled.",
        "requested_version_not_ready": "The requested package version was not active after installation.",
    }.get(code, code)


__all__ = ["ToolBootstrapManifest", "ToolBootstrapPlan", "ToolBootstrapService"]

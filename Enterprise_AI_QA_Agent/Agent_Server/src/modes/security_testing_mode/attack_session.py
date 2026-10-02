"""Campaign-scoped persistent Docker session for bounded security verification."""
from __future__ import annotations

import shlex
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from src.application.security.execution_environment_service import (
    SecurityCommandExecutionResult,
    SecurityExecutionEnvironmentService,
)


@dataclass
class SecurityShellCommandRecord:
    command_id: str
    command: str
    target: str
    started_at: str
    completed_at: str
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str


@dataclass
class SecurityShellSession:
    """One persistent container bound to one authorized campaign."""

    environment: SecurityExecutionEnvironmentService
    campaign_id: str
    target_allowlist: list[str]
    approval_scope_hash: str
    artifact_dir: Path
    context: Any = None
    timeout_seconds: float = 900.0
    command_timeout_seconds: float = 120.0
    session_id: str = field(default_factory=lambda: f"attack_session_{uuid4().hex[:20]}")
    container_name: str = ""
    status: str = "new"
    created_at: str = ""
    closed_at: str = ""
    close_reason: str = ""
    commands: list[SecurityShellCommandRecord] = field(default_factory=list)

    async def create(self) -> "SecurityShellSession":
        if not self.campaign_id.strip():
            raise ValueError("SecurityShellSession requires campaign_id.")
        if not self.approval_scope_hash.strip():
            raise ValueError("SecurityShellSession requires approval_scope_hash.")
        if not self.target_allowlist:
            raise ValueError("SecurityShellSession requires a non-empty target_allowlist.")
        self.container_name = await self.environment.create_persistent_container(
            campaign_id=self.campaign_id,
            artifact_dir=self.artifact_dir,
            context=self.context,
        )
        self.created_at = _utc_now()
        self.status = "active"
        return self

    async def exec(
        self,
        *,
        command: str,
        target: str,
        timeout_seconds: float | None = None,
    ) -> SecurityCommandExecutionResult:
        self._require_active()
        self._validate_target(target)
        normalized = str(command or "").strip()
        if not normalized:
            raise ValueError("SecurityShellSession command is required.")
        if len(normalized) > 4096:
            raise ValueError("SecurityShellSession command exceeds 4096 characters.")
        self._validate_command(normalized)
        remaining = self._remaining_seconds()
        assigned = min(
            float(timeout_seconds or self.command_timeout_seconds),
            self.command_timeout_seconds,
            remaining,
        )
        if assigned < 1:
            raise TimeoutError("SecurityShellSession total timeout budget is exhausted.")
        result = await self.environment.execute_in_container(
            container_name=self.container_name,
            command=normalized,
            command_args=shlex.split(normalized, posix=True),
            timeout_seconds=assigned,
            artifact_dir=self.artifact_dir,
            context=self.context,
        )
        self.commands.append(
            SecurityShellCommandRecord(
                command_id=f"shell_cmd_{uuid4().hex[:16]}",
                command=normalized,
                target=target,
                started_at=result.started_at.isoformat(),
                completed_at=result.completed_at.isoformat(),
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
        return result

    async def write_stdin(self, data: str) -> None:
        """P1 contract placeholder: stdin is accepted only for detached jobs."""
        self._require_active()
        if str(data or ""):
            raise ValueError("No interactive process is attached to this SecurityShellSession.")

    def read_output(self, cursor: int = 0) -> dict[str, Any]:
        self._require_active(allow_closed=True)
        start = max(0, int(cursor or 0))
        records = self.commands[start:]
        return {
            "cursor": len(self.commands),
            "records": [record.__dict__.copy() for record in records],
        }

    async def put_file(self, *, local_path: Path | str, container_path: str) -> str:
        self._require_active()
        return await self.environment.put_file(
            local_path=local_path,
            container_path=container_path,
            artifact_dir=self.artifact_dir,
            context=self.context,
            container_name=self.container_name,
        )

    async def get_file(self, *, container_path: str, local_path: Path | str) -> Path:
        self._require_active()
        return await self.environment.get_file(
            container_path=container_path,
            local_path=local_path,
            artifact_dir=self.artifact_dir,
            context=self.context,
            container_name=self.container_name,
        )

    async def heartbeat(self) -> bool:
        if self.status != "active" or not self.container_name:
            return False
        return await self.environment.container_heartbeat(self.container_name, context=self.context)

    async def close(self, reason: str = "completed") -> None:
        if self.status == "closed":
            return
        if self.container_name:
            await self.environment.cleanup_container(self.container_name, context=self.context)
        self.status = "closed"
        self.closed_at = _utc_now()
        self.close_reason = str(reason or "completed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "campaign_id": self.campaign_id,
            "container_name": self.container_name,
            "target_allowlist": list(self.target_allowlist),
            "approval_scope_hash": self.approval_scope_hash,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
            "command_count": len(self.commands),
            "commands": [record.__dict__.copy() for record in self.commands],
        }

    def _require_active(self, *, allow_closed: bool = False) -> None:
        valid = {"active", "closed"} if allow_closed else {"active"}
        if self.status not in valid:
            raise RuntimeError(f"SecurityShellSession is not active: {self.status}")

    def _remaining_seconds(self) -> float:
        if not self.created_at:
            return self.timeout_seconds
        created = datetime.fromisoformat(self.created_at)
        elapsed = (datetime.now(timezone.utc) - created).total_seconds()
        return max(0.0, self.timeout_seconds - elapsed)

    def _validate_target(self, target: str) -> None:
        candidate = _target_key(target)
        allowed = {_target_key(item) for item in self.target_allowlist}
        if not candidate or candidate not in allowed:
            raise ValueError(f"SecurityShellSession target is outside the authorized allowlist: {target}")

    def _validate_command(self, command: str) -> None:
        # This substring guard is a heuristic for the runtime's fixed commands,
        # not a shell parser or a container security boundary. Production callers
        # currently supply the three templates in _run_persistent_attack_session;
        # do not expose arbitrary model/user commands under this guard alone.
        denied = (
            "rm -rf",
            "mkfs",
            "shutdown",
            "reboot",
            "poweroff",
            "dd if=",
            "--privileged",
            "docker.sock",
            "curl | sh",
            "wget | sh",
        )
        lowered = command.lower()
        for fragment in denied:
            if fragment in lowered:
                raise ValueError(f"SecurityShellSession command contains denied pattern: {fragment}")
        allowed = {_target_key(item) for item in self.target_allowlist}
        for url in re.findall(r"https?://[^\s'\";|]+", command, flags=re.IGNORECASE):
            if _target_key(url) not in allowed:
                raise ValueError(
                    f"SecurityShellSession command references an out-of-scope target: {url}"
                )


def _target_key(value: str) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    host = (parsed.hostname or text.split(":", 1)[0]).strip("[]").lower()
    port = parsed.port
    if not port:
        if parsed.scheme == "https":
            port = 443
        elif parsed.scheme == "http":
            port = 80
    return f"{host}:{port or ''}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["SecurityShellCommandRecord", "SecurityShellSession"]

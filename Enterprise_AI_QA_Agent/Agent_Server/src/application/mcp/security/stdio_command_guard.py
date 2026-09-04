from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Iterable


class StdioCommandGuardError(ValueError):
    pass


@dataclass(frozen=True)
class StdioCommandGuard:
    allowlist: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings) -> "StdioCommandGuard":
        raw = getattr(settings.orchestration, "mcp_stdio_command_allowlist", []) or []
        return cls(allowlist=tuple(_normalize_executable(item) for item in raw if str(item).strip()))

    def validate(
        self,
        *,
        command: str,
        confirmed_at: str | None,
    ) -> tuple[str, list[str]]:
        executable, args = split_stdio_command(command)
        normalized = _normalize_executable(executable)
        if normalized not in self.allowlist:
            allowed = ", ".join(self.allowlist) or "none"
            raise StdioCommandGuardError(
                f"Stdio MCP command '{normalized}' is not allowed. Allowed commands: {allowed}."
            )
        if not str(confirmed_at or "").strip():
            raise StdioCommandGuardError(
                "Stdio MCP server requires explicit first-use confirmation before spawning a process."
            )
        return executable, args


def split_stdio_command(command: str) -> tuple[str, list[str]]:
    raw = str(command or "").strip()
    if not raw:
        raise StdioCommandGuardError("Stdio MCP command is empty.")
    try:
        parts = shlex.split(raw, posix=False)
    except ValueError as exc:
        raise StdioCommandGuardError(f"Invalid stdio MCP command: {exc}") from exc
    parts = [item.strip().strip('"').strip("'") for item in parts if item.strip()]
    if not parts:
        raise StdioCommandGuardError("Stdio MCP command is empty.")
    return parts[0], parts[1:]


def _normalize_executable(value: object) -> str:
    name = Path(str(value or "").strip().strip('"').strip("'")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name

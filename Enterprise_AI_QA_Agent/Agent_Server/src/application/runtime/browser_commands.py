"""Shared parsing of the existing Python playwright-cli command contract."""
from __future__ import annotations

import logging
import shlex
from typing import Any


logger = logging.getLogger(__name__)


def browser_command_args(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("args") or payload.get("command_args") or payload.get("command") or []
    if isinstance(raw, str):
        return shlex.split(raw, posix=False)
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def strip_browser_global_options(args: list[str]) -> tuple[list[str], bool]:
    stripped, raw = [], False
    for arg in args:
        if arg == "--raw":
            raw = True
        elif arg.startswith("-s="):
            continue
        else:
            stripped.append(arg)
    return stripped, raw


def browser_command_navigation_target(payload: dict[str, Any]) -> str:
    """Read only URL positions that the native runtime uses for navigation."""
    if str(payload.get("action") or "").strip().lower() not in {"command", "playwright-cli", "cli"}:
        return ""
    try:
        args, _ = strip_browser_global_options(browser_command_args(payload))
    except ValueError as exc:
        # The unchanged runtime parser rejects malformed shell quoting before
        # executing a command. Do not guess a target or scan arbitrary script.
        logger.warning("browser_command_target_unparsed", extra={"error_type": type(exc).__name__})
        return ""
    if not args:
        return ""
    verb, rest = args[0].strip().lower(), args[1:]
    if verb in {"open", "explore"}:
        return next((arg for arg in rest if not arg.startswith("--")), "")
    if verb in {"goto", "tab-new"}:
        return rest[0] if rest else ""
    return ""

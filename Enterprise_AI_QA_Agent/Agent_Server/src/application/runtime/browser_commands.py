"""Shared parsing of the existing Python playwright-cli command contract."""
from __future__ import annotations

import logging
import shlex
from typing import Any


logger = logging.getLogger(__name__)


def browser_command_args(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("args") or payload.get("command_args") or payload.get("command") or []
    if isinstance(raw, str):
        args = shlex.split(raw, posix=False)
    elif isinstance(raw, list):
        args = [str(item) for item in raw]
    else:
        return []
    command = payload.get("command")
    if payload.get("args") and isinstance(command, str):
        verb = shlex.split(command, posix=False)
        # The registered tool permits a command verb plus its arguments. Keep
        # legacy complete args (including global flags) without duplicating it.
        plain, _ = strip_browser_global_options(args)
        if len(verb) == 1 and (not plain or plain[0] != verb[0]):
            logger.debug("browser_split_command_normalized", extra={"argument_count": len(args)})
            return [verb[0], *args]
    return args


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

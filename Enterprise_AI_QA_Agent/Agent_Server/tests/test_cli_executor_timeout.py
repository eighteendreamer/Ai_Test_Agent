from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

import src.application.runtime.tool_runtime_service as tool_runtime_module
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
    _run_cli_subprocess,
)


def test_cli_executor_keeps_successful_output(tmp_path: Path) -> None:
    stdout, stderr, exit_code, timed_out = _run_cli_subprocess(
        [sys.executable, "-c", "import sys; print('ready'); print('detail', file=sys.stderr)"],
        tmp_path,
        5,
    )

    assert (stdout.strip(), stderr.strip(), exit_code, timed_out) == (
        "ready", "detail", 0, False,
    )


@pytest.mark.asyncio
async def test_cli_executor_explicit_zero_timeout_uses_configured_minimum(tmp_path: Path, monkeypatch) -> None:
    service = ToolRuntimeService.__new__(ToolRuntimeService)
    service._resolve_cli_cwd = lambda requested: tmp_path
    service._prepare_local_artifact_dir = lambda context, key: tmp_path
    service._build_cli_invocation = lambda shell, command: (sys.executable, ["-c", "pass"])
    captured_timeout: list[float] = []

    def fake_subprocess(command, cwd, timeout_seconds):
        captured_timeout.append(timeout_seconds)
        return "", "", 0, False

    monkeypatch.setattr(tool_runtime_module, "_run_cli_subprocess", fake_subprocess)
    context = ToolExecutionContext(
        session_id="zero-timeout", turn_id="zero-timeout", trace_id="zero-timeout",
        user_message="", normalized_input="", context_bundle={},
    )

    result = await service._run_cli_executor(
        {"command": "no real subprocess", "timeout_seconds": 0}, context,
    )
    await service._run_cli_executor({"command": "no real subprocess"}, context)

    assert result["status"] == "completed"
    assert captured_timeout == [1.0, 20.0]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree regression")
@pytest.mark.asyncio
async def test_cli_executor_timeout_terminates_child_process(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
    )
    service = ToolRuntimeService.__new__(ToolRuntimeService)
    service._resolve_cli_cwd = lambda requested: tmp_path
    service._prepare_local_artifact_dir = lambda context, key: tmp_path
    service._build_cli_invocation = lambda shell, command: (
        sys.executable,
        ["-c", parent_code, str(child_pid_path)],
    )
    context = ToolExecutionContext(
        session_id="timeout-test", turn_id="timeout-test", trace_id="timeout-test",
        user_message="", normalized_input="", context_bundle={},
    )
    child_pid = None
    try:
        result = await service._run_cli_executor(
            {"command": "isolated child process", "timeout_seconds": 1}, context,
        )
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        listing = await asyncio.to_thread(
            subprocess.run,
            ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, check=True,
        )
        assert result["status"] == "partial"
        assert result["error"] == "timeout"
        assert f'"{child_pid}"' not in listing.stdout
    finally:
        if child_pid is None and child_pid_path.exists():
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        if child_pid is not None:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                capture_output=True, text=True, check=False,
            )

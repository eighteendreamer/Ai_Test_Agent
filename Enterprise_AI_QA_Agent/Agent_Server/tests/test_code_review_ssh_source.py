from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.modes.code_review_mode.models import ProjectSource


def _remote_path(path: Path) -> str:
    if os.name != "nt":
        return path.as_posix()
    if not shutil.which("wsl.exe"):
        pytest.skip("WSL is required for the local POSIX shell regression on Windows")
    result = subprocess.run(
        ["wsl.exe", "--", "wslpath", "-a", str(path).replace("\\", "/")],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"No usable WSL POSIX shell: {result.stderr.strip()}")
    return result.stdout.strip()


class _LocalRemoteShell(ToolRuntimeService):
    async def _run_ssh_shell(self, project_source, remote_script, *, timeout_seconds):
        command = ["wsl.exe", "--", "sh"] if os.name == "nt" else ["sh"]
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            input=remote_script,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "ok": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }


@pytest.mark.asyncio
async def test_ssh_project_file_reader_stays_within_declared_root(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "inside.txt").write_text("INSIDE_MARKER\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_MARKER\n", encoding="utf-8")
    remote_root = _remote_path(root)
    remote_outside = _remote_path(outside)
    runtime = _LocalRemoteShell()
    context = ToolExecutionContext(
        session_id="session",
        turn_id="turn",
        trace_id="trace",
        user_message="Read a project file",
        normalized_input="Read a project file",
        context_bundle={},
    )
    source = {"source_type": "ssh", "root_path": remote_root, "ssh": {"host": "localhost"}}

    for path in ("inside.txt", f"{remote_root}/inside.txt"):
        inside = await runtime._run_project_file_reader({"project_source": source, "path": path}, context)
        assert inside["content"] == "INSIDE_MARKER"

    for path in ("../outside.txt", remote_outside):
        escaped = await runtime._run_project_file_reader({"project_source": source, "path": path}, context)
        assert escaped["status"] == "failed"
        assert "within the project root" in escaped["error"]


@pytest.mark.asyncio
async def test_ssh_project_file_reader_rejects_symlink_outside_root(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_MARKER\n", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")
    remote_root = _remote_path(root)
    runtime = _LocalRemoteShell()
    context = ToolExecutionContext(
        session_id="session",
        turn_id="turn",
        trace_id="trace",
        user_message="Read a project file",
        normalized_input="Read a project file",
        context_bundle={},
    )

    escaped = await runtime._run_project_file_reader(
        {"project_source": {"source_type": "ssh", "root_path": remote_root, "ssh": {"host": "localhost"}}, "path": "linked.txt"},
        context,
    )
    assert escaped["status"] == "failed"
    assert "remote_file_outside_project_root" in escaped["error"]


@pytest.mark.asyncio
async def test_ssh_host_is_after_option_terminator():
    class CapturingRuntime(ToolRuntimeService):
        async def _run_subprocess(self, executable, args, *, timeout_seconds, cwd=None):
            self.invocation = (executable, args)
            return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}

    runtime = CapturingRuntime()
    source = ProjectSource(source_type="ssh", root_path="/srv/repo", ssh={"host": "-V"})
    await runtime._run_ssh_shell(source, "true", timeout_seconds=5)

    executable, args = runtime.invocation
    assert executable == "ssh"
    assert args[-3:] == ["--", "-V", "sh -lc true"]

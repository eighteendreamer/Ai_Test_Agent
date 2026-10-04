from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from src.runtime import limited_parser_process as import_process
from src.application.test_cases.import_process import CaseImportProcess, ImportProcessError
from src.schemas.case_import import CaseImportOptions, CaseImportPolicy

CONTENT = json.dumps([{"case_key": "real-process", "title": "实际 JSON", "content_json": {"扩展": [0, False, None]}}]).encode()


@pytest.mark.asyncio
async def test_actual_worker_reads_json_and_returns_complete_content():
    preview = await CaseImportProcess().parse(filename="real.json", content=CONTENT,
        policy=CaseImportPolicy(), options=CaseImportOptions(layout="json", mode_key="api_testing"))
    assert preview.candidates[0].draft.content_json == {"扩展": [0, False, None]}


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"timeout_seconds": 0.000001}, "时间限制"),
    ({"max_memory_bytes": 1}, "内存限制"),
    ({"max_cpu_seconds": 0.000001}, "CPU 限制"),
    ({"max_output_bytes": 1}, "大小限制"),
])
async def test_actual_worker_resource_limits_reap_process_and_temp_files(monkeypatch, changes, reason):
    original = subprocess.Popen
    started = []
    def track(command, **kwargs):
        process = original(command, **kwargs)
        started.append((process, psutil.Process(process.pid), Path(command[-2]).parent))
        return process
    monkeypatch.setattr(import_process.subprocess, "Popen", track)
    with pytest.raises(ImportProcessError, match=reason) as caught:
        await CaseImportProcess().inspect(filename="real.json", content=CONTENT, policy=CaseImportPolicy(**changes))
    assert caught.value.exit_code is not None
    assert isinstance(caught.value.stdout, str) and isinstance(caught.value.stderr, str)
    process, handle, temporary = started[0]
    assert process.poll() is not None and not handle.is_running() and not temporary.exists()


@pytest.mark.asyncio
async def test_cancellation_waits_for_actual_process_cleanup(monkeypatch):
    original = subprocess.Popen
    started = []
    def track(command, **kwargs):
        process = original(command, **kwargs)
        started.append((process, psutil.Process(process.pid), Path(command[-2]).parent))
        return process
    monkeypatch.setattr(import_process.subprocess, "Popen", track)
    task = asyncio.create_task(CaseImportProcess().inspect(filename="cancel.json", content=CONTENT, policy=CaseImportPolicy()))
    async with asyncio.timeout(5):
        while not started:
            await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process, handle, temporary = started[0]
    assert process.poll() is not None and not handle.is_running() and not temporary.exists()


@pytest.mark.asyncio
async def test_failed_real_subprocess_retains_stdout_stderr_and_exit_code(monkeypatch):
    original = subprocess.Popen
    def controlled_failure(command, **kwargs):
        # Actual OS process failure, without letting API callers select commands.
        return original([sys.executable, "-X", "utf8", "-c", "import sys; print('complete stdout'); print('complete stderr', file=sys.stderr); sys.exit(7)"], **kwargs)
    monkeypatch.setattr(import_process.subprocess, "Popen", controlled_failure)
    with pytest.raises(ImportProcessError) as caught:
        await CaseImportProcess().inspect(filename="failed.json", content=CONTENT, policy=CaseImportPolicy())
    assert caught.value.exit_code == 7
    assert caught.value.stdout.strip() == "complete stdout"
    assert caught.value.stderr.strip() == "complete stderr"

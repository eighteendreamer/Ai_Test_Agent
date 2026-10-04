from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import psutil
import pytest

from src.application.documents.parser_process import DocumentParseProcess
from src.runtime import limited_parser_process
from src.runtime.limited_parser_process import ParserProcessError
from src.schemas.document_parsing import DocumentParsePolicy
from test_document_parsers import actual_docx, actual_pdf


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_subprocess_parses_each_original_without_blocking_event_loop(tmp_path, extension):
    path = tmp_path / f"requirements.{extension}"
    if extension == "docx":
        actual_docx(path)
    elif extension == "pdf":
        actual_pdf(path)
    else:
        path.write_bytes("# 订单查询\r\n有效期：7 天。\r\n".encode("utf-8-sig"))
    task = asyncio.create_task(DocumentParseProcess().parse(filename=path.name, content=path.read_bytes()))
    ticks = 0
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.002)
    result = await task
    assert ticks > 1
    assert "7" in result.text and result.source_size_bytes == path.stat().st_size
    assert result.parser.parser_id == {"md": "markdown", "pdf": "pdf", "docx": "docx"}[extension]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"timeout_seconds": 0.000001}, "时间限制"),
    ({"max_memory_bytes": 1}, "内存限制"),
    ({"max_cpu_seconds": 0.000001}, "CPU 限制"),
    ({"max_output_bytes": 1}, "大小限制"),
])
async def test_docx_resource_limits_reap_actual_child_and_temp_files(tmp_path, monkeypatch, changes, reason):
    path = tmp_path / "actual.docx"
    actual_docx(path)
    popen = subprocess.Popen
    observed = []
    def track(command, **kwargs):
        process = popen(command, **kwargs)
        observed.append((process, psutil.Process(process.pid), Path(command[-2]).parent))
        return process
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", track)
    with pytest.raises(ParserProcessError, match=reason) as caught:
        await DocumentParseProcess().parse(filename=path.name, content=path.read_bytes(), policy=DocumentParsePolicy(**changes))
    assert caught.value.exit_code is not None
    process, handle, temporary = observed[0]
    assert process.poll() is not None and not handle.is_running() and not temporary.exists()


@pytest.mark.asyncio
async def test_actual_parse_cancellation_waits_for_cleanup(monkeypatch):
    popen = subprocess.Popen
    observed = []
    def track(command, **kwargs):
        process = popen(command, **kwargs)
        observed.append((process, psutil.Process(process.pid), Path(command[-2]).parent))
        return process
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", track)
    task = asyncio.create_task(DocumentParseProcess().parse(filename="real.md", content=b"# original"))
    async with asyncio.timeout(5):
        while not observed:
            await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process, handle, temporary = observed[0]
    assert process.poll() is not None and not handle.is_running() and not temporary.exists()


@pytest.mark.asyncio
async def test_real_failed_worker_preserves_stdout_stderr_and_status(monkeypatch):
    popen = subprocess.Popen
    def fail(command, **kwargs):
        return popen([sys.executable, "-X", "utf8", "-c", "import sys; print('actual stdout'); print('actual stderr',file=sys.stderr); sys.exit(9)"], **kwargs)
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", fail)
    with pytest.raises(ParserProcessError) as caught:
        await DocumentParseProcess().parse(filename="real.md", content=b"# original")
    assert caught.value.exit_code == 9
    assert caught.value.stdout.strip() == "actual stdout" and caught.value.stderr.strip() == "actual stderr"


@pytest.mark.asyncio
async def test_invalid_or_scan_pdf_is_not_a_fake_success(tmp_path):
    with pytest.raises(ParserProcessError, match="PDF"):
        await DocumentParseProcess().parse(filename="bad.pdf", content=b"%PDF-1.7\ninvalid")
    path = tmp_path / "blank.pdf"
    actual_pdf(path, blank=True)
    result = await DocumentParseProcess().parse(filename=path.name, content=path.read_bytes())
    assert result.status == "unsupported" and any(w.code == "pdf_no_text" for w in result.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_actual_descendant_is_reaped_after_parent_exit_or_cancellation(tmp_path, monkeypatch, cancel):
    original = subprocess.Popen
    marker = tmp_path / "descendant.pid"
    script = "\n".join([
        "import subprocess, sys, time",
        "from pathlib import Path",
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
        "Path(sys.argv[3]).write_text(str(child.pid))",
        "time.sleep(0.3)",
        "from src.application.documents.parser_worker import main",
        "raise SystemExit(main())",
    ])
    def start(command, **kwargs):
        return original([sys.executable, "-X", "utf8", "-c", script, command[-2], command[-1], str(marker)], **kwargs)
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", start)
    task = asyncio.create_task(DocumentParseProcess().parse(filename="actual.md", content=b"# actual requirement"))
    async with asyncio.timeout(5):
        while not marker.exists():
            await asyncio.sleep(0.005)
    child = psutil.Process(int(marker.read_text()))
    try:
        if cancel:
            # Allow the actual monitor to observe the child before cancelling.
            await asyncio.sleep(0.12)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).text == "# actual requirement"
        assert not child.is_running()
    finally:
        if child.is_running():
            child.kill()
            child.wait(timeout=5)


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_until_actual_cleanup_finishes(monkeypatch):
    original = limited_parser_process.LimitedParserProcess._kill_tree
    entered, finished = Event(), Event()
    def slow_cleanup(handle, observed):
        entered.set()
        time.sleep(0.15)
        try:
            original(handle, observed)
        finally:
            finished.set()
    monkeypatch.setattr(limited_parser_process.LimitedParserProcess, "_kill_tree", staticmethod(slow_cleanup))
    task = asyncio.create_task(DocumentParseProcess().parse(filename="actual.md", content=b"# original"))
    await asyncio.sleep(0.02)
    task.cancel()
    async with asyncio.timeout(5):
        while not entered.is_set():
            await asyncio.sleep(0.002)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set(), "重复取消在实际进程清理结束之前返回"
    finally:
        async with asyncio.timeout(5):
            while not finished.is_set():
                await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_actual_trace_flood_preserves_bounded_prefix_and_exit_code(monkeypatch):
    original = subprocess.Popen
    def flood(command, **kwargs):
        script = "import sys; sys.stderr.write('known-prefix:' + 'x' * 10000); sys.stderr.flush(); sys.exit(17)"
        return original([sys.executable, "-X", "utf8", "-c", script], **kwargs)
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", flood)
    with pytest.raises(ParserProcessError, match="大小限制") as caught:
        await DocumentParseProcess().parse(filename="actual.md", content=b"# original", policy=DocumentParsePolicy(max_output_bytes=500))
    assert caught.value.stderr.startswith("known-prefix:")
    assert caught.value.stderr.endswith("[parser output exceeded configured bound]")
    assert caught.value.exit_code is not None
    assert len(caught.value.stderr) < 600

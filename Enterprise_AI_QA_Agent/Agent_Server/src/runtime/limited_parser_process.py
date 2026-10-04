"""Shared, fixed-entry parser process lifecycle, adapted from CaseImportProcess."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from threading import Event
from typing import Protocol

import psutil

logger = logging.getLogger(__name__)


class ParserProcessPolicy(Protocol):
    timeout_seconds: float
    max_cpu_seconds: float
    max_memory_bytes: int
    max_output_bytes: int


class ParserProcessError(ValueError):
    def __init__(self, message: str, *, exit_code: int | None, stdout: str, stderr: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class LimitedParserProcess:
    def __init__(self, *, worker_module: str, temporary_prefix: str):
        # These values come from internal adapters, never from HTTP request fields.
        self._module = worker_module
        self._prefix = temporary_prefix

    async def run(self, request: dict, policy: ParserProcessPolicy):
        cancelled = Event()
        task = asyncio.create_task(asyncio.to_thread(self._run_sync, request, policy, cancelled))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            # Cancellation can be delivered again while OS cleanup is in progress.
            # Keep the thread task alive and observable until its finally completes.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                task.result()
            except Exception as exc:
                logger.warning("parser_cancel_cleanup_finished", extra={
                    "worker_module": self._module, "error_type": type(exc).__name__})
            raise

    def _run_sync(self, request: dict, policy: ParserProcessPolicy, cancelled: Event):
        with tempfile.TemporaryDirectory(prefix=self._prefix) as directory:
            root = Path(directory)
            input_path, output_path = root / "input.json", root / "output.json"
            input_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            stdout_path, stderr_path = root / "stdout", root / "stderr"
            started = time.monotonic()
            reason = ""
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-m", self._module, str(input_path), str(output_path)],
                    cwd=Path(__file__).resolve().parents[2], stdout=stdout, stderr=stderr,
                )
                handle = psutil.Process(process.pid)
                observed = {handle.pid: handle}
                try:
                    while process.poll() is None:
                        if cancelled.is_set():
                            reason = "解析请求已取消"
                        elif time.monotonic() - started > policy.timeout_seconds:
                            reason = "解析进程超过时间限制"
                        else:
                            memory, cpu = 0, 0.0
                            try:
                                for child in handle.children(recursive=True):
                                    observed[child.pid] = child
                            except psutil.NoSuchProcess:
                                pass
                            for child in observed.values():
                                try:
                                    memory += child.memory_info().rss
                                    times = child.cpu_times()
                                    cpu += times.user + times.system
                                except psutil.NoSuchProcess:
                                    pass
                            if memory > policy.max_memory_bytes:
                                reason = "解析进程超过内存限制"
                            elif cpu > policy.max_cpu_seconds:
                                reason = "解析进程超过 CPU 限制"
                        if any(path.exists() and path.stat().st_size > policy.max_output_bytes
                            for path in (output_path, stdout_path, stderr_path)):
                            reason = "解析输出超过大小限制"
                        if reason:
                            break
                        cancelled.wait(0.05)
                finally:
                    self._kill_tree(handle, observed)
                    process.wait(timeout=5)
            stdout_text = self._read_output(stdout_path, policy.max_output_bytes)
            stderr_text = self._read_output(stderr_path, policy.max_output_bytes)
            if any(path.stat().st_size > policy.max_output_bytes for path in (stdout_path, stderr_path)):
                reason = reason or "解析输出超过大小限制"
            if not reason and time.monotonic() - started > policy.timeout_seconds:
                reason = "解析进程超过时间限制"
            if reason or not output_path.exists():
                self.fail(reason or "解析进程未产生结果", process.returncode, stdout_text, stderr_text)
            if output_path.stat().st_size > policy.max_output_bytes:
                self.fail("解析输出超过大小限制", process.returncode, stdout_text, stderr_text)
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                self.fail("解析进程返回无效结果", process.returncode, stdout_text, stderr_text)
            if not isinstance(payload, dict):
                self.fail("解析进程返回无效结果", process.returncode, stdout_text, stderr_text)
            if process.returncode != 0 or not payload.get("ok"):
                self.fail(payload.get("error") or "解析进程失败", process.returncode, stdout_text, stderr_text)
            if "result" not in payload:
                self.fail("解析进程缺少结果", process.returncode, stdout_text, stderr_text)
            logger.info("parser_process_completed", extra={"worker_module": self._module,
                "operation": request.get("operation", "parse"), "pid": process.pid, "exit_code": process.returncode,
                "stdout_chars": len(stdout_text), "stderr_chars": len(stderr_text)})
            return payload["result"]

    @staticmethod
    def _read_output(path, maximum):
        with path.open("rb") as stream:
            raw = stream.read(maximum)
        text = raw.decode("utf-8", errors="replace")
        return text + "\n[parser output exceeded configured bound]" if path.stat().st_size > maximum else text

    @staticmethod
    def _kill_tree(handle, observed):
        try:
            for child in handle.children(recursive=True):
                observed[child.pid] = child
        except psutil.NoSuchProcess:
            pass
        processes = list(observed.values())
        for process in reversed(processes):
            with suppress(psutil.NoSuchProcess):
                process.kill()
        _, alive = psutil.wait_procs(processes, timeout=5)
        if alive:
            raise RuntimeError("无法回收解析进程")

    def fail(self, message, code, stdout, stderr):
        logger.error("parser_process_failed", extra={"worker_module": self._module,
            "exit_code": code, "stdout_chars": len(stdout), "stderr_chars": len(stderr)})
        raise ParserProcessError(message, exit_code=code, stdout=stdout, stderr=stderr)

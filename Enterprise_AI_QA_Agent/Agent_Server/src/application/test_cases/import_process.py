from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from threading import Event

import psutil

from src.schemas.case_import import CaseImportOptions, CaseImportPolicy, CaseImportPreview, CaseImportSheet

logger = logging.getLogger(__name__)


class ImportProcessError(ValueError):
    def __init__(self, message: str, *, exit_code: int | None, stdout: str, stderr: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class CaseImportProcess:
    async def inspect(self, *, filename: str, content: bytes, policy: CaseImportPolicy, header_row: int = 1) -> list[CaseImportSheet]:
        result = await self._run({"operation": "inspect", "header_row": header_row}, filename, content, policy)
        return [CaseImportSheet.model_validate(sheet) for sheet in result]

    async def parse(self, *, filename: str, content: bytes, policy: CaseImportPolicy, options: CaseImportOptions) -> CaseImportPreview:
        result = await self._run({"operation": "parse", "options": options.model_dump(mode="json")}, filename, content, policy)
        return CaseImportPreview.model_validate(result)

    async def _run(self, request: dict, filename: str, content: bytes, policy: CaseImportPolicy):
        if not content or len(content) > policy.limits.max_bytes:
            self._failure("导入文件为空或超过文件大小限制", None, "", "")
        request = {**request, "filename": filename, "content_base64": base64.b64encode(content).decode(), "policy": policy.model_dump(mode="json")}
        cancelled = Event()
        task = asyncio.create_task(asyncio.to_thread(self._run_sync, request, policy, cancelled))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            with suppress(Exception):
                await task
            raise

    def _run_sync(self, request: dict, policy: CaseImportPolicy, cancelled: Event):
        # No upstream counterpart (checked: runtime CLI timeout and browser
        # cleanup helpers): parser requests need input/output, CPU and RSS bounds.
        with tempfile.TemporaryDirectory(prefix="qa-case-import-") as directory:
            root = Path(directory)
            input_path, output_path = root / "input.json", root / "output.json"
            input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            stdout_path, stderr_path = root / "stdout", root / "stderr"
            started = time.monotonic()
            reason = ""
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-m", "src.application.test_cases.import_worker", str(input_path), str(output_path)],
                    cwd=Path(__file__).resolve().parents[3], stdout=stdout, stderr=stderr,
                )
                handle = psutil.Process(process.pid)
                try:
                    while process.poll() is None:
                        if cancelled.is_set():
                            reason = "解析请求已取消"
                        elif time.monotonic() - started > policy.timeout_seconds:
                            reason = "解析进程超过时间限制"
                        else:
                            try:
                                if handle.memory_info().rss > policy.max_memory_bytes:
                                    reason = "解析进程超过内存限制"
                                cpu = handle.cpu_times()
                                if cpu.user + cpu.system > policy.max_cpu_seconds:
                                    reason = "解析进程超过 CPU 限制"
                            except psutil.NoSuchProcess:
                                pass
                        if any(path.exists() and path.stat().st_size > policy.max_output_bytes for path in (output_path, stdout_path, stderr_path)):
                            reason = "解析输出超过大小限制"
                        if reason:
                            self._kill_tree(handle)
                            break
                        cancelled.wait(0.05)
                finally:
                    if process.poll() is None:
                        self._kill_tree(handle)
                    process.wait(timeout=5)
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            if reason or not output_path.exists():
                self._failure(reason or "解析进程未产生结果", process.returncode, stdout_text, stderr_text)
            if output_path.stat().st_size > policy.max_output_bytes:
                self._failure("解析输出超过大小限制", process.returncode, stdout_text, stderr_text)
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                self._failure("解析进程返回无效结果", process.returncode, stdout_text, stderr_text)
            if process.returncode != 0 or not payload.get("ok"):
                self._failure(payload.get("error") or "解析进程失败", process.returncode, stdout_text, stderr_text)
            logger.info("case_import_process_completed", extra={"operation": request["operation"], "pid": process.pid, "exit_code": process.returncode})
            return payload["result"]

    @staticmethod
    def _kill_tree(handle):
        try:
            processes = [*handle.children(recursive=True), handle]
        except psutil.NoSuchProcess:
            return
        for process in processes:
            with suppress(psutil.NoSuchProcess):
                process.kill()
        _, alive = psutil.wait_procs(processes, timeout=5)
        if alive:
            raise RuntimeError("无法回收解析进程")

    @staticmethod
    def _failure(message, code, stdout, stderr):
        logger.error("case_import_process_failed", extra={"exit_code": code, "stdout_chars": len(stdout), "stderr_chars": len(stderr)})
        raise ImportProcessError(message, exit_code=code, stdout=stdout, stderr=stderr)

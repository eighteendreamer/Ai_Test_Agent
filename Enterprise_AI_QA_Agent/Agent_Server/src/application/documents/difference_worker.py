"""Fixed internal entry for bounded deterministic comparison, without model calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import psutil

from src.application.documents.difference_engine import compare_snapshots
from src.schemas.document_differences import DocumentDifferencePolicy
from src.schemas.document_parsing import DocumentParseSnapshotView


def main() -> int:
    request_path, result_path = map(Path, sys.argv[1:3])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        policy = DocumentDifferencePolicy.model_validate(request["policy"])
        if request_path.stat().st_size > policy.max_input_bytes:
            raise ValueError("比较输入超过大小限制")
        result = compare_snapshots(old=DocumentParseSnapshotView.model_validate(request["old"]),
            new=DocumentParseSnapshotView.model_validate(request["new"]), policy=policy)
        process = psutil.Process()
        cpu = process.cpu_times()
        if cpu.user + cpu.system > policy.max_cpu_seconds:
            raise ValueError("比较进程超过 CPU 限制")
        if process.memory_info().rss > policy.max_memory_bytes:
            raise ValueError("比较进程超过内存限制")
        payload, code = {"ok": True, "result": result.model_dump(mode="json")}, 0
    except Exception as exc:
        payload = {"ok": False, "error": str(exc) if type(exc) is ValueError else f"文档比较失败：{type(exc).__name__}"}
        code = 2
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if "policy" in locals() and len(encoded) > policy.max_output_bytes:
        encoded = json.dumps({"ok": False, "error": "比较输出超过大小限制"}).encode()
        code = 2
    result_path.write_bytes(encoded)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

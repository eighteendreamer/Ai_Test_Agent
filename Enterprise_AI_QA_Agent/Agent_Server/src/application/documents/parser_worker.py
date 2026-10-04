"""Fixed subprocess entry; no document parser runs inside an API event loop."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import psutil

from src.application.documents.parser_factory import DocumentParserFactory
from src.schemas.document_parsing import DocumentParseOptions, DocumentParsePolicy


def main() -> int:
    request_path, result_path = map(Path, sys.argv[1:3])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    policy = DocumentParsePolicy.model_validate(request["policy"])
    try:
        result = DocumentParserFactory().parse(filename=request["filename"],
            content=base64.b64decode(request["content_base64"], validate=True),
            options=DocumentParseOptions.model_validate(request["options"]), limits=policy.limits)
        process = psutil.Process()
        cpu = process.cpu_times()
        if cpu.user + cpu.system > policy.max_cpu_seconds:
            raise ValueError("解析进程超过 CPU 限制")
        if process.memory_info().rss > policy.max_memory_bytes:
            raise ValueError("解析进程超过内存限制")
        payload = {"ok": True, "result": result.model_dump(mode="json")}
        code = 0
    except Exception as exc:
        payload = {"ok": False, "error": str(exc) if type(exc) is ValueError else f"文档解析器失败：{type(exc).__name__}"}
        code = 2
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > policy.max_output_bytes:
        encoded = json.dumps({"ok": False, "error": "解析输出超过大小限制"}).encode()
        code = 2
    result_path.write_bytes(encoded)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

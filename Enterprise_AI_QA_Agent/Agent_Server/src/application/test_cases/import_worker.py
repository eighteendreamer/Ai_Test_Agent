"""Fixed subprocess entry. Reads only the parent's temporary parser request."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import psutil

from src.application.test_cases.import_parser import CaseImportParser
from src.schemas.case_import import CaseImportOptions, CaseImportPolicy


def main() -> int:
    request_path, result_path = map(Path, sys.argv[1:3])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    policy = CaseImportPolicy.model_validate(request["policy"])
    parser = CaseImportParser(policy.limits)
    try:
        content = base64.b64decode(request["content_base64"], validate=True)
        if request["operation"] == "inspect":
            result = [sheet.model_dump(mode="json") for sheet in parser.inspect(
                filename=request["filename"], content=content, header_row=request.get("header_row", 1),
            )]
        elif request["operation"] == "parse":
            result = parser.parse(filename=request["filename"], content=content,
                                  options=CaseImportOptions.model_validate(request["options"])).model_dump(mode="json")
        else:
            raise ValueError("未知的用例解析操作")
        # The parent polls while running. Check before publishing as well:
        # short jobs may finish between two parent observations.
        process = psutil.Process()
        cpu = process.cpu_times()
        if cpu.user + cpu.system > policy.max_cpu_seconds:
            raise ValueError("解析进程超过 CPU 限制")
        if process.memory_info().rss > policy.max_memory_bytes:
            raise ValueError("解析进程超过内存限制")
        payload = {"ok": True, "result": result}
        code = 0
    except Exception as exc:
        payload = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else f"解析器失败：{type(exc).__name__}", "error_type": type(exc).__name__}
        code = 2
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > policy.max_output_bytes:
        encoded = json.dumps({"ok": False, "error": "解析输出超过大小限制"}).encode()
        code = 2
    result_path.write_bytes(encoded)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

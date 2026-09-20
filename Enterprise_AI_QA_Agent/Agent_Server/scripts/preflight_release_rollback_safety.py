"""P6 发布与回滚验收门：扫出启动路径上的破坏性 DDL，并汇总一次发布会改动的加性结构。

发布安全要求任何版本都能被回滚而不丢数据；启动时执行的 ``ALTER TABLE ... DROP COLUMN``
一类语句会立即让上一版本代码读到不存在的列，属于回滚阻断项，必须先由人决策。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 只有带表名的语句才算 DDL；纯关键词字符串（如命令黑名单）不会命中。
DESTRUCTIVE = re.compile(
    r"ALTER\s+TABLE[^;]*?\bDROP\s+(COLUMN|CONSTRAINT|INDEX)|\bTRUNCATE\s+TABLE|\bDROP\s+TABLE\s",
    re.IGNORECASE,
)
ADDITIVE = re.compile(
    r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS|\bALTER\s+TABLE[^;]*?\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS",
    re.IGNORECASE,
)


def _sql_literals(tree: ast.AST) -> list[tuple[int, int, str]]:
    """Yield SQL text per string node, re-joining f-string literal chunks.

    An f-string parses into one Constant per literal segment, so a statement that
    spans ``ALTER TABLE {table} ... DROP COLUMN`` only matches once rejoined.
    """
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.lineno, node.col_offset, node.value))
        elif isinstance(node, ast.JoinedStr):
            literal = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if literal.strip():
                found.append((node.lineno, node.col_offset, literal))
    return found


def scan(source_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Split startup SQL into rollback blockers and additive changes."""
    blockers: list[dict[str, Any]] = []
    additive: list[dict[str, Any]] = []
    for path in sorted(source_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"Cannot parse {path.relative_to(PROJECT_ROOT)}: {exc}") from exc
        relative = str(path.relative_to(source_root)).replace("\\", "/")
        for line, column, value in _sql_literals(tree):
            for match in DESTRUCTIVE.finditer(value):
                blockers.append({
                    "file": relative,
                    "line": line,
                    "column": column,
                    "statement": " ".join(match.group(0).split())[:160],
                })
            for match in ADDITIVE.finditer(value):
                additive.append({
                    "file": relative,
                    "line": line,
                    "statement": " ".join(match.group(0).split())[:80],
                })
    return {"blockers": blockers, "additive": additive}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(PROJECT_ROOT / "src"))
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report only")
    args = parser.parse_args()

    report = scan(Path(args.source_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"additive startup DDL statements: {len(report['additive'])}")
        for item in report["additive"]:
            print(f"  {item['file']}:{item['line']} {item['statement']}")
        print(f"rollback blockers (destructive startup DDL): {len(report['blockers'])}")
        for item in report["blockers"]:
            print(f"  {item['file']}:{item['line']} {item['statement']}")
        if report["blockers"]:
            print("发布门禁：以上语句在启动路径执行，回滚到上一版本会读到已消失的列/表，需先决策再放行。")
    return 1 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

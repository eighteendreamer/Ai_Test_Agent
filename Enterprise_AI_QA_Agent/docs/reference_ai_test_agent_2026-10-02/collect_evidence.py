"""Read local reference source and attempt two existing unit-test files."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
REFERENCE = ROOT / "项目借鉴/ai_test_agent"


def git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def fingerprint(root, folders):
    return [{"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for folder in folders for p in sorted((root / folder).rglob("*"))
            if p.is_file() and p.suffix in {".py", ".vue", ".js", ".ts", ".yaml"}
            and "__pycache__" not in p.parts]


def main():
    before = fingerprint(REFERENCE, ["agent", "api", "skills", "tools", "worker", "tests", "ui/src"])
    own_before = fingerprint(ROOT, ["Agent_Server/src", "agent_web/src"])
    files, routes, tables, tests, errors = [], [], [], [], []
    for entry in before:
        path = REFERENCE / entry["path"]
        if path.suffix != ".py":
            continue
        content = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(content, filename=str(path))
        except SyntaxError as error:
            errors.append({"path": entry["path"], "line": error.lineno, "type": type(error).__name__})
            continue
        files.append({"path": entry["path"], "lines": len(content.splitlines())})
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and entry["path"] == "tools/database.py":
                for statement in node.body:
                    if isinstance(statement, ast.Assign) and any(getattr(t, "id", "") == "__tablename__" for t in statement.targets):
                        tables.append({"class": node.name, "table": statement.value.value, "line": node.lineno})
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if entry["path"].startswith("tests/") and node.name.startswith("test_"):
                tests.append({"path": entry["path"], "function": node.name, "line": node.lineno})
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if getattr(decorator.func.value, "id", "") not in {"router", "app", "catch_router"}:
                    continue
                if decorator.func.attr not in {"get", "post", "put", "patch", "delete", "websocket"}:
                    continue
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    routes.append({"path": entry["path"], "line": node.lineno,
                                   "method": decorator.func.attr, "route": decorator.args[0].value})
    metadata = {"captured_at_utc": datetime.now(UTC).isoformat(), "reference_commit": git(REFERENCE, "rev-parse", "HEAD"),
                "own_commit": git(ROOT, "rev-parse", "HEAD"), "reference_dirty_before": bool(git(REFERENCE, "status", "--porcelain")),
                "files": files, "routes": routes, "orm_tables": tables, "test_definitions": tests,
                "syntax_errors": errors, "source_hashes": before,
                "scope": "Static local source review; counts exclude main.py decorators and parameterized expansion; no deployment claims."}
    with (OUT / "source-evidence.json").open("x", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
    selected = ["tests/test_exceptions.py", "tests/test_param_resolver.py"]
    command = [sys.executable, "-B", "-m", "pytest", *[str(REFERENCE / p) for p in selected],
               "-q", "-p", "no:cacheprovider", "-o", "addopts=", "--confcutdir", str(REFERENCE / "tests"),
               f"--junitxml={OUT / 'unit-tests.xml'}"]
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": ""}
    with tempfile.TemporaryDirectory(prefix="reference_review_") as cwd, (OUT / "unit-tests.log").open("x", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
    execution = {"command": command, "python": sys.version, "exit_code": result.returncode,
                 "scope": "Existing exception and parameter-resolver unit tests, shared installed runtime; no dependency installation, no app startup, no real model/browser/database.",
                 "reference_source_unchanged": before == fingerprint(REFERENCE, ["agent", "api", "skills", "tools", "worker", "tests", "ui/src"]),
                 "own_source_unchanged": own_before == fingerprint(ROOT, ["Agent_Server/src", "agent_web/src"]),
                 "reference_dirty_after": bool(git(REFERENCE, "status", "--porcelain"))}
    (OUT / "verification.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"python_files_parsed": len(files), "route_decorators": len(routes), "orm_tables": len(tables),
                      "test_definitions": len(tests), "syntax_errors": len(errors), **execution}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Validate report references and retain original-source syntax evidence."""
from __future__ import annotations

import ast
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
REFERENCE = ROOT / "项目借鉴/ai_test_agent"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_name", nargs="?", default="report-validation.json")
    destination = OUT / parser.parse_args().output_name
    if destination.exists():
        raise FileExistsError("Keep prior validation evidence; use a new output directory.")
    report = (OUT / "REPORT.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", report)
    local_links = [link for link in links if not link.startswith("https://")]
    missing = [link for link in local_links
               if (OUT / unquote(link.split("#", 1)[0])).resolve() != destination
               and not (OUT / unquote(link.split("#", 1)[0])).exists()]
    evidence = json.loads((OUT / "source-evidence.json").read_text(encoding="utf-8"))
    changed, errors, parsed = [], [], 0
    for entry in evidence["source_hashes"]:
        path = REFERENCE / entry["path"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            changed.append(entry["path"])
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=entry["path"])
            parsed += 1
        except SyntaxError as error:
            errors.append({"path": entry["path"], "line": error.lineno, "message": error.msg})
    for path in [OUT / "collect_evidence.py", Path(__file__), REFERENCE / "main.py"]:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    result = subprocess.run(["git", "status", "--porcelain"], cwd=REFERENCE,
                            capture_output=True, check=True)
    data = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "local_link_occurrences": len(local_links),
        "missing_links": missing,
        "changed_reference_sources": changed,
        "reference_worktree_clean": not result.stdout.strip(),
        "reference_python_files_parsed": parsed,
        "reference_syntax_errors": errors,
        "report_helpers_and_reference_main_parse": "passed",
        "route_decorators_from_successful_files": len(evidence["routes"]),
        "orm_table_declarations": len(evidence["orm_tables"]),
        "test_function_definitions": len(evidence["test_definitions"]),
        "scope": "Report integrity and unchanged source syntax; not an app startup or functional test.",
    }
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False))
    if missing or changed or result.stdout.strip():
        raise SystemExit(1)


if __name__ == "__main__":
    main()

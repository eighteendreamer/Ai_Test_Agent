"""Audit API dependency-admission evidence without rewriting prior artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation_path = directory / "p3-api-dependency-validation.json"
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


plan_hashes = {str(path): digest(path) for path in plans}
assert len(set(plan_hashes.values())) == 1, "three plans differ"

expected = {
    "p3-api-dependency-before.xml": {"tests": 9, "failures": 9, "errors": 0, "skipped": 0},
    "p3-api-dependency-final.xml": {"tests": 11, "failures": 0, "errors": 0, "skipped": 0},
    "p3-api-dependency-verified4.xml": {"tests": 73, "failures": 0, "errors": 0, "skipped": 3},
    "p3-api-dependency-regression2.xml": {"tests": 106, "failures": 0, "errors": 0, "skipped": 5},
}
test_counts = {}
for name, expected_counts in expected.items():
    suite = ET.parse(directory / name).getroot().find("testsuite")
    assert suite is not None
    actual = {key: int(suite.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}
    assert actual == expected_counts, f"unexpected {name}: {actual}"
    test_counts[name] = actual

actual_http = json.loads((directory / "p3-api-dependency-final" / "actual-http.json").read_text(encoding="utf-8"))
assert actual_http["terminal_extra_http"] == 0
assert actual_http["external_ai_calls"] == 0
assert actual_http["report"]["completed"] == 2
assert actual_http["report"]["failed"] == 0
assert actual_http["report"]["skipped"] == 0
assert actual_http["requests"] == [["POST", "/orders"], ["GET", "/orders/0"]]

selected = [directory / name for name in [
    "P3_API_DEPENDENCY_ADMISSION_RESEARCH.md", "p3-api-dependency-audit.py",
    "p3-api-dependency-before.log", "p3-api-dependency-before.xml",
    "p3-api-dependency-final.log", "p3-api-dependency-final.xml",
    "p3-api-dependency-final/actual-http.json",
    "p3-api-dependency-verified4.log", "p3-api-dependency-verified4.xml",
    "p3-api-dependency-regression2.log", "p3-api-dependency-regression2.xml",
]]
artifact_hashes = {str(path.relative_to(root)).replace("\\", "/"): digest(path) for path in selected}
validation_path.write_text(json.dumps({
    "baseline": "5156e19c",
    "full_goal_complete": False,
    "dependency_admission_fixed": True,
    "real_http_order_verified": True,
    "unresolved_path_zero_http": True,
    "external_ai_calls": 0,
    "tests": test_counts,
    "three_plans_equal": True,
    "plan_sha256": plan_hashes[str(plans[0])],
    "artifact_hashes": artifact_hashes,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"API dependency artifacts audited={len(selected)}; full goal active")

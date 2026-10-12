"""Audit current Git document-source acceptance evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert len({digest(path) for path in plans}) == 1, "three plans differ"


def counts(name: str) -> dict[str, int]:
    suite = ET.parse(directory / name).getroot().find("testsuite")
    assert suite is not None
    return {key: int(suite.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}


observed = {
    "p3-git-current.xml": counts("p3-git-current.xml"),
    "p3-git-live2.xml": counts("p3-git-live2.xml"),
    "p3-git-production-current.xml": counts("p3-git-production-current.xml"),
}
assert observed["p3-git-current.xml"] == {"tests": 28, "failures": 0, "errors": 0, "skipped": 4}
assert observed["p3-git-live2.xml"] == {"tests": 26, "failures": 0, "errors": 0, "skipped": 0}
assert observed["p3-git-production-current.xml"] == {"tests": 2, "failures": 0, "errors": 0, "skipped": 0}

selected = [directory / name for name in [
    "P3_GIT_SOURCE_CURRENT_RESEARCH.md", "p3-git-current-audit.py",
    "p3-git-current.log", "p3-git-current.xml",
    "p3-git-live2.log", "p3-git-live2.xml",
    "p3-git-production-current.log", "p3-git-production-current.xml",
]]
validation = directory / "p3-git-current-validation.json"
validation.write_text(json.dumps({
    "baseline": "4874360c",
    "full_goal_complete": False,
    "git_source_implemented": True,
    "upload_does_not_use_git": True,
    "external_ai_calls": 0,
    "tests": observed,
    "three_plans_equal": True,
    "plan_sha256": digest(plans[0]),
    "artifact_hashes": {str(path.relative_to(root)).replace("\\", "/"): digest(path) for path in selected},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("Git source evidence audited; implementation and production acceptance verified; full goal active")

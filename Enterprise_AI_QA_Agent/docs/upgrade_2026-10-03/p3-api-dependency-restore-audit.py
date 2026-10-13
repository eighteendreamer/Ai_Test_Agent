"""Audit actual process recovery evidence and committed Git raw bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[2]
directory = Path(__file__).resolve().parent
repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True).strip())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(name):
    suite = ET.parse(directory / name).getroot().find("testsuite")
    return {key: int(suite.get(key, "0")) for key in ("tests", "failures", "errors", "skipped")}


observed = {name: counts(name) for name in (
    "p3-api-dependency-restore-before.xml", "p3-api-dependency-restore-after.xml",
    "p3-api-dependency-restore-regression.xml", "p3-api-dependency-restore-regression2.xml")}
assert observed["p3-api-dependency-restore-before.xml"] == dict(tests=7, failures=7, errors=0, skipped=0)
assert observed["p3-api-dependency-restore-after.xml"] == dict(tests=7, failures=0, errors=0, skipped=0)
assert observed["p3-api-dependency-restore-regression.xml"] == dict(tests=70, failures=5, errors=0, skipped=2)
assert observed["p3-api-dependency-restore-regression2.xml"] == dict(tests=70, failures=0, errors=0, skipped=2)

for prefix in ("after", "after2"):
    for variant in ("brace", "colon", "explicit", "body-query", "effective-url"):
        folder = directory / f"p3-api-dependency-restore-{prefix}" / variant
        checkpoint = json.loads((folder / "checkpoint.json").read_text(encoding="utf8"))
        result = json.loads((folder / "result.json").read_text(encoding="utf8"))
        http = json.loads((folder / "http.json").read_text(encoding="utf8"))
        assert http["first_exit"] == 73 and http["second_exit"] == 0
        assert len(http["requests"]) == 2 and http["requests"][0]["path"] == "/orders"
        assert http["requests"][1]["path"] == ("/confirm?id=0" if variant == "body-query" else "/orders/0")
        assert checkpoint["tasks"][0] == result["campaign"]["tasks"][0]
        assert all(task["status"] == "completed" for task in result["campaign"]["tasks"])
        assert result["report"]["completed"] == 2 and result["report"]["failed"] == 0
        assert result["external_ai_calls"] == 0

# Compare all raw product artifacts delivered by the previous Git acceptance
# commit, not only its eight summary files. Read Git blobs without text decoding.
git_raw = {}
paths = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "b25d15cf"],
    cwd=root, text=True).splitlines()
for name in paths:
    if "/p3-git-production-current/" not in name:
        continue
    original = subprocess.check_output(["git", "show", f"b25d15cf:{name}"], cwd=root)
    current = (repo / name).read_bytes()
    assert original == current, f"Git product raw bytes changed: {name}"
    git_raw[name] = hashlib.sha256(current).hexdigest()
assert len(git_raw) == 48

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
assert "扫描 PDF OCR 属于首期范围外" in plans[0].read_text(encoding="utf8")

artifacts = [path for path in directory.glob("p3-api-dependency-restore-*") if path.is_file()
    and path.name != "p3-api-dependency-restore-validation.json"]
for folder in ("before", "after", "after2"):
    artifacts.extend(path for path in (directory / f"p3-api-dependency-restore-{folder}").rglob("*") if path.is_file())
artifacts += [root / "Agent_Server/tests/test_api_dependency_restore_http.py",
    directory / "P3_API_DEPENDENCY_RESTORE_RESEARCH.md"]
report = {"baseline": "b25d15cf", "full_goal_complete": False, "external_ai_calls": 0,
    "verification_scope": "actual local HTTP, abrupt child process exit and new process coordinator recovery; file checkpoint",
    "production_storage_restart_verified_by_this_batch": False, "tests": observed,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]),
    "previous_git_raw_48_artifacts_unchanged": git_raw,
    "artifact_hashes": {str(path.relative_to(root)).replace("\\", "/"): digest(path) for path in artifacts}}
(directory / "p3-api-dependency-restore-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
print("Process recovery verified; 48 previous raw artifacts unchanged; complete upgrade goal remains active.")

"""Audit explicit read-only reconciliation without unsafe write replay."""
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


expected = {"functional3": (13, 0, 0), "final": (167, 0, 8), "smoke": (40, 0, 0)}
tests = {}
for name, wanted in expected.items():
    suite = ET.parse(directory / f"p3-api-unknown-reconciliation-{name}.xml").getroot().find("testsuite")
    observed = tuple(int(suite.get(key, "0")) for key in ("tests", "failures", "skipped"))
    assert observed == wanted and int(suite.get("errors", "0")) == 0, (name, observed)
    tests[name] = dict(zip(("tests", "failures", "skipped"), observed))

for name in ("explicit-reconciliation-effect_absent.json", "explicit-reconciliation-effect_present.json"):
    facts = json.loads((directory / "p3-api-unknown-reconciliation-functional3" / name).read_text(encoding="utf8"))
    tasks = facts["tasks"]
    assert [task["status"] for task in tasks] == ["failed", "completed", "skipped"]
    assert tasks[0]["request_outcome"] == "unknown"
    assert tasks[0]["reconciliation_status"] in {"effect_absent", "effect_present"}
    assert facts["terminal_extra_http"] == facts["external_ai_calls"] == 0
    assert [call["method"] for call in facts["calls"]] == ["POST", "GET"]
for name in ("unknown-write.json", "server-error-write.json"):
    facts = json.loads((directory / "p3-api-unknown-reconciliation-functional3" / name).read_text(encoding="utf8"))
    assert facts["request_count"] == 1

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
previous = {}
for name in subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "0f492ff9"], cwd=root, text=True).splitlines():
    if "/p3-api-required-binding-" not in name:
        continue
    current = (repo / name).read_bytes()
    assert current == subprocess.check_output(["git", "show", f"0f492ff9:{name}"], cwd=root)
    previous[name] = hashlib.sha256(current).hexdigest()
assert len(previous) > 100
artifacts = [p for p in directory.glob("p3-api-unknown-reconciliation-*") if p.is_file() and p.name != "p3-api-unknown-reconciliation-validation.json"]
for folder in directory.glob("p3-api-unknown-reconciliation-*"):
    if folder.is_dir():
        artifacts.extend(p for p in folder.rglob("*") if p.is_file())
artifacts += [directory / "P3_API_UNKNOWN_RECONCILIATION_RESEARCH.md", root / "Agent_Server/tests/test_api_unknown_write_recovery.py"]
report = {"baseline": "0f492ff9", "full_goal_complete": False, "tests": tests,
    "explicit_read_only_reconciliation_verified": True, "unknown_write_replay_prevented": True,
    "automatic_query_discovery_or_write_retry_verified": False, "external_ai_calls": 0,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]), "previous_binding_originals_unchanged": previous,
    "artifact_hashes": {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in artifacts}}
(directory / "p3-api-unknown-reconciliation-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
print("Explicit read-only reconciliation verified; prior binding originals unchanged; full goal remains active.")

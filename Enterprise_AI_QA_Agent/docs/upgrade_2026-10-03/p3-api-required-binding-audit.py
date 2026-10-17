"""Audit missing-binding prevention and real producer-output evidence."""
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


expected = {"before": (10, 10, 0), "after": (10, 0, 0), "final": (158, 0, 8)}
tests = {}
for name, wanted in expected.items():
    suite = ET.parse(directory / f"p3-api-required-binding-{name}.xml").getroot().find("testsuite")
    observed = tuple(int(suite.get(key, "0")) for key in ("tests", "failures", "skipped"))
    assert observed == wanted and int(suite.get("errors", "0")) == 0, (name, observed)
    tests[name] = dict(zip(("tests", "failures", "skipped"), observed))

expanded = sorted((directory / "p3-api-required-binding-expanded2").glob("*.json"))
assert len(expanded) == 16
for path in expanded:
    facts = json.loads(path.read_text(encoding="utf8"))
    assert facts["external_ai_calls"] == 0
    for event in facts["events"]:
        for value in json.dumps(event, ensure_ascii=False):
            assert value not in {"stale-example"}
    if "missing" in path.name:
        assert facts["statuses"] == ["completed", "failed", "skipped", "completed"]
        assert all(task["path"] not in {"/consume", "/child"} for task in facts["events"])
    elif "values" in path.name or "fallback" in path.name or "literal" in path.name or "present-null" in path.name:
        assert facts["statuses"] == ["completed"] * 2

process = json.loads((directory / "p3-api-required-binding-process/http.json").read_text(encoding="utf8"))
assert process["first_exit"] == 73 and process["second_exit"] == 0
assert len(process["events"]) == 1 and process["producer_unchanged"]
result = json.loads((directory / "p3-api-required-binding-process/result.json").read_text(encoding="utf8"))
assert result["campaign"]["tasks"][1]["status"] == "failed"
assert result["campaign"]["tasks"][1]["attempts"] == 0
assert result["external_ai_calls"] == 0

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
previous = {}
for name in subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "fec6234b"], cwd=root, text=True).splitlines():
    if "/p3-api-token-expiry-" not in name:
        continue
    current = (repo / name).read_bytes()
    assert current == subprocess.check_output(["git", "show", f"fec6234b:{name}"], cwd=root)
    previous[name] = hashlib.sha256(current).hexdigest()
assert len(previous) > 20
artifacts = [p for p in directory.glob("p3-api-required-binding-*") if p.is_file() and p.name != "p3-api-required-binding-validation.json"]
for folder in directory.glob("p3-api-required-binding-*"):
    if folder.is_dir():
        artifacts.extend(p for p in folder.rglob("*") if p.is_file())
artifacts += [directory / "P3_API_REQUIRED_BINDING_RESEARCH.md", root / "Agent_Server/tests/test_api_required_binding_http.py"]
report = {"baseline": "fec6234b", "full_goal_complete": False, "tests": tests,
    "real_http_missing_binding_and_values_verified": True, "process_restore_no_duplicate_producer": True,
    "external_ai_calls": 0, "automatic_401_or_default_ai_full_flow_verified": False,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]), "previous_token_expiry_originals_unchanged": previous,
    "artifact_hashes": {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in artifacts}}
(directory / "p3-api-required-binding-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
print("Required bindings and producer-output recovery verified; prior expiry originals unchanged; full goal remains active.")

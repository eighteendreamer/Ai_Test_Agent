"""Verify owned evidence, unchanged prior originals and current plan copies."""
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent


def fact(path):
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix(),
            "size_bytes": len(data), "sha256": sha256(data).hexdigest()}


def verify_prior(item):
    path = Path(item["path"])
    if not path.is_absolute():
        path = ROOT / path
    assert fact(path) == item, f"Prior bytes changed: {item['path']}"


previous = json.loads((DOCS / "p2-default-current-validation.json").read_bytes())
for item in previous["evidence_files"] + previous["protected_current_files"]:
    verify_prior(item)

plans = [ROOT / "docs/upgrade_2026-10-03/PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
assert len({p.read_bytes() for p in plans}) == 1, "Plan copies differ"

tests = []
for name in ("contract", "live", "projection", "default"):
    suite = ET.parse(DOCS / f"p2-linked-context-{name}.xml").getroot()
    cases = list(suite.iter("testcase"))
    tests.append({"batch": name, "tests": len(cases),
        "failed": sum(c.find("failure") is not None for c in cases),
        "errors": sum(c.find("error") is not None for c in cases),
        "skipped": sum(c.find("skipped") is not None for c in cases)})
assert tests == [
    {"batch": "contract", "tests": 57, "failed": 0, "errors": 0, "skipped": 21},
    {"batch": "live", "tests": 70, "failed": 0, "errors": 0, "skipped": 3},
    {"batch": "projection", "tests": 24, "failed": 0, "errors": 0, "skipped": 0},
    {"batch": "default", "tests": 1, "failed": 1, "errors": 0, "skipped": 0}], tests

pg = json.loads((DOCS / "p2-linked-context-default/PG-planning.json").read_bytes())
verification = json.loads((DOCS / "p2-linked-context-default/verification.json").read_bytes())
assert verification["task"]["status"] == "failed" and verification["actual_business_HTTP"] == []
assert not verification["planning_completed"] and not verification["business_execution_completed"]
assert pg["seed"]["linked_source_context_protocol"] == "case-planning-linked-navigation-v1"
requests = []
for path in sorted((DOCS / "p2-linked-context-default/physical").glob("*.request.json")):
    raw = json.loads(path.read_bytes())
    context = json.loads(raw["messages"][1]["content"])
    nav = context["linked_source_reads"]
    assert nav["protocol"] == pg["seed"]["linked_source_context_protocol"]
    assert "snapshot" not in nav["entries"][0]["basis"]
    assert nav["entries"][0]["basis"]["version_id"] == pg["checkpoint"]["linked_sources"]["entries"][0]["basis"]["version"]["id"]
    case = next(e for e in context["evidence"] if e["kind"] == "case")
    assert case["payload"]["content"] == json.loads((DOCS / "p2-linked-context-default/full-case.json").read_bytes())
    requests.append({"planning_stage": context["planning_stage"],
        "context_chars": len(raw["messages"][1]["content"]),
        "prompt_chars": len(raw["messages"][0]["content"]),
        "navigation_chars": len(json.dumps(nav, ensure_ascii=False)),
        "evidence_chars": len(json.dumps(context["evidence"], ensure_ascii=False))})
assert {x["planning_stage"] for x in requests} == {"logic", "effects"} and len(requests) == 2
assert len(verification["SDK_create_observations"]) == 2
assert all(len(x["physical_requests"]) == 1 for x in verification["SDK_create_observations"])

paths = sorted(p for p in DOCS.glob("p2-linked-context-*") if p.is_file())
paths += sorted(p for d in DOCS.glob("p2-linked-context-*") if d.is_dir() for p in d.rglob("*") if p.is_file())
paths = [p for p in paths if p.name != "p2-linked-context-validation.json"]
result = {"goal_complete": False, "tests": tests, "actual_requests": requests,
    "evidence_files": [fact(p) for p in paths], "prior_evidence_unchanged": len(previous["evidence_files"]),
    "protected_current_files": previous["protected_current_files"], "plan_copies": [fact(p) for p in plans],
    "scope": "navigation verified with actual originals/stores; default Step effects still length/8192/body0; no Run/business HTTP; old seed byte compatibility only"}
(DOCS / "p2-linked-context-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"tests": tests, "evidence_files": len(paths), "prior_evidence_unchanged": result["prior_evidence_unchanged"], "actual_requests": requests}, ensure_ascii=False))

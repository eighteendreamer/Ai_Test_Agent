"""Audit the actual scene-knowledge acceptance without rewriting old evidence."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


suite = ET.parse(DOCS / "p5-scene-knowledge-complete.xml").getroot().find("testsuite")
assert tuple(int(suite.attrib[key]) for key in ("tests", "failures", "skipped", "errors")) == (1, 0, 0, 0)
regression = ET.parse(DOCS / "p5-scene-knowledge-regression-delivery.xml").getroot().find("testsuite")
assert tuple(int(regression.attrib[key]) for key in ("tests", "failures", "skipped", "errors")) == (63, 0, 2, 0)
failed = (DOCS / "p5-scene-knowledge-before.log").read_text(encoding="utf-8")
assert "AssertionError" in failed and "scene_knowledge" in failed and "chunks" in failed
final = DOCS / "p5-scene-knowledge-complete"
catalog = read(final / "scene-knowledge-catalog.json")["output"]
knowledge = read(final / "scene-knowledge-before-or-fixed.json")["output"]
jobs = read(final / "scene-knowledge-tool-jobs.json")
assert catalog["protocol"] == "recording-scene-knowledge-v1"
assert catalog["verification_status"] == "not_executed"
assert catalog["scene_catalog"]["items"]
assert knowledge["scene_knowledge"]["content_hash"] == read(final / "version2.json")["version"]["content_hash"]
assert knowledge["verification_status"] == "not_executed"
version = read(final / "version2.json")["version"]
assert knowledge["scene_knowledge"]["content"] == version["content"]
assert knowledge["scene_knowledge"]["source"] == version["source"]
registered = read(final / "scene-knowledge-registered.json")
assert registered["output"] == knowledge
assert read(final / "scene-knowledge-cross-project.json")["status"] == "failed"
for stage in ("restart", "archived"):
    recovery = read(final / f"scene-knowledge-{stage}.json")
    assert recovery["replayed"] and recovery["replay"]["job_id"] == registered["job_id"]
    assert recovery["replay"]["output"] == recovery["fresh"]["output"] == knowledge
assert sum(item["status"] == "completed" for item in jobs) == 2
assert sum(item["status"] == "failed" for item in jobs) == 1
assert all(item["output_payload"].get("protocol") == "recording-scene-knowledge-v1" for item in jobs if item["status"] == "completed")
facts = read(final / "facts.json")
assert facts["original_events_unchanged"] and facts["postgres_original_rows_unchanged"]
assert read(final / "page-errors.json") == []
assert (final / "product-build.stderr").read_bytes() == b""
sources = [
    "Agent_Server/src/application/recorder/scene_knowledge.py",
    "Agent_Server/src/application/recorder/scene_service.py",
    "Agent_Server/src/application/runtime/tool_runtime_service.py",
    "Agent_Server/src/infrastructure/recording_scene_store.py",
    "Agent_Server/src/main.py",
    "Agent_Server/src/registry/tools.py",
    "Agent_Server/tests/test_scene_knowledge_contract.py",
    "Agent_Server/tests/test_scene_knowledge_live.py",
    "Agent_Server/tests/test_recording_scene_product.py",
    "docs/upgrade_2026-10-03/P5_SCENE_KNOWLEDGE_RESEARCH.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/PLAN.md",
]
artifacts = sorted({file for path in DOCS.glob("p5-scene-knowledge-*")
    for file in ([path] if path.is_file() else path.rglob("*"))
    if file.is_file() and file.name not in ("p5-scene-knowledge-audit.py", "p5-scene-knowledge-validation.json")})
previous = read(DOCS / "p5-recording-scene-validation.json")
for item in previous["artifacts"] + previous["protected_files"]:
    assert digest(ROOT / item["path"])["sha256"] == item["sha256"], item["path"]
assert (DOCS / "p5-scene-knowledge-compile-delivery.log").read_text(encoding="utf-8-sig").strip() == "compileall_exit=0"
plans = [DOCS / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
         Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(plan.read_bytes() == plans[0].read_bytes() for plan in plans)
assert b"\r\r\n" not in plans[0].read_bytes()
result = {
    "baseline": "cc2d74f5",
    "final_junit": suite.attrib,
    "regression_junit": regression.attrib,
    "final_facts": facts,
    "source_hashes": [digest(ROOT / path) for path in sources],
    "artifact_hashes": [digest(path) for path in artifacts],
    "previous_artifacts_checked": len(previous["artifacts"]),
    "protected_files": previous["protected_files"],
    "plans_equal": 3,
    "default_model_invoked": False,
    "verification_status": "not_executed",
    "full_goal_complete": False,
    "not_proven": ["default Agent scene understanding/execution", "planning catalog scene source", "DeepSeek/manual cleaning/omissions",
        "network/iframe/Shadow DOM full association", "failure localization/report", "preparation/unknown effects/revisions/other modes",
        "API renewal/full Agent", "performance capacity/streaming/security", "requirement business review/deployment/migrations"]
}
(DOCS / "p5-scene-knowledge-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"sources": len(sources), "artifacts": len(artifacts), "tests": suite.attrib["tests"], "failures": suite.attrib["failures"]}))

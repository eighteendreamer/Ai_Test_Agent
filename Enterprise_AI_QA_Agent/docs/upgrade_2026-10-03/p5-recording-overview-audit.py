"""Read-only audit for formal-project overview and generation-context evidence."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
assert b"\r\r\n" not in plans[0].read_bytes()

contract = ET.parse(docs / "p5-recording-formal-context-contract3.xml").getroot().find("testsuite")
live = ET.parse(docs / "p5-recording-overview-final.xml").getroot().find("testsuite")
regression = ET.parse(docs / "p5-recording-overview-regression-final.xml").getroot().find("testsuite")
assert contract.attrib["tests"] == "19" and contract.attrib["failures"] == contract.attrib["errors"] == "0"
assert live.attrib["tests"] == "2" and live.attrib["failures"] == live.attrib["errors"] == live.attrib["skipped"] == "0"
assert regression.attrib["failures"] == regression.attrib["errors"] == regression.attrib["skipped"] == "0"
assert regression.attrib["tests"] == "27"
assert "vue-tsc --noEmit" in (docs / "p5-recording-overview-build.log").read_text(encoding="utf-8")
assert "built in" in (docs / "p5-recording-overview-build.log").read_text(encoding="utf-8")
assert "4 passed" in (docs / "p5-recording-overview-frontend.log").read_text(encoding="utf-8")

live_facts = {}
for kind in ("formal-id", "bound"):
    folder = docs / "p5-recording-overview-final" / kind
    overview = read(folder / "project-overview.json")
    graph = overview["graph"]
    assert graph["available"] and graph["page_count"] == 1 and graph["element_count"] == 2
    assert graph["recording_count"] == 1 and graph["action_count"] == 8 and graph["edge_count"] == 24
    facts = read(folder / "facts.json")
    assert facts["events"] == 8 and facts["restart_equal"] and facts["browser_restored"] and facts["pg_events_unchanged"]
    assert not facts["missing_recorded_nodes"] and not facts["dangling_edges"] and not facts["unplanned_api_failures"]
    assert facts["other_project_status"] == 404 and not facts["page_errors"]
    assert read(folder / "pg-event-rows.json") == read(folder / "pg-event-rows-after-restart.json")
    cleanup = read(folder / "owned-graph-cleanup.json")
    assert all(item["after"] == 0 for item in cleanup)
    context = read(folder / "generation-context.json")
    assert context["project_id"] == overview["project"]["id"]
    assert len([n for n in context["nodes"] if n["kind"] == "recording"]) == 1
    assert len([n for n in context["nodes"] if n["kind"] == "action"]) == 8
    assert "完整知识输入😀" * 40 not in json.dumps(context, ensure_ascii=False)
    live_facts[kind] = {"project_overview_graph": graph, "facts": facts, "cleanup": cleanup}

previous = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json",
             "p5-security-report-validation.json", "p5-perf-report-validation.json", "p5-recording-knowledge-validation.json"):
    items = read(docs / name)["artifacts"]
    for item in items:
        assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]
    previous[name] = len(items)
perf = read(docs / "p5-perf-evidence-validation.json")
previous["performance_evidence"] = len(perf["artifact_hashes"])
for path, expected_hash in perf["artifact_hashes"].items():
    assert digest(root / path)["sha256"] == expected_hash, path
protected = read(docs / "p5-security-evidence-validation.json")["protected_files"]
for item in protected:
    assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]

artifacts = sorted(p for p in docs.glob("p5-recording-overview-*") if p.is_file() and p.name != "p5-recording-overview-validation.json")
artifacts += sorted(p for folder in docs.glob("p5-recording-overview-*") if folder.is_dir() for p in folder.rglob("*") if p.is_file())
artifacts += sorted(docs.glob("p5-recording-formal-context-*"))
sources = ["Agent_Server/src/application/projects/project_overview_service.py", "Agent_Server/src/application/knowledge/knowledge_graph_service.py",
    "Agent_Server/src/application/test_cases/generation_pipeline.py", "Agent_Server/src/schemas/project.py",
    "Agent_Server/tests/test_project_overview.py", "Agent_Server/tests/test_test_case_generation_pipeline.py",
    "Agent_Server/tests/test_knowledge_graph_read_only.py", "Agent_Server/tests/test_recording_knowledge_product.py",
    "agent_web/src/types.ts", "agent_web/src/views/ProjectsView.vue",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P5_RECORDING_KNOWLEDGE_PRODUCT_RESEARCH.md", "docs/upgrade_2026-10-03/.gitattributes"]
result = {"baseline": "6502b74", "contract": contract.attrib, "live_product": live.attrib, "regression": regression.attrib,
    "frontend": "4 passed in 1 file", "build": "npm run build; vue-tsc and Vite exit 0", "compileall": "exit0",
    "live_facts": live_facts, "plans_equal": 3, "previous_artifacts_checked": previous,
    "protected_files": protected, "sources": [digest(root / item) for item in sources],
    "artifacts": [digest(item) for item in artifacts], "default_step_deferred": True, "full_goal_complete": False,
    "not_proven": ["default AI generation/execution", "scene/step/business alias editing", "failure localization/report",
        "manual DeepSeek capture", "complex cleaning/omission and cross-document/Shadow DOM", "API renewal",
        "performance capacity and AI stream", "other security tool faults", "production historical migration"]}
(docs / "p5-recording-overview-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "contract": contract.attrib, "live_product": live.attrib,
    "plans_equal": 3, "previous_artifacts_checked": previous}))

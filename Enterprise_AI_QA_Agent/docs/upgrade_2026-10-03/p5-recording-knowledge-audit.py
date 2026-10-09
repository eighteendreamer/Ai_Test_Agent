"""Read-only audit of actual new capture/product facts and preserved evidence."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


previous = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json",
             "p5-security-report-validation.json", "p5-perf-report-validation.json"):
    items = read(docs / name)["artifacts"]
    for item in items:
        assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
    previous[name] = len(items)
perf = read(docs / "p5-perf-evidence-validation.json")
for path, digest in perf["artifact_hashes"].items():
    assert fingerprint(root / path)["sha256"] == digest, path
previous["performance_evidence"] = len(perf["artifact_hashes"])
protected = read(docs / "p5-security-evidence-validation.json")["protected_files"]
for item in protected:
    assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
assert b"\r\r\n" not in plans[0].read_bytes()

before = docs / "p5-recording-knowledge-before2"
assert read(before / "formal-id/knowledge-http.json")["status"] == 409
problems = read(before / "bound/projection-problems.json")
assert len(problems["missing_recorded_nodes"]) == 9 and problems["dangling_edges"]

facts_by_kind = {}
for kind in ("formal-id", "bound"):
    folder = docs / "p5-recording-knowledge-restored" / kind
    facts = read(folder / "facts.json")
    assert facts["events"] == 8 and facts["restart_equal"] and facts["browser_restored"] and facts["pg_events_unchanged"]
    assert not facts["missing_recorded_nodes"] and not facts["dangling_edges"]
    assert facts["other_project_status"] == 404 and not facts["page_errors"] and not facts["unplanned_api_failures"]
    for failure in facts["planned_restart_failures"]:
        assert failure["method"] == "GET" and "/sessions/" in failure["url"] and failure["status"] == 500
    detail = read(folder / "pg-recording-detail.json")
    events = detail["events"]
    assert len(events) == 8 and len([e for e in events if e["type"] == "click"]) == 2
    assert any(e["type"] == "fill" and e["value"] == "完整知识输入😀" * 40 for e in events)
    assert read(folder / "pg-event-rows.json") == read(folder / "pg-event-rows-after-restart.json")
    knowledge = read(folder / "knowledge-graph.json")
    summary = knowledge["summary"]
    expected = {"page_count": 1, "element_count": 2, "entity_count": 0, "recording_count": 1, "action_count": 8, "edge_count": 24}
    assert {key: summary[key] for key in expected} == expected
    assert summary["project_name"] == "录制知识项目"
    own = next(s for s in read(folder / "knowledge-projects.json") if s["project_id"] == summary["project_id"])
    assert own["action_count"] == 8 and own["recording_count"] == 1
    ids = {n["id"] for n in knowledge["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in knowledge["edges"])
    cleanup = read(folder / "owned-graph-cleanup.json")
    assert [item["before"] for item in cleanup] == [12, 0]
    assert all(item["after"] == 0 for item in cleanup)
    build = (folder / "product-build.stdout").read_text(encoding="utf-8")
    assert "vue-tsc --noEmit" in build and "built in" in build
    assert (folder / "product-build.stderr").stat().st_size == 0
    facts_by_kind[kind] = {"summary": summary, "cleanup": cleanup,
        "planned_restart_failures": facts["planned_restart_failures"], "unplanned_api_failures": [], "pg_events_unchanged": True}

tests = {}
for name in ("before", "before2", "verified", "final", "restored", "regression", "consumers"):
    suite = ET.parse(docs / ("p5-recording-knowledge-" + name + ".xml")).getroot().find("testsuite")
    tests[name] = dict(suite.attrib)
assert tests["before"]["failures"] == "1" and tests["before2"]["failures"] == "2"
assert tests["restored"]["tests"] == "2" and tests["restored"]["failures"] == tests["restored"]["errors"] == tests["restored"]["skipped"] == "0"
assert tests["regression"]["tests"] == "57" and tests["regression"]["skipped"] == "2" and tests["regression"]["failures"] == "0"
assert tests["consumers"]["tests"] == "9" and tests["consumers"]["failures"] == tests["consumers"]["skipped"] == "0"
assert "4 passed" in (docs / "p5-recording-knowledge-frontend.log").read_text(encoding="utf-8")
artifacts = sorted(p for p in docs.glob("p5-recording-knowledge-*") if p.is_file() and p.name != "p5-recording-knowledge-validation.json")
artifacts += sorted(p for folder in docs.glob("p5-recording-knowledge-*") if folder.is_dir() for p in folder.rglob("*") if p.is_file())
sources = ["Agent_Server/src/application/knowledge/knowledge_graph_service.py", "Agent_Server/src/schemas/knowledge.py",
    "Agent_Server/tests/test_recording_knowledge_graph_projection.py", "Agent_Server/tests/test_recording_knowledge_product.py",
    "agent_web/src/types.ts", "agent_web/src/views/KnowledgeView.vue", "agent_web/src/locales/zh-CN.ts",
    "agent_web/src/locales/zh-TW.ts", "agent_web/src/locales/en-US.ts", "docs/upgrade_2026-10-03/PLAN.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/P5_RECORDING_KNOWLEDGE_PRODUCT_RESEARCH.md"]
result = {"baseline": "adf51172", "tests": tests, "frontend": "4 passed in 1 file", "actual_build": 2,
    "compileall": "exit0", "facts": facts_by_kind, "plans_equal": 3, "previous_artifacts_checked": previous,
    "protected_files": protected, "sources": [fingerprint(root / p) for p in sources], "artifacts": [fingerprint(p) for p in artifacts],
    "default_step_deferred": True, "full_goal_complete": False,
    "not_proven": ["manual DeepSeek business capture", "default AI knowledge use and assertions", "complex cleaning and omissions",
        "scene/step/business alias editing and failure reports", "unbound project overview", "project list capacity/pagination",
        "full recording events in generic generation context", "Agent API renewal", "performance capacity and AI stream",
        "other security tool faults", "business quality and production historical migrations"]}
(docs / "p5-recording-knowledge-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "previous_checked": previous, "plans_equal": 3, "product_tests": tests["restored"]}))

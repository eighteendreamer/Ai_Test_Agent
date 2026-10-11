"""Verify actual scene-source identity, durable recovery and old evidence."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT/"docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    raw = path.read_bytes()
    return {"path":path.relative_to(ROOT).as_posix(),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def counts(name, expected):
    result = ET.parse(DOCS/f"p5-scene-planning-{name}.xml").getroot().find("testsuite").attrib
    assert tuple(int(result[k]) for k in ("tests","failures","skipped","errors")) == expected
    return result


suites = {"before":counts("before",(1,1,0,0)),"final":counts("final",(2,0,0,0)),
    "regression":counts("regression",(62,0,3,0)),"contract":counts("contract",(5,0,0,0)),
    "reference-final":counts("reference-final",(4,4,0,0)),
    "reference-complete":counts("reference-complete",(4,0,0,0)),
    "regression-final":counts("regression-final",(72,0,3,0)),
    "worker-before":counts("worker-before",(1,1,0,0)),
    "worker-complete":counts("worker-complete",(5,0,0,0))}
assert "短引用目录的已读证据哈希不一致" in (DOCS/"p5-scene-planning-reference-before.log").read_text(encoding="utf-8-sig")
before = read(DOCS/"p5-scene-planning-before/scene-planning-initial-pg.json")
assert before["checkpoint"]["linked_sources"]["entries"][0]["error"] == "该来源不是可读取的文档引用"
assert not before["checkpoint"]["linked_sources"]["entries"][0]["read"]
facts = {}
for name in ("basis-ack","read-ack","basis-ack-refs","read-ack-refs"):
    folder = DOCS/"p5-scene-planning-reference-complete"/name
    first = read(folder/"scene-planning-initial-pg.json")
    last = read(folder/"scene-planning-recovered-pg.json")
    version = read(folder/"version2.json")["version"]
    progress = first["checkpoint"]["linked_sources"]
    assert progress["next_index"] == (0 if name.startswith("basis-ack") else 1)
    assert first["checkpoint"]["plan"]["rounds"] == []
    assert first["seed"] == last["seed"]
    assert last["seed"]["plan"]["config_snapshot"]["policy"]["linked_source_protocol"] == "case-planning-linked-sources-v2"
    assert first["seed"]["plan"]["prompt_version"] == "case-planning-linked-scenes-v12"
    basis = progress["entries"][0]["basis"]
    assert (basis["scene_id"],basis["version_id"],basis["content_hash"]) == (version["scene_id"],version["id"],version["content_hash"])
    scene = next(e for e in last["checkpoint"]["plan"]["evidence"] if e["kind"] == "scene")
    assert scene["payload"]["content"] == version["content"]
    assert scene["payload"]["source"] == version["source"]
    assert scene["version"] == version["id"] and scene["content_hash"] == version["content_hash"]
    assert scene["payload"]["verification_status"] == "not_executed"
    canonical = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    assert scene["content_hash"] == canonical({"content":scene["payload"]["content"],"source":scene["payload"]["source"]})
    assert scene["payload_hash"] == canonical(scene["payload"])
    assert read(folder/"scene-planning-hash-rejections.json") == ["alias-projection","native-content","native-source"]
    assert scene["id"] in last["checkpoint"]["plan"]["decision"]["items"][0]["evidence_ids"]
    effects = last["checkpoint"]["plan"]["rounds"][-1]
    wire = effects["decision"]["bindings"][0]["evidence_ids"]
    if name.endswith("-refs"):
        catalog = effects["evidence_reference_catalog"]
        key, entry = next((k,v) for k,v in catalog["evidence"].items() if v["kind"] == "scene")
        assert wire == [key] and entry["evidence_id"] == scene["id"]
        assert entry["payload_hash"] == scene["payload_hash"] and entry["content_hash"] == scene["content_hash"]
        assert last["seed"]["plan"]["config_snapshot"]["policy"]["evidence_reference_protocol"] == "case-planning-evidence-references-v1"
    else:
        assert wire == [scene["id"]] and "evidence_reference_catalog" not in effects
    for alias in scene["payload"]["aliases"]:
        original = next(s for s in version["source"]["original_draft"]["steps"] if s["data"]["seq"] == alias["source_seq"])
        assert alias["target"] == original["data"]["event"]["target"]
        assert alias["page"] == original["data"]["event"]["page"]
    completed = read(folder/"scene-planning-completed.json")
    assert completed["status"] == "completed" and completed["attempt_no"] == completed["round_count"] == 2
    assert last["checkpoint"]["plan"]["status"] == "planned"
    assert read(folder/"scene-planning-checkpoint-rejections.json") == ["changed-hash","missing-progress"]
    failures = read(folder/"scene-planning-source-rejections.json")
    assert {f["fault"] for f in failures} == {"missing-version","missing-hash","wrong-hash","foreign-project"}
    for failure in failures:
        assert failure["checkpoint"]["plan"]["status"] == "no_match"
        entry = failure["checkpoint"]["linked_sources"]["entries"][0]
        assert entry["error"] and not entry["read"] and entry["basis"] is None
        assert not any(e["kind"] == "scene" for e in failure["checkpoint"]["plan"]["evidence"])
    product = read(folder/"facts.json")
    assert product["original_events_unchanged"] and product["postgres_original_rows_unchanged"]
    assert read(folder/"page-errors.json") == [] and (folder/"product-build.stderr").read_bytes() == b""
    facts[name] = {"status":"planned","source_version":version["id"],"attempts":2,"controlled_planning_rounds":2,
        "source_rejections":4,"checkpoint_rejections":2,"hash_rejections":3,"short_references":name.endswith("-refs"),"product":product}
document = read(DOCS/"p5-scene-planning-document-final/verification.json")
assert document["model_requests"] == 1 and document["terminal_additional_requests"] == 0
assert document["actual_mysql"]["policy"]["linked_source_protocol"] == "case-planning-linked-sources-v2"
worker = DOCS/"p5-scene-planning-worker-complete"
assert "场景固定来源不属于当前项目或读取尚未配置" in (DOCS/"p5-scene-planning-worker-before.log").read_text(encoding="utf-8-sig")
reads = read(worker/"scene-planning-worker-reads.json")
assert [item["stage"] for item in reads] == ["initial","restart"]
assert reads[0]["evidence"] == reads[1]["evidence"]
assert all(item["production_bootstrap"] and item["model_invocations"] == 0 for item in reads)
assert read(worker/"facts.json")["postgres_original_rows_unchanged"]
assert read(worker/"page-errors.json") == [] and (worker/"product-build.stderr").read_bytes() == b""
assert "68 passed" in (DOCS/"p5-scene-planning-frontend-final.log").read_text(encoding="utf-8-sig")
assert (DOCS/"p5-scene-planning-compile-worker-final.log").read_text(encoding="utf-8-sig").strip() == "compileall_exit=0"
old = read(DOCS/"p5-scene-knowledge-validation.json")
for item in old["artifact_hashes"] + old["protected_files"]:
    assert digest(ROOT/item["path"])["sha256"] == item["sha256"], item["path"]
plans = [DOCS/"PLAN.md",Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans) and b"\r\r\n" not in plans[0].read_bytes()
sources = ["Agent_Server/src/application/recorder/scene_knowledge.py",
    *[f"Agent_Server/src/application/test_cases/{f}.py" for f in ("planning_evidence_references","planning_linked_sources","planning_sources","planning_stages","planning_workflow")],
    "Agent_Server/src/main.py","Agent_Server/src/runtime/postgres_planning_task_store.py",
    "Agent_Server/src/runtime/planning_worker_runtime.py",
    "Agent_Server/src/schemas/case_planning.py","Agent_Server/src/schemas/case_planning_sources.py",
    "Agent_Server/tests/test_scene_planning_contract.py","Agent_Server/tests/test_scene_planning_live.py",
    "Agent_Server/tests/test_scene_planning_worker_live.py",
    "agent_web/src/features/projects/CasePlanDialog.vue","agent_web/src/features/projects/CasePlanDialog.test.ts",
    "agent_web/src/types.ts","docs/upgrade_2026-10-03/P5_SCENE_PLANNING_RESEARCH.md",
    "docs/upgrade_2026-10-03/PLAN.md","docs/upgrade_2026-10-03/IMPLEMENTATION.md","docs/upgrade_2026-10-03/.gitattributes"]
artifacts = sorted({f for p in DOCS.glob("p5-scene-planning-*") for f in ([p] if p.is_file() else p.rglob("*"))
    if f.is_file() and f.name not in ("p5-scene-planning-audit.py","p5-scene-planning-validation.json")})
result = {"baseline":"fee770f0","junit":suites,"facts":facts,"source_hashes":[digest(ROOT/p) for p in sources],
    "worker_fixed_reads":reads,
    "artifact_hashes":[digest(p) for p in artifacts],"previous_artifacts_checked":len(old["artifact_hashes"]),
    "protected_files":old["protected_files"],"plans_equal":3,"controlled_response":True,"default_model_invoked":False,
    "full_goal_complete":False,"not_proven":["unlinked scene discovery/query during planning","default Agent knowledge understanding/execution",
        "manual DeepSeek/cleaning/omissions","complex response association/failure report","preparation/unknown effects/revisions/other modes",
        "API renewal/full Agent","performance capacity/streaming/security","business review/deployment/migrations/full HTTP tracing"]}
(DOCS/"p5-scene-planning-validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"artifacts":len(artifacts),"previous_artifacts":len(old["artifact_hashes"]),"sources":len(sources),"final_tests":4}))

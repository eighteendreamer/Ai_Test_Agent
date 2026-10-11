"""Audit actual query/resume data and immutable previous originals."""
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
    result = ET.parse(DOCS/f"p5-scene-query-{name}.xml").getroot().find("testsuite").attrib
    assert tuple(int(result[k]) for k in ("tests","failures","skipped","errors")) == expected
    return result


suites = {"before":counts("before",(1,1,0,0)),"verified":counts("verified",(4,4,0,0)),
    "complete":counts("complete",(4,0,0,0)),"contract":counts("contract",(12,0,0,0)),
    "regression-final":counts("regression-final",(88,0,3,0))}
before = read(DOCS/"p5-scene-query-before/scene-query-before.json")
assert before["query"] is None and "literal_error" in before["schema_error"] and before["actual_pg_catalog"]["items"]
facts = {}
for stage in ("logic","effects"):
    for fault in ("pending-read","read-ack"):
        name = stage+"-"+fault; folder = DOCS/"p5-scene-query-complete"/name
        first = read(folder/"scene-query-initial-pg.json")
        last = read(folder/"scene-query-recovered-pg.json")
        version = read(folder/"version2.json")["version"]
        query = first["checkpoint"]["pending_decision"]["queries"][0]
        assert first["checkpoint"]["phase"] == "query" and query["kind"] == "scene"
        assert query["source_id"] == version["scene_id"] and query["version_id"] == version["id"] and query["content_hash"] == version["content_hash"]
        assert first["checkpoint"]["query_index"] == (0 if fault == "pending-read" else 1)
        assert first["seed"] == last["seed"]
        assert first["seed"]["plan"]["prompt_version"] == "case-planning-scene-query-v13"
        policy = first["seed"]["plan"]["config_snapshot"]["policy"]
        assert policy["scene_query_protocol"] == "case-planning-scene-query-v1" and "linked_source_protocol" not in policy
        scene = next(e for e in last["checkpoint"]["plan"]["evidence"] if e["kind"] == "scene")
        case = next(e for e in last["checkpoint"]["plan"]["evidence"] if e["kind"] == "case")
        assert not any(s["source_type"] == "ui_recording_scene" for s in case["payload"]["source_refs"])
        assert case["payload"]["content"]["未知嵌套"]["输入"] == [0,False,"😀"]
        assert scene["payload"]["content"] == version["content"] and scene["payload"]["source"] == version["source"]
        assert scene["version"] == version["id"] and scene["content_hash"] == version["content_hash"]
        assert scene["payload"]["verification_status"] == "not_executed"
        assert scene["id"] in last["checkpoint"]["plan"]["decision"]["items"][0]["evidence_ids"]
        rounds = last["checkpoint"]["plan"]["rounds"]
        assert len(rounds) == 5
        directory = [r for r in rounds if r["decision"].get("queries") and r["decision"]["queries"][0]["kind"] == "scenes"]
        assert [r["decision"]["queries"][0]["offset"] for r in directory] == [0,1]
        assert [r["query_results"][0]["has_more"] for r in directory] == [True,False]
        assert all(r["query_results"][0]["evidence_ids"] == [] for r in directory)
        assert all(r["planning_stage"] == stage for r in directory)
        if stage == "effects":
            catalog = rounds[-1]["evidence_reference_catalog"]
            key = next(k for k,v in catalog["evidence"].items() if v["evidence_id"] == scene["id"])
            assert rounds[-1]["decision"]["bindings"][0]["evidence_ids"] == [key]
        completed = read(folder/"scene-query-completed.json")
        assert completed["status"] == "completed" and completed["attempt_no"] == 2 and completed["round_count"] == 5
        execution = read(folder/"scene-query-execution-facts.json")
        assert execution["controlled_model_calls"] == 5 and execution["terminal_additional_calls"] == 0 and execution["no_authored_scene_reference"]
        assert len(execution["fixed_read_calls"]) == 1 and execution["fixed_read_calls"][0]["version_id"] == version["id"]
        assert read(folder/"scene-query-rejections.json") == ["foreign-project","wrong-hash","disabled-policy"]
        product = read(folder/"facts.json")
        assert product["original_events_unchanged"] and product["postgres_original_rows_unchanged"] and product["scene_heads"] == 3
        assert read(folder/"page-errors.json") == [] and (folder/"product-build.stderr").read_bytes() == b""
        facts[name] = {"status":"planned","fixed_read_calls":1,"controlled_model_calls":5,"attempts":2,"original_events_unchanged":True}
assert "73 passed" in (DOCS/"p5-scene-query-frontend.log").read_text(encoding="utf-8-sig")
assert (DOCS/"p5-scene-query-compile.log").read_text(encoding="utf-8-sig").strip() == "compileall_exit=0"
old = read(DOCS/"p5-scene-planning-validation.json")
older = read(DOCS/"p5-scene-knowledge-validation.json")
for item in old["artifact_hashes"] + older["artifact_hashes"] + old["protected_files"]:
    assert digest(ROOT/item["path"])["sha256"] == item["sha256"], item["path"]
plans = [DOCS/"PLAN.md",Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans) and b"\r\r\n" not in plans[0].read_bytes()
sources = [*[f"Agent_Server/src/application/test_cases/{f}.py" for f in ("planning_evidence_references","planning_service","planning_sources","planning_stages","planning_workflow")],
    *[f"Agent_Server/src/schemas/{f}.py" for f in ("case_planning","case_planning_scenes","case_planning_task")],
    *[f"Agent_Server/tests/{f}.py" for f in ("test_scene_query_live","test_scene_query_contract","test_recording_scene_product")],
    "agent_web/src/types.ts","agent_web/src/features/projects/CasePlanDialog.vue","agent_web/src/features/projects/CasePlanDialog.test.ts",
    "docs/upgrade_2026-10-03/P5_SCENE_QUERY_RESEARCH.md","docs/upgrade_2026-10-03/PLAN.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md","docs/upgrade_2026-10-03/.gitattributes"]
artifacts = sorted({f for p in DOCS.glob("p5-scene-query-*") for f in ([p] if p.is_file() else p.rglob("*"))
    if f.is_file() and f.name not in ("p5-scene-query-audit.py","p5-scene-query-validation.json")})
result = {"baseline":"b8c69d4e","junit":suites,"facts":facts,"source_hashes":[digest(ROOT/p) for p in sources],
    "artifact_hashes":[digest(p) for p in artifacts],"previous_artifacts_checked":len(old["artifact_hashes"])+len(older["artifact_hashes"]),
    "protected_files":old["protected_files"],"plans_equal":3,"controlled_response":True,"default_model_invoked":False,
    "full_goal_complete":False,"not_proven":["default AI autonomous scene choice/understanding/assertions","independent planning CLI/process kill scene queries",
        "scene expected sources in technical effect declarations","manual DeepSeek/cleaning/omissions","complex response association/failure reports",
        "preparations/unknown effects/revisions/other modes","API full Agent renewal","performance capacity/streaming/security","business review/deployment/migrations/full HTTP tracing"]}
(DOCS/"p5-scene-query-validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"artifacts":len(artifacts),"previous_artifacts":result["previous_artifacts_checked"],"sources":len(sources),"live_tests":4}))

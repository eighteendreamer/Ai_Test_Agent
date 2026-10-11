"""Audit real CLI process recovery without rewriting earlier validation files."""
from datetime import datetime
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
    result = ET.parse(DOCS/f"p5-scene-cli-{name}.xml").getroot().find("testsuite").attrib
    assert tuple(int(result[k]) for k in ("tests","failures","skipped","errors")) == expected,(name,result)
    return result


suites = {name:counts(name,expected) for name,expected in {
    "first":(1,1,0,0),"verified":(1,1,0,0),"tree-verified":(1,0,0,0),
    "complete":(4,0,0,0),"contract":(26,0,1,0),"contract-live":(26,0,0,0)}.items()}
facts = {}
for stage in ("logic","effects"):
    for fault in ("pending-read","read-ack"):
        name = stage+"-"+fault
        folder = DOCS/"p5-scene-cli-complete"/name
        first = read(folder/"scene-cli-killed-pg.json")
        expired = read(folder/"scene-cli-natural-expiry-pg.json")
        final = read(folder/"scene-cli-recovered-pg.json")
        terminal = read(folder/"scene-cli-terminal-pg.json")
        marker = read(folder/"scene-cli-commit-marker.json")
        killed = read(folder/"scene-cli-kill.json")
        absent = read(folder/"scene-cli-worker-absent.json")
        version = read(folder/"version2.json")["version"]
        query = first["checkpoint"]["pending_decision"]["queries"][0]
        assert query["kind"] == "scene" and query["source_id"] == version["scene_id"]
        assert query["version_id"] == version["id"] and query["content_hash"] == version["content_hash"]
        assert first["checkpoint"] == marker["checkpoint"] == expired["checkpoint"]
        assert first["checkpoint"]["query_index"] == (0 if fault == "pending-read" else 1)
        assert marker["sql_commit_returned"] and killed["committed_checkpoint_confirmed"]
        assert killed["exit_code"] != 0 and killed["worker_pid"] == marker["pid"]
        assert marker["pid"] == killed["launcher_pid"] or marker["parent_pid"] == killed["launcher_pid"]
        assert absent["exit_code"] == 0 and f'"{marker["pid"]}"' not in absent["stdout"]
        assert killed["pg_lease_seconds"] == 30 and killed["redis_reclaim_idle_ms"] == 1000
        assert datetime.fromisoformat(expired["server_now"]) >= datetime.fromisoformat(expired["lease_expires_at"])
        assert first["seed"] == expired["seed"] == final["seed"] == terminal["seed"]
        assert first["deadline_at"] == final["deadline_at"] == terminal["deadline_at"]
        assert final["status"] == terminal["status"] == "completed" and final["attempt_no"] == 2
        assert final["attempts"][-1]["reason"] == "lease_expired" and first["attempt_no"] == 1
        for key in ("checkpoint","attempt_no","attempt_id","attempts"):
            assert final[key] == terminal[key]
        assert first["seed"]["plan"]["prompt_version"] == "case-planning-scene-query-v13"
        assert first["seed"]["plan"]["config_snapshot"]["policy"]["scene_query_protocol"] == "case-planning-scene-query-v1"
        assert "scene_query_protocol" not in read(folder/"scene-cli-current-policy.json")["policy"]
        assert read(folder/"scene-cli-current-scene.json")["version"]["version"] == 4
        plan = final["checkpoint"]["plan"]
        scene = next(e for e in plan["evidence"] if e["kind"] == "scene")
        case = next(e for e in plan["evidence"] if e["kind"] == "case")
        assert not any(r["source_type"] == "ui_recording_scene" for r in case["payload"]["source_refs"])
        assert case["payload"]["content"]["未知嵌套"]["输入"] == [0,False,"😀"]
        assert scene["version"] == version["id"] and scene["content_hash"] == version["content_hash"]
        assert scene["payload"]["content"] == version["content"] and scene["payload"]["source"] == version["source"]
        assert scene["payload"]["verification_status"] == "not_executed"
        assert scene["id"] in plan["decision"]["items"][0]["evidence_ids"] and plan["status"] == "planned"
        http = read(folder/"scene-cli-http.json")
        assert len(http) == 4 and read(folder/"scene-cli-provider-errors.json") == []
        assert all(r["path"] == "/v1/chat/completions" and not r["body"].get("stream",False) for r in http)
        assert len({r["context"]["round"] for r in http}) == 4
        if stage == "effects":
            catalog = http[-1]["context"]["evidence_reference_catalog"]["evidence"]
            key = next(k for k,v in catalog.items() if v["evidence_id"] == scene["id"])
            decision = json.loads(http[-1]["response"]["choices"][0]["message"]["content"])
            assert decision["bindings"][0]["evidence_ids"] == [key]
        reads = [json.loads(line) for line in (folder/"scene-cli-reads.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(reads) == 1 and reads[0]["basis"]["version_id"] == version["id"]
        assert (reads[0]["pid"] == marker["pid"]) == (fault == "read-ack")
        for worker in ("after-kill","terminal"):
            assert "case_planning_processed=1" in (folder/f"scene-cli-{worker}.stdout").read_text(encoding="utf-8")
        product = read(folder/"facts.json")
        assert product["original_events_unchanged"] and product["postgres_original_rows_unchanged"]
        assert read(folder/"page-errors.json") == []
        assert (folder/"product-build.stderr").read_bytes() == b""
        facts[name] = read(folder/"scene-cli-facts.json")
assert (DOCS/"p5-scene-cli-compile.log").read_text(encoding="utf-8-sig").strip() == "compileall_exit=0"
previous = [read(DOCS/name) for name in ("p5-scene-query-validation.json","p5-scene-planning-validation.json",
    "p5-scene-knowledge-validation.json")]
old_artifacts = [item for batch in previous for item in batch["artifact_hashes"]]
protected = previous[0]["protected_files"]
for item in old_artifacts+protected:
    assert digest(ROOT/item["path"])["sha256"] == item["sha256"],item["path"]
plans = [DOCS/"PLAN.md",Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans) and b"\r\r\n" not in plans[0].read_bytes()
sources = ["Agent_Server/tests/scene_cli_probe.py","Agent_Server/tests/test_scene_cli_live.py",
    "docs/upgrade_2026-10-03/P5_SCENE_CLI_RESEARCH.md","docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/PLAN.md","docs/upgrade_2026-10-03/IMPLEMENTATION.md"]
artifacts = sorted({f for p in DOCS.glob("p5-scene-cli-*") for f in ([p] if p.is_file() else p.rglob("*"))
    if f.is_file() and f.name not in ("p5-scene-cli-audit.py","p5-scene-cli-validation.json")})
result = {"baseline":"1776cdd9","junit":suites,"facts":facts,"artifact_hashes":[digest(p) for p in artifacts],
    "source_hashes":[digest(ROOT/p) for p in sources],"previous_artifacts_checked":len(old_artifacts),
    "protected_files":protected,"plans_equal":3,"controlled_local_http_model":True,"default_model_invoked":False,
    "full_goal_complete":False,"not_proven":["default AI autonomous scene choice/understanding/execution/assertions",
        "scene expected sources in technical effect declarations","manual DeepSeek/cleaning/omissions",
        "complex response association/failure reports","preparations/unknown effects/revisions/other modes",
        "API full Agent renewal","performance capacity/streaming/security","business review/deployment/migrations/full HTTP tracing"]}
(DOCS/"p5-scene-cli-validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"artifacts":len(artifacts),"previous_artifacts":len(old_artifacts),"live_cases":4,
    "old_and_new_contracts":26,"plans_equal":3}))

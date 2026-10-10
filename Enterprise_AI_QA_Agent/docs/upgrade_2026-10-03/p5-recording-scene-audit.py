"""Acceptance scope and byte preservation; never rewrite previous verification records."""
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

root=Path(__file__).resolve().parents[2]
docs=root/"docs/upgrade_2026-10-03"


def read(path): return json.loads(path.read_bytes())
def digest(path):
    data=path.read_bytes()
    return {"path":path.relative_to(root).as_posix(),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}


counts={}
for name,tests,failed,skipped in [("before",1,1,0),("api-first",1,1,0),("product-first",1,1,0),
    ("product-verified",1,1,0),("product-restored",1,1,0),("product-final",1,1,0),
    ("product-delivery",1,0,0),("product-complete",1,0,0),("product-integrity",1,0,0),("projection-before",1,1,0),
    ("regression",73,0,2),("regression-final",74,0,2)]:
    suite=ET.parse(docs/f"p5-recording-scene-{name}.xml").getroot().find("testsuite")
    assert tuple(int(suite.attrib[k]) for k in ("tests","failures","skipped","errors"))==(tests,failed,skipped,0),(name,suite.attrib)
    counts[name]=suite.attrib
assert "13 passed" in (docs/"p5-recording-scene-frontend-delivery.log").read_text(encoding="utf-8")
assert "error TS" not in (docs/"p5-recording-scene-typecheck-final.log").read_text(encoding="utf-8")
assert "compileall_exit=0" in (docs/"p5-recording-scene-compile-final.log").read_text(encoding="utf-8")
assert "compileall_exit=0" in (docs/"p5-recording-scene-compile-integrity.log").read_text(encoding="utf-8-sig")
live=docs/"p5-recording-scene-product-integrity"
facts=read(live/"facts.json")
assert facts["scene_heads"]==2 and facts["scene_versions"]==6 and facts["original_events_unchanged"]
assert facts["project_isolation"]==404 and facts["archived_write"]==409 and facts["default_ai_invoked"] is False
assert read(live/"page-errors.json")==[]
lost=read(live/"committed-response-lost.json")
assert len(lost)==1 and lost[0]["status"]==201
assert "built in" in (live/"product-build.stdout").read_text(encoding="utf-8")
assert (live/"product-build.stderr").read_bytes()==b""
rows=read(live/"postgres-final-scenes.json")
assert len(rows["ui_recording_scene"])==2 and len(rows["ui_recording_scene_version"])==6
assert len(rows["agent_test_case_versions"])==2
for row in rows["ui_recording_scene_version"]:
    payload=row["payload"]
    encoded=json.dumps({"content":payload["content"],"source":payload["source"]},sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
    assert hashlib.sha256(encoded).hexdigest()==payload["content_hash"]
    assert row["id"]==payload["id"] and row["request_hash"]==payload["request_hash"]
raw=read(live/"original-recording.json")
assert [r["seq"] for r in sorted(rows["ui_recording_event"],key=lambda r:r["seq"])]==[r["seq"] for r in raw["events"]]
original_rows=read(live/"postgres-original-events.json")
assert sorted(rows["ui_recording_event"],key=lambda r:r["seq"])==original_rows
assert facts["postgres_original_rows_unchanged"]
for row,event in zip(original_rows,raw["events"]):
    # Existing RecordingStore._row_to_event uses the indexed TIMESTAMPTZ column
    # and ensure_utc_datetime returns naive UTC; the JSON payload retains Z.
    assert {k:v for k,v in row["payload"].items() if k!="timestamp"}=={k:v for k,v in event.items() if k!="timestamp"}
    column=datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc).replace(tzinfo=None)
    assert column==datetime.fromisoformat(event["timestamp"])
    assert column==datetime.fromisoformat(row["payload"]["timestamp"]).astimezone(timezone.utc).replace(tzinfo=None)
draft=read(live/"formal-draft.json")
assert draft["case"]["lifecycle_status"]=="draft"
scene=draft["version"]["test_data"]["scene"]
assert scene["description"] and scene["aliases"][0]["target"] and scene["aliases"][0]["page"]
assert any(r["source_type"]=="ui_recording_scene" and r["version"]==scene["version_id"] for r in draft["version"]["source_refs"])

plans=[docs/"PLAN.md",Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes()==plans[0].read_bytes() for p in plans) and b"\r\r\n" not in plans[0].read_bytes()
old={}
for name in ("p0-model-request-profile-validation.json","p0-model-stream-terminal-validation.json",
    "p0-model-completion-validation.json","p5-recording-overview-validation.json","p5-recording-knowledge-validation.json",
    "p5-perf-report-validation.json","p5-security-report-validation.json","p5-security-evidence-validation.json",
    "p1-template-fixed-version-app-validation.json"):
    artifacts=read(docs/name)["artifacts"]
    for item in artifacts: assert digest(root/item["path"])["sha256"]==item["sha256"],item["path"]
    old[name]=len(artifacts)
perf=read(docs/"p5-perf-evidence-validation.json")["artifact_hashes"]
for path,expected in perf.items(): assert digest(root/path)["sha256"]==expected,path
old["p5-perf-evidence-validation.json"]=len(perf)
protected=read(docs/"p0-model-request-profile-validation.json")["protected_files"]
for item in protected: assert digest(root/item["path"])["sha256"]==item["sha256"],item["path"]
sources=["Agent_Server/src/schemas/recording_scene.py","Agent_Server/src/infrastructure/recording_scene_store.py",
    "Agent_Server/src/application/recorder/scene_service.py","Agent_Server/src/api/routes/recording_scenes.py",
    "Agent_Server/src/api/routes/recordings.py","Agent_Server/src/core/config.py","Agent_Server/src/main.py",
    "Agent_Server/tests/test_recording_scene_contract.py","Agent_Server/tests/test_recording_scene_product.py",
    "Agent_Server/tests/test_recording_scene_draft_projection.py","agent_web/src/features/recorder/sceneApi.ts",
    "agent_web/src/views/RecordingScenesView.vue","agent_web/src/views/RecordingScenesView.test.ts",
    "agent_web/src/views/KnowledgeView.vue","agent_web/src/views/ProjectsView.vue","agent_web/src/router/index.ts",
    "docs/upgrade_2026-10-03/PLAN.md","docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P5_RECORDING_SCENE_RESEARCH.md","docs/upgrade_2026-10-03/.gitattributes"]
artifacts=sorted({p for item in docs.glob("p5-recording-scene-*") for p in ([item] if item.is_file() else item.rglob("*"))
    if p.is_file() and p.name!="p5-recording-scene-validation.json"})
patterns=[re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"Authorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._-]{20,}",re.I)]
for path in artifacts+[root/s for s in sources]: assert not any(p.search(path.read_bytes()) for p in patterns),path
result={"baseline":"5276887c","junit":counts,"live_facts":facts,"plans_equal":3,"previous_artifacts_checked":old,
    "protected_files":protected,"sources":[digest(root/s) for s in sources],"artifacts":[digest(p) for p in artifacts],
    "secret_pattern_hits":0,"full_goal_complete":False,"frontend_tests":13,"compileall":"exit0",
    "not_proven":["default Agent scene knowledge/execution/real DeepSeek","pre-recording planning/cross-recording/selector review",
        "complete cleaning/omissions/network/document/Shadow DOM association/failure reports","archived scene version specialty/capacity/production history/concurrent DDL",
        "preparations/unknown effects/revisions/other modes","API renewal/full Agent/perf capacity/AI streaming/other security tools",
        "business review/deployment/all-phase HTTP/old worker originals","external model compatibility/multimodal/other reasoning protocols"]}
(docs/"p5-recording-scene-validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"artifacts":len(artifacts),"final_product":counts["product-integrity"],"regression":counts["regression-final"],"old":old}))

"""Audit current exact restrictions, actual PG/HTTP evidence and immutable history."""
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from environment_planning_delivery_20261006 import ROOT,DOCS,PLANS,IGNORE_SHA,sha,read,plan_basis,original_run
from environment_decisions_delivery_20261006 import check_review
from src.application.documents.requirement_execution_checks import fixed_result,body_checks
from src.application.test_runs.environment_restrictions import restriction_reviews
from src.runtime.postgres_business_environment_store import environment_digest
from src.schemas.run_management import TestRunItemRecord as RunItemRecord

PREFIX = "p4-environment-restrictions-"
REPORT = DOCS/(PREFIX+"validation.json")
RELATED = {
    "Agent_Server/src/application/test_runs/dependencies.py",
    "Agent_Server/src/application/test_runs/environment_restrictions.py",
    "Agent_Server/src/application/test_runs/execution_service.py",
    "Agent_Server/src/application/test_runs/run_service.py",
    "Agent_Server/src/application/test_runs/run_store.py",
    "Agent_Server/src/runtime/postgres_business_planning.py",
    "Agent_Server/tests/test_business_environment_restrictions.py",
    "Agent_Server/tests/test_business_environment_restrictions_production.py",
    "Agent_Server/tests/business_environment_restrictions_production_scenario.py",
    "Agent_Server/tests/business_applicability_production_scenario.py",
    "Agent_Server/tests/test_document_impact_production.py",
    "docs/upgrade_2026-10-03/PLAN.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_RESTRICTIONS_DESIGN.md",
    "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/environment_restrictions_delivery_20261006.py",
    "docs/upgrade_2026-10-03/p4-environment-restrictions-validation.json",
}


def state(folder,name):
    return read(DOCS/(PREFIX+folder)/("applicability-"+name)/"verification.json")


def check_run(value):
    plan = plan_basis(value["fixed_plan"])
    detail = value["run"]
    run = original_run(detail["run"])
    assert run.plan_snapshot.plan == plan
    for review in value["records"]:
        check_review(review["record"],value["jobs"])
    for raw in detail["results"]:
        result = fixed_result(raw)
        receipt = result.actual.get("environment_restriction")
        if receipt:
            assert result.status == "blocked" and result.actual["business_execution_started"] is False
            assert receipt["sha256"] == environment_digest({k:v for k,v in receipt.items() if k != "sha256"})
            assert receipt["planning_basis_sha256"] == plan.environment.sha256
            assert receipt["snapshot_sha256"] == plan.environment.snapshot.sha256
            entry = RunItemRecord.model_validate(next(row for row in detail["items"] if row["id"] == result.run_item_id))
            reviews = [check_review(r,value["jobs"]) for r in receipt["current_reviews"]]
            assert [r.model_dump(mode="json") for r in restriction_reviews(plan,entry,reviews)] == receipt["reviews"]
            assert receipt["case_id"] == entry.case_id and receipt["case_version_id"] == entry.case_version_id
    return plan,detail


def verify():
    plans = [path.read_bytes() for path in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT/".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)",plans[0].decode("utf-8")):
        target = Path(re.sub(r":\d+$","",link))
        if target == REPORT and "--record" in sys.argv: continue
        assert target.is_file(),link
    previous = read(DOCS/"p4-environment-planning-validation.json")
    prior = {}
    for name in [*previous["previous_raw_evidence_verified"],"p4-environment-planning-validation.json"]:
        items = read(DOCS/name)["raw_files"]
        for row in items:
            data = (ROOT/row["path"]).read_bytes()
            assert len(data) == row["size_bytes"] and sha(data) == row["sha256"],row["path"]
        prior[name] = len(items)
    files = sorted(path for path in DOCS.rglob("*") if path.is_file() and path != REPORT
        and path.relative_to(DOCS).parts[0].startswith(PREFIX))
    raw_files = [{"path":path.relative_to(ROOT).as_posix(),"size_bytes":path.stat().st_size,
        "sha256":sha(path.read_bytes())} for path in files]
    tests = {}
    expected = {"reproduction":(1,1,0,0),"reproduction-captured":(1,1,0,0),"create-first":(3,0,0,0),
        "live-first":(9,0,0,0),"live-final":(12,0,0,0),"production-first":(1,1,0,0),
        "production-captured":(1,1,0,0),"production-final":(1,0,0,0),"regression":(134,0,0,2)}
    for path in DOCS.glob(PREFIX+"*.xml"):
        name = path.stem[len(PREFIX):]
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        result = {k:sum(int(s.get(k,"0")) for s in suites) for k in ["tests","failures","errors","skipped"]}
        if name in expected: assert tuple(result.values()) == expected[name],(name,result)
        else: raise AssertionError((name,result))
        result["time_seconds"] = round(sum(float(s.get("time","0")) for s in suites),3)
        result["skipped_reasons"] = [t.find("skipped").get("message","") for s in suites for t in s.findall("testcase")
            if t.find("skipped") is not None]
        tests[name] = result
    assert set(expected) == set(tests)
    failure = state("reproduction-captured","restriction-create-md")
    assert failure["restrictions_working"] is False and all(row["status"] == "queued" for row in failure["run"]["items"])
    formats = {}
    for ext in ["md","docx","pdf"]:
        value = state("live-final","restriction-independent-"+ext)
        plan,detail = check_run(value)
        assert detail["run"]["status"] == "completed" and len(detail["results"]) == 3
        assert sorted(row["status"] for row in detail["results"]) == ["blocked","blocked","passed"]
        result = fixed_result(value["independent_result"])
        checks = list(body_checks(result,"independent",plan.project_id).values())
        assert [c["actual"] for c in checks] == [200,9,0,False,None]
        assert checks[2]["actual"] == 0 and checks[3]["actual"] is False and checks[4]["actual"] is None
        job = value["independent_job"]
        assert job["input_payload"]["task"]["path"] == plan.environment.snapshot.configuration.payload.base_url+"/rule"
        assert job["id"] in {ref.evidence_id for ref in result.evidence_refs if ref.evidence_type == "tool_job"}
        assert job["output_payload"]["task_result"]["execution_checkpoint"]["http_observation_v1"]["acquisition"] == "network"
        source = value["original_case"]["version"]["source_refs"][0]["metadata"]["parse_snapshot"]["seed"]["original_asset"]
        directory = DOCS/(PREFIX+"live-final")/("applicability-restriction-independent-"+ext)
        data = (directory/source["filename"]).read_bytes()
        assert sha(data) == source["sha256"] and len(data) == source["size_bytes"]
        assert len(value["full_case"]["version"]["content_json"]["steps"]) == 12 and value["blocked_new_HTTP"] == 0
        formats[ext] = {"original_sha256":source["sha256"],"full_case_steps":12,"independent_result_sha256":result.payload_hash}
    for name,phase in [("claim","claim"),("start","start"),("project-lock","start")]:
        value = state("live-final","restriction-"+name)
        _,detail = check_run(value)
        receipts = [r["actual"]["environment_restriction"] for r in detail["results"] if r["actual"].get("environment_restriction")]
        assert len(receipts) == 1 and receipts[0]["phase"] == phase
        if name == "claim": assert value["claimed_count"] == 1
        if name == "start": assert value["actual_committed_reply_lost"] and value["blocked_new_HTTP"] == 0
        if name == "project-lock": assert value["actual_lock_waits"] and all(r["blockers"] for r in value["actual_lock_waits"])
    for name in ["revoke","unknown","other-target","conflict"]:
        value = state("live-final","restriction-"+name)
        _,detail = check_run(value)
        assert not detail["results"]
    recovery = state("live-final","restriction-started-recovery")
    _,detail = check_run(recovery)
    assert not detail["results"] and recovery["new_recovery_HTTP"] == 0
    assert recovery["business_state_change_verified"] is recovery["autonomous_continuation_invoked"] is False
    assert recovery["original_started_attempt"] in detail["attempts"]
    assert detail["attempts"][-1]["checkpoint_payload"] == recovery["original_checkpoint"]
    assert recovery["original_checkpoint"]["business_effect_status"] == "unknown"
    captured = read(DOCS/(PREFIX+"production-captured")/"md-native-finish"/"environment-restriction-run-captured.json")
    assert captured["sql_run"] != captured["detail"]["run"]
    assert original_run(captured["sql_run"]).model_dump(mode="json") == captured["detail"]["run"]
    assert {k for k in captured["sql_run"] if captured["sql_run"][k] != captured["detail"]["run"][k]} == {"updated_at","completed_at"}
    production = DOCS/(PREFIX+"production-final")/"md-native-finish"
    current = read(production/"environment-restriction.json")
    restored = read(production/"environment-restriction-restart.json")
    plan_basis(current["plan"])
    original_run(current["detail"]["run"])
    assert original_run(current["sql_run"]).model_dump(mode="json") == current["detail"]["run"]
    result = fixed_result(current["detail"]["results"][0])
    assert result.status == "blocked" and result.actual["business_execution_started"] is False
    receipt = result.actual["environment_restriction"]
    assert receipt["sha256"] == environment_digest({k:v for k,v in receipt.items() if k != "sha256"})
    assert receipt["reviews"] == [current["additional_reviews"][0]]
    assert current["additional_reviews"][1]["request"]["action"] == "revoke"
    assert current["independent_execution_CLI"] and current["business_HTTP_during_restriction"] == 0
    assert restored["revoked_blocked_result_preserved"] and restored["new_model_HTTP"] == 0
    assert restored["creation_hash"] == current["detail"]["run"]["creation_hash"]
    messages = read(production/"physical-requests.json")
    assert len(messages) == 4 and current["physical_planning_calls"] == 1
    assert json.loads(messages[-1]["messages"][-1]["content"])["business_environment"] == current["plan"]["environment"]
    checkpoints = read(production/"checkpoints.json")
    for ref in [checkpoints["input_checkpoint"],checkpoints["manifest"],checkpoints["reasoning_checkpoint"],
        *[check["result"] for check in checkpoints["checks"]],
        *[call[key] for call in checkpoints["calls"] for key in ["request","response"] if call.get(key)]]:
        data = (production/(ref["sha256"]+".json")).read_bytes()
        assert sha(data) == ref["sha256"] and len(data) == ref["size_bytes"]
    for path in files:
        if path.suffix in {".json",".log",".xml",".stdout",".stderr"}:
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}",path.read_text("utf-8",errors="replace")),path
    return {"research_baseline":"bbea328e","plan_sha256":sha(plans[0]),"plans_byte_identical":True,
        "gitignore_unchanged":True,"previous_raw_evidence_verified":prior,"raw_files":raw_files,"tests":tests,
        "formats":formats,"create_claim_start_exact_restrictions_verified":True,"real_project_lock_race_verified":True,
        "started_checkpoint_preserved_without_new_HTTP":True,"production_physical_model_requests":4,
        "independent_production_terminal_execution_CLI_verified":True,"current_allowed_receipts_implemented":False,
        "started_action_amendment_implemented":False,"new_target_regression_verified":False,
        "formal_environment_page_implemented":False,"external_default_model_tested":False,"full_P0_P5_complete":False}


def staged(result):
    repo = Path(subprocess.check_output(["git","rev-parse","--show-toplevel"],cwd=ROOT,text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()+"/"
    raw = {row["path"]:row for row in result["raw_files"]}
    expected = {prefix+name for name in RELATED|set(raw)}
    index = {name for name in subprocess.check_output(["git","diff","--cached","--name-only","-z"],cwd=repo).decode().split("\0") if name}
    assert index == expected,{"unexpected":sorted(index-expected),"missing":sorted(expected-index)}
    for name,row in raw.items():
        data = subprocess.check_output(["git","show",":"+prefix+name],cwd=repo)
        assert len(data) == row["size_bytes"] and sha(data) == row["sha256"],name
    for name in RELATED:
        data = subprocess.check_output(["git","show",":"+prefix+name],cwd=repo)
        assert data.replace(b"\r\n",b"\n") == (ROOT/name).read_bytes().replace(b"\r\n",b"\n"),name
    return {"scope_verified":True,"files":len(index),"raw_blobs_verified":len(raw)}


if __name__ == "__main__":
    result = verify()
    if "--record" in sys.argv:
        if REPORT.exists(): raise RuntimeError("Preserve the original report")
        REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    else: assert read(REPORT) == result
    summary = {k:result[k] for k in ["plan_sha256","tests","full_P0_P5_complete"]}
    summary.update(raw_evidence_files=len(result["raw_files"]),prior_evidence_files=sum(result["previous_raw_evidence_verified"].values()))
    if "--staged" in sys.argv: summary["index"] = staged(result)
    print(json.dumps(summary,ensure_ascii=False))

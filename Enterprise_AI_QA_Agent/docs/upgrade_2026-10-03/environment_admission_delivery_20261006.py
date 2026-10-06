"""Check original receipts, actual tools/transports and staged immutable evidence."""
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from environment_planning_delivery_20261006 import ROOT,DOCS,PLANS,IGNORE_SHA,sha,read,plan_basis,original_run
from src.application.documents.requirement_execution_checks import fixed_result,body_checks
from src.application.test_runs.environment_admission import fixed_environment_admission
from src.schemas.run_management import TestRunDetail,TestRunAttemptRecord
from src.schemas.case_execution import AgentCaseExecutionContext

PREFIX = "p4-environment-admission-"
REPORT = DOCS/(PREFIX+"validation.json")
RELATED = {
    "Agent_Server/src/application/prompting/prompt_assembly_service.py",
    "Agent_Server/src/application/test_runs/agent_case_assessment.py",
    "Agent_Server/src/application/test_runs/agent_case_context.py",
    "Agent_Server/src/application/test_runs/run_store.py",
    "Agent_Server/src/application/test_runs/environment_admission.py",
    "Agent_Server/src/schemas/case_execution.py",
    "Agent_Server/src/schemas/run_management.py",
    "Agent_Server/src/schemas/business_execution.py",
    "Agent_Server/tests/business_applicability_production_scenario.py",
    "Agent_Server/tests/test_business_environment_restrictions.py",
    "Agent_Server/tests/test_document_impact_calls.py",
    "Agent_Server/tests/test_document_impact_production.py",
    "Agent_Server/tests/test_business_environment_admission.py",
    "Agent_Server/tests/test_business_environment_admission_production.py",
    "Agent_Server/tests/business_environment_admission_production_scenario.py",
    "agent_web/src/types.ts","agent_web/src/services/businessExecution.types.ts",
    "docs/upgrade_2026-10-03/PLAN.md","docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_ADMISSION_DESIGN.md",
    "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/environment_admission_delivery_20261006.py",
    "docs/upgrade_2026-10-03/p4-environment-admission-validation.json",
}


def state(folder,name):
    return read(DOCS/(PREFIX+folder)/("applicability-"+name)/"verification.json")


def receipt(detail,attempt_id):
    detail = TestRunDetail.model_validate(detail)
    attempt = next(a for a in detail.attempts if a.id == attempt_id)
    item = next(i for i in detail.items if i.id == attempt.run_item_id)
    value = fixed_environment_admission(detail.run,item,attempt,detail.attempts)
    assert value is not None
    original_run(detail.run.model_dump(mode="json"))
    return value.model_dump(mode="json")


def verify():
    plans = [p.read_bytes() for p in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT/".gitignore").read_bytes()) == IGNORE_SHA
    prior_report = read(DOCS/"p4-environment-restrictions-validation.json")
    prior = {}
    for name in prior_report["previous_raw_evidence_verified"]:
        items = read(DOCS/name)["raw_files"]
        for row in items:
            data = (ROOT/row["path"]).read_bytes()
            assert len(data) == row["size_bytes"] and sha(data) == row["sha256"],row["path"]
        prior[name] = len(items)
    for row in prior_report["raw_files"]:
        data = (ROOT/row["path"]).read_bytes()
        assert len(data) == row["size_bytes"] and sha(data) == row["sha256"],row["path"]
    prior["p4-environment-restrictions-validation.json"] = len(prior_report["raw_files"])
    files = sorted(p for p in DOCS.rglob("*") if p.is_file() and p != REPORT
        and p.relative_to(DOCS).parts[0].startswith(PREFIX))
    raw_files = [{"path":p.relative_to(ROOT).as_posix(),"size_bytes":p.stat().st_size,"sha256":sha(p.read_bytes())} for p in files]
    tests = {}
    failures = {"reproduction","start-replay-reproduction","production-first"}
    for p in DOCS.glob(PREFIX+"*.xml"):
        name = p.stem[len(PREFIX):]
        root = ET.parse(p).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        value = {k:sum(int(s.get(k,"0")) for s in suites) for k in ["tests","failures","errors","skipped"]}
        assert value["errors"] == 0 and value["failures"] == (1 if name in failures else 0),(name,value)
        value["time_seconds"] = round(sum(float(s.get("time","0")) for s in suites),3)
        value["skipped_reasons"] = [t.find("skipped").get("message","") for s in suites for t in s.findall("testcase")
            if t.find("skipped") is not None]
        tests[name] = value
    assert failures <= set(tests) and {"regression","production-stream"} <= set(tests)
    old = state("reproduction","admission-start-md")
    assert not old["current_confirmation_persisted"]
    assert TestRunAttemptRecord.model_validate(old["attempt"]).model_dump(mode="json") == old["attempt"]
    formats = {}
    for ext in ["md","docx","pdf"]:
        value = state("live-final","admission-result-"+ext)
        admission = value["attempt"]["environment_admission"]
        assert admission["reviews"] == [value["current_review"]]
        assert value["agent_context"]["environment_admission"] == admission
        context = AgentCaseExecutionContext.model_validate(value["agent_context"])
        assert context.execution_json() == value["agent_context"]
        result = fixed_result(value["independent_result"])
        assert result.actual["environment_admission"] == admission and result.status == "passed"
        assert [c["actual"] for c in body_checks(result,"independent",value["fixed_plan"]["project_id"]).values()] == [200,9,0,False,None]
        job = value["independent_job"]
        assert job["output_payload"]["task_result"]["execution_checkpoint"]["http_observation_v1"]["acquisition"] == "network"
        source = value["original_case"]["version"]["source_refs"][0]["metadata"]["parse_snapshot"]["seed"]["original_asset"]
        data = (DOCS/(PREFIX+"live-final")/("applicability-admission-result-"+ext)/source["filename"]).read_bytes()
        assert sha(data) == source["sha256"] and len(data) == source["size_bytes"]
        formats[ext] = {"original_sha256":source["sha256"],"full_case_steps":len(value["full_case"]["version"]["content_json"]["steps"]),
            "admission_sha256":admission["sha256"],"result_sha256":result.payload_hash}
    lost = state("live-final","admission-lost-start-recovered")
    assert receipt(lost["run"],lost["persisted_attempt"]["id"]) == lost["persisted_attempt"]["environment_admission"]
    assert lost["actual_allowed_commit_reply_lost"] and lost["new_replay_HTTP"] == 0
    recovery = state("live-final","admission-checkpoint-recovery")
    assert recovery["original_context"] == recovery["recovered_context"] and recovery["new_recovery_HTTP"] == 0
    assert receipt(recovery["run"],recovery["run"]["attempts"][-1]["id"]) == recovery["original_attempt"]["environment_admission"]
    graph = state("graph-first","admission-agent-graph")
    assert graph["new_recovery_HTTP"] == graph["new_recovery_model_calls"] == 0
    result = fixed_result(graph["actual_result"])
    assert result.actual["environment_admission"] == graph["root_attempt"]["environment_admission"]
    assert receipt(graph["run"],result.attempt_id) == result.actual["environment_admission"]
    failed = read(DOCS/(PREFIX+"production-first")/"md-native-finish/environment-admission-execution-attempt.json")
    assert len(failed["physical_requests"]) == 5 and failed["physical_requests"][-1]["stream"] is True
    assert failed["detail"]["results"][0]["status"] == "error"
    production = DOCS/(PREFIX+"production-stream")/"md-native-finish"
    value = read(production/"environment-admission.json")
    result = fixed_result(value["detail"]["results"][0])
    assert result.status == "passed" and value["actual_HTTP_counts"] == [2,1]
    assert value["physical_model_calls"] == 3 and value["controlled_model"]
    admission = result.actual["environment_admission"]
    assert receipt(value["detail"],admission["execution_attempt_id"]) == admission
    assert admission["reviews"] == value["additional_reviews"]
    assert value["plan"]["environment"]["reviews"] != admission["reviews"]
    assert original_run(value["SQL_run"]).model_dump(mode="json") == value["detail"]["run"]
    assert [c["actual"] for c in body_checks(result,"target",value["plan"]["project_id"]).values()] == [200,7,0,False,None]
    messages = read(production/"physical-requests.json")
    assert len(messages) == 6 and messages == value["physical_requests"]
    assert messages[-2]["stream"] and messages[-1]["stream"]
    assert sum(m["role"] == "tool" for m in messages[-1]["messages"]) == 1
    assert read(production/"environment-admission-restart.json")["new_model_HTTP"] == 0
    checkpoints = read(production/"checkpoints.json")
    for ref in [checkpoints["input_checkpoint"],checkpoints["manifest"],checkpoints["reasoning_checkpoint"],
        *[c["result"] for c in checkpoints["checks"]],
        *[c[k] for c in checkpoints["calls"] for k in ["request","response"] if c.get(k)]]:
        data = (production/(ref["sha256"]+".json")).read_bytes()
        assert sha(data) == ref["sha256"] and len(data) == ref["size_bytes"]
    for p in files:
        if p.suffix in {".json",".log",".xml",".stdout",".stderr"}:
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}",p.read_text("utf-8",errors="replace")),p
    return {"research_baseline":"afa9381e","plan_sha256":sha(plans[0]),"plans_byte_identical":True,
        "gitignore_unchanged":True,"previous_raw_evidence_verified":prior,"raw_files":raw_files,"tests":tests,"formats":formats,
        "original_admission_and_agent_recovery_verified":True,"actual_SDK_HTTP_calls":6,"actual_execution_business_HTTP":1,
        "terminal_replay_new_HTTP":0,"current_allowed_receipts_implemented":True,"new_tables_or_dependencies":False,
        "started_action_amendment_implemented":False,"new_target_regression_verified":False,
        "external_default_model_tested":False,"formal_environment_page_implemented":False,"full_P0_P5_complete":False}


def staged(result):
    repo = Path(subprocess.check_output(["git","rev-parse","--show-toplevel"],cwd=ROOT,text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()+"/"
    raw = {r["path"]:r for r in result["raw_files"]}
    expected = {prefix+n for n in RELATED|set(raw)}
    actual = {n for n in subprocess.check_output(["git","diff","--cached","--name-only","-z"],cwd=repo).decode().split("\0") if n}
    assert actual == expected,{"unexpected":sorted(actual-expected),"missing":sorted(expected-actual)}
    for name,row in raw.items():
        data = subprocess.check_output(["git","show",":"+prefix+name],cwd=repo)
        assert sha(data) == row["sha256"] and len(data) == row["size_bytes"],name
    for name in RELATED:
        data = subprocess.check_output(["git","show",":"+prefix+name],cwd=repo)
        assert data.replace(b"\r\n",b"\n") == (ROOT/name).read_bytes().replace(b"\r\n",b"\n"),name
    return {"scope_verified":True,"files":len(actual),"raw_blobs_verified":len(raw)}


if __name__ == "__main__":
    result = verify()
    if "--record" in sys.argv:
        if REPORT.exists(): raise RuntimeError("Preserve original report")
        REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    else: assert read(REPORT) == result
    summary = {k:result[k] for k in ["plan_sha256","tests","full_P0_P5_complete"]}
    summary.update(raw_evidence_files=len(result["raw_files"]),prior_evidence_files=sum(result["previous_raw_evidence_verified"].values()))
    if "--staged" in sys.argv: summary["index"] = staged(result)
    print(json.dumps(summary,ensure_ascii=False))

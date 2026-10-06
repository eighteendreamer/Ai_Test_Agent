"""Recheck fixed planning/real target evidence without rewriting historical records."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from requirement_execution_delivery_20261006 import ROOT, DOCS, PLANS, IGNORE_SHA
from environment_decisions_delivery_20261006 import check_review
from src.application.documents.requirement_execution_checks import fixed_result, body_checks
from src.application.test_runs.plan_mapping import build_plan_run
from src.runtime.postgres_business_planning import fixed_planning_environment
from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.case_planning import CasePlanRecord, CasePlanRunRequest
from src.schemas.run_management import TestRunRecord
from src.modes.api_testing_mode.http_observation import HttpTarget, target_in_scope

PREFIX = "p4-environment-planning-"
REPORT = DOCS / (PREFIX + "validation.json")
RELATED = {
    "Agent_Server/src/application/prompting/prompt_assembly_service.py",
    "Agent_Server/src/application/sessions/session_service.py",
    "Agent_Server/src/application/test_cases/planning_service.py",
    "Agent_Server/src/application/test_cases/planning_workflow.py",
    "Agent_Server/src/application/test_runs/agent_case_assessment.py",
    "Agent_Server/src/application/test_runs/agent_case_context.py",
    "Agent_Server/src/application/test_runs/agent_case_execution.py",
    "Agent_Server/src/application/test_runs/agent_regression_service.py",
    "Agent_Server/src/application/test_runs/plan_mapping.py",
    "Agent_Server/src/application/test_runs/run_store.py",
    "Agent_Server/src/containers.py", "Agent_Server/src/main.py",
    "Agent_Server/src/modes/api_testing_mode/http_observation.py",
    "Agent_Server/src/runtime/postgres_planning_task_store.py",
    "Agent_Server/src/runtime/postgres_business_planning.py",
    "Agent_Server/src/schemas/business_planning.py",
    "Agent_Server/src/schemas/business_tool_observation.py",
    "Agent_Server/src/schemas/case_execution.py", "Agent_Server/src/schemas/case_planning.py",
    "Agent_Server/src/schemas/http_observation.py",
    "Agent_Server/tests/test_agent_case_session.py",
    "Agent_Server/tests/test_document_impact_production.py",
    "Agent_Server/tests/test_business_planning.py",
    "Agent_Server/tests/business_planning_production_scenario.py",
    "Agent_Server/tests/test_business_planning_production.py",
    "agent_web/src/services/businessPlanning.types.ts",
    "agent_web/src/services/projects.planning-tasks.api.test.ts", "agent_web/src/types.ts",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_PLANNING_DESIGN.md",
    "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/environment_planning_delivery_20261006.py",
    "docs/upgrade_2026-10-03/p4-environment-planning-validation.json",
}


def sha(data): return hashlib.sha256(data).hexdigest()
def read(path): return json.loads(path.read_bytes())


def plan_basis(raw):
    plan = CasePlanRecord.model_validate(raw)
    fixed_planning_environment(plan.project_id, plan.request.environment, plan.environment)
    return plan


def original_run(raw):
    run = TestRunRecord.model_validate(raw)
    _, rebuilt, _ = build_plan_run(run.plan_snapshot.plan,
        CasePlanRunRequest(creation_key=run.creation_key, mode_key=run.mode_key, session_id=run.session_id),
        now=run.created_at, created_by=run.created_by, regression=run.plan_snapshot.regression)
    assert rebuilt.id == run.id and rebuilt.creation_hash == run.creation_hash
    return run


def verify():
    plans = [path.read_bytes() for path in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT / ".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)", plans[0].decode("utf-8")):
        target = Path(re.sub(r":\d+$", "", link))
        if target == REPORT and "--record" in sys.argv: continue
        assert target.is_file(), link
    last = read(DOCS / "p4-environment-decisions-validation.json")
    prior = {}
    for name in [*last["previous_raw_evidence_verified"], "p4-environment-decisions-validation.json"]:
        items = read(DOCS/name)["raw_files"]
        for item in items:
            data = (ROOT/item["path"]).read_bytes()
            assert len(data) == item["size_bytes"] and sha(data) == item["sha256"], item["path"]
        prior[name] = len(items)
    files = sorted(path for path in DOCS.rglob("*") if path.is_file()
        and path.relative_to(DOCS).parts[0].startswith(PREFIX) and path != REPORT)
    raw_files = [{"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size,
        "sha256": sha(path.read_bytes())} for path in files]
    expected = {
        "offline-first": (1, 0, 1, 0), "offline-fixed": (61, 0, 0, 25),
        "live-first": (6, 0, 0, 0), "live-final": (10, 3, 0, 0),
        "live-verified": (10, 0, 0, 0), "live-separated": (10, 0, 0, 0),
        "compatibility-first": (5, 0, 0, 0), "regression": (0, 0, 0, 0),
        "regression-fixed": (83, 0, 0, 5), "client-final": (6, 0, 0, 0),
        "production-first": (1, 1, 0, 0), "production-fixed": (1, 1, 0, 0),
        "production-final": (1, 0, 0, 0), "agent-regression": (87, 12, 0, 3),
        "agent-live": (28, 4, 0, 14), "origin-live": (24, 0, 0, 0),
        "agent-recovery-fixed": (87, 0, 0, 2),
        "model-context-final": (20, 0, 0, 1),
    }
    tests = {}
    for path in sorted(DOCS.glob(PREFIX+"*.xml")):
        name = path.stem[len(PREFIX):]
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        result = {key: sum(int(s.get(key,"0")) for s in suites)
            for key in ["tests", "failures", "errors", "skipped"]}
        assert name in expected and tuple(result.values()) == expected[name], (name,result)
        result["time_seconds"] = round(sum(float(s.get("time","0")) for s in suites),3)
        result["skipped_reasons"] = [t.find("skipped").get("message", "")
            for s in suites for t in s.findall("testcase") if t.find("skipped") is not None]
        tests[name] = result
    assert set(expected).issubset(tests) and "model-context-final" in tests
    formats = {}
    folder = DOCS/(PREFIX+"model-context-final")
    for ext in ["md", "docx", "pdf"]:
        directory = folder/("applicability-planning-"+ext)
        state = read(directory/"verification.json")
        plan = plan_basis(state["fixed_plan"])
        assert plan.environment.snapshot.model_dump(mode="json") == state["snapshots"][1]
        assert state["model_context"]["business_environment"] == state["fixed_plan"]["environment"]
        for review in plan.environment.reviews: check_review(review.model_dump(mode="json"),state["jobs"])
        run = original_run(state["fixed_run"])
        assert run.plan_snapshot.plan == plan and run.creation_hash == state["recovered_creation_hash"]
        context = AgentCaseExecutionContext.model_validate(state["agent_context"])
        assert context.execution_json() == state["agent_context"] and context.business_environment == plan.environment
        assert context.runtime_inputs == state["request"]["runtime_context"]
        assert context.runtime_inputs["business_environment"] == "客户端自报不能覆盖"
        physical = state["agent_physical_model_requests"]
        assert len(physical) == 1
        system = "\n".join(m["content"] for m in physical[0]["messages"] if m["role"] == "system")
        assert "server-bound business_environment fixes the selected target" in system
        assert json.dumps(context.execution_json(),ensure_ascii=False,allow_nan=False) in system
        assert len(context.steps[0].content_json["steps"]) == 12
        result = fixed_result(state["actual_case_result"])
        checks = list(body_checks(result,"target",plan.project_id).values())
        assert result.status == "passed" and len(checks) == 5
        assert [c["actual"] for c in checks] == [200,9,0,False,None]
        assert checks[2]["actual"] == 0 and checks[3]["actual"] is False and checks[4]["actual"] is None
        job = state["actual_case_tool"]
        assert job["metadata"]["project_id"] == plan.project_id
        assert job["id"] in {ref.evidence_id for ref in result.evidence_refs if ref.evidence_type == "tool_job"}
        target = plan.environment.snapshot.configuration.payload.base_url
        assert job["input_payload"]["task"]["path"] == target+"/rule"
        observation = job["output_payload"]["task_result"]["execution_checkpoint"]["http_observation_v1"]
        assert observation["acquisition"] == "network" and len(observation["hops"]) == 1
        assert target_in_scope(HttpTarget.model_validate(observation["hops"][0]["target"]),target)
        source = state["original_case"]["version"]["source_refs"][0]["metadata"]["parse_snapshot"]["seed"]["original_asset"]
        raw = (directory/source["filename"]).read_bytes()
        assert sha(raw) == source["sha256"] and len(raw) == source["size_bytes"]
        refreshed = plan_basis(state["refreshed_plan"])
        assert refreshed.environment.reviews[0].request.action == "revoke"
        assert refreshed.environment.sha256 != plan.environment.sha256
        assert [len(calls) for calls in state["actual_HTTP"]] == [2,3]
        assert state["claim_restrictions_implemented"] is state["autonomous_model"] is False
        formats[ext] = {"original_sha256":source["sha256"], "case_steps":12,
            "target_from_fixed_context":True, "business_inputs_preserved":True, "actual_HTTP":5,
            "result_payload_sha256":result.payload_hash, "tool_output_sha256":sha(json.dumps(
                job["output_payload"],ensure_ascii=False,sort_keys=True,separators=(",",":")).encode())}
    durable = read(folder/"applicability-planning-durable"/"verification.json")
    basis = durable["seed"]["plan"]["environment"]
    assert basis == durable["final_plan"]["environment"] == durable["model_context"]["business_environment"]
    assert durable["task"]["status"] == "completed" and not durable["current_config_used_on_replay"]
    boundaries = read(folder/"applicability-planning-boundaries"/"verification.json")
    assert boundaries["wrong_bases_saved_run_count"] == 0
    production = DOCS/(PREFIX+"production-final")/"md-native-finish"
    state = read(production/"environment-planning.json")
    restored = read(production/"environment-planning-restart.json")
    plan = plan_basis(state["plan"])
    assert state["seed"]["plan"]["environment"] == state["plan"]["environment"]
    original_run(state["sql_run"])
    assert state["sql_run"] == state["detail"]["run"]
    assert restored["creation_hash"] == state["sql_run"]["creation_hash"] and restored["recovery_new_model_HTTP"] == 0
    assert restored["API_restarted"] and restored["current_config_removed"]
    messages = read(production/"physical-requests.json")
    assert len(messages) == 3 and state["physical_planning_calls"] == 1
    model_context = json.loads(messages[-1]["messages"][-1]["content"])
    assert model_context["business_environment"] == state["plan"]["environment"]
    assert len(model_context["evidence"][0]["payload"]["content"]["steps"]) == 12
    checkpoints = read(production/"checkpoints.json")
    for ref in [checkpoints["input_checkpoint"],checkpoints["manifest"],checkpoints["reasoning_checkpoint"],
        *[check["result"] for check in checkpoints["checks"]],
        *[call[key] for call in checkpoints["calls"] for key in ["request","response"] if call.get(key)]]:
        raw = (production/(ref["sha256"]+".json")).read_bytes()
        assert sha(raw) == ref["sha256"] and len(raw) == ref["size_bytes"]
    assert "built in 11.65s" in (DOCS/(PREFIX+"build-final.log")).read_text("utf-8")
    for path in files:
        if path.suffix in {".json",".log",".xml",".stdout",".stderr"}:
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}",path.read_text("utf-8",errors="replace")),path
    return {"research_baseline":"67602761","plan_sha256":sha(plans[0]),"plans_byte_identical":True,
        "gitignore_unchanged":True,"previous_raw_evidence_verified":prior,"raw_files":raw_files,
        "tests":tests,"formats":formats,"production_planning_API_CLI_and_run_creation_verified":True,
        "production_physical_model_requests":3,"production_planning_calls":1,
        "actual_tool_receipt_source":"controlled_fixture","production_run_executed":False,
        "execution_restrictions_implemented":False,"formal_environment_page_implemented":False,
        "external_default_model_tested":False,"full_P0_P5_complete":False}


def verify_staged(result):
    repo = Path(subprocess.check_output(["git","rev-parse","--show-toplevel"],cwd=ROOT,text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()+"/"
    raw = {item["path"]:item for item in result["raw_files"]}
    expected = {prefix+name for name in RELATED|set(raw)}
    staged = {name for name in subprocess.check_output(["git","diff","--cached","--name-only","-z"],cwd=repo).decode().split("\0") if name}
    assert staged == expected,{"unexpected":sorted(staged-expected),"missing":sorted(expected-staged)}
    for name,item in raw.items():
        blob = subprocess.check_output(["git","show",":"+prefix+name],cwd=repo)
        assert len(blob) == item["size_bytes"] and sha(blob) == item["sha256"],name
    for name in RELATED:
        blob = subprocess.check_output(["git","show",":"+prefix+name],cwd=repo)
        assert blob.replace(b"\r\n",b"\n") == (ROOT/name).read_bytes().replace(b"\r\n",b"\n"),name
    return {"scope_verified":True,"files":len(staged),"raw_blobs_verified":len(raw)}


if __name__ == "__main__":
    result = verify()
    if "--record" in sys.argv:
        if REPORT.exists(): raise RuntimeError("Preserve the original report")
        REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    else: assert read(REPORT) == result
    summary = {key:result[key] for key in ["plan_sha256","tests","full_P0_P5_complete"]}
    summary.update(raw_evidence_files=len(result["raw_files"]),prior_evidence_files=sum(result["previous_raw_evidence_verified"].values()))
    if "--staged" in sys.argv: summary["index"] = verify_staged(result)
    print(json.dumps(summary,ensure_ascii=False))

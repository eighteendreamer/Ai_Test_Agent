"""Verify the formal App against actual fixed checks, histories and physical sends."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from requirement_execution_delivery_20261006 import ROOT, DOCS, PLANS, IGNORE_SHA, check_execution

OLD_PLAN_SHA = "a52af093c9b882f255038e66772ef9986cb8bae9df8f11f34910c8a91c3336ad"
PREFIX = "p4-execution-product-"
REPORT = DOCS/"p4-execution-product-validation.json"


def sha(data): return hashlib.sha256(data).hexdigest()


def verify():
    plans = [path.read_bytes() for path in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT/".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)", plans[0].decode("utf-8")):
        target = Path(re.sub(r":\d+$", "", link))
        if target != REPORT: assert target.is_file(), link
    previous = json.loads((DOCS/"p4-requirement-execution-validation.json").read_text("utf-8"))
    prior_counts = {}
    for name in [*previous["previous_raw_evidence_verified"], "p4-requirement-execution-validation.json"]:
        report = json.loads((DOCS/name).read_text("utf-8"))
        for item in report["raw_files"]:
            data = (ROOT/item["path"]).read_bytes()
            assert len(data) == item["size_bytes"] and sha(data) == item["sha256"], item["path"]
        prior_counts[name] = len(report["raw_files"])
    files = sorted(path for path in DOCS.rglob("*") if path.is_file() and path.relative_to(DOCS).parts[0].startswith(PREFIX)
        and path.name != "p4-execution-product-validation.json")
    raw = [{"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha(path.read_bytes())} for path in files]
    tests = {}
    for path in files:
        if path.suffix == ".xml":
            root = ET.parse(path).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            tests[path.name] = {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ["tests", "failures", "errors", "skipped"]}
            tests[path.name]["time"] = round(sum(float(suite.get("time", "0")) for suite in suites), 3)
    for name, count in [("components-delivery", 120), ("App-verified", 1), ("production-verified", 1), ("coverage-compatibility", 1)]:
        result = tests[PREFIX+name+".xml"]
        assert result["tests"] == count and result["failures"] == result["errors"] == result["skipped"] == 0
    assert tests[PREFIX+"production-compatibility.xml"]["failures"] == 1
    folder = DOCS/(PREFIX+"App-verified")
    state = json.loads((folder/"requirement-execution-product.json").read_text("utf-8"))
    restart = json.loads((folder/"requirement-execution-product-restart.json").read_text("utf-8"))
    counts = check_execution(state)
    assert counts == (5, 2, 6)
    old_sample = json.loads((DOCS/"p4-requirement-execution-production-identity/md-native-finish/requirement-execution.json").read_text("utf-8"))
    assert check_execution(old_sample) == (2, 2, 6)
    production_sample = json.loads((DOCS/(PREFIX+"production-verified")/"md-native-finish/requirement-execution.json").read_text("utf-8"))
    assert check_execution(production_sample) == (2, 2, 6)
    assert restart["records"] == state["execution_sql"]
    assert state["association_model_http_calls"] == restart["recovery_model_http_calls"] == 0
    assert len(state["browser_submissions"]) == 5 and len(state["lost_response_submissions"]) == 1
    assert state["browser_submissions"][0] == state["first"]["request"]
    assert state["browser_submissions"][3]["expected_execution_id"] == state["third"]["id"]
    assert state["final"]["previous_execution_id"] == state["concurrent"]["id"]
    assert [request["status"] for request in state["business_http"]] == [201, 200, 200, 201, 200, 500]
    assert all(result["status"] == "failed" for result in state["results"])
    assert all(check["check"]["status"] == "passed" and check["result_status"] == "failed"
        and check["regression_source_result_id"] == state["results"][0]["id"] for check in state["final"]["checks"])
    assert len(state["final"]["checks"]) == 4
    assert state["matrix"]["points"][0]["execution_scope"] == "selected_checks" and state["matrix"]["deployment_status"] == "unknown"
    for detail, result in zip(state["run_details"], state["results"]):
        assert detail["run"]["project_id"] == state["first"]["project_id"]
        assert result in detail["results"] and detail["items"][0]["result_id"] == result["id"]
    requests = json.loads((folder/"physical-requests.json").read_text("utf-8"))
    calls = json.loads((folder/"calls.json").read_text("utf-8"))
    objects = {path.stem: path.read_bytes() for path in folder.glob("*.object")}
    for digest, data in objects.items(): assert sha(data) == digest
    for call in calls:
        raw_request = objects[call["request"]["sha256"]]
        assert len(raw_request) == call["request"]["size_bytes"]
        request = json.loads(raw_request)["request"]
        messages = [{"role": "system", "content": request["system_prompt"]}, *request["messages"]]
        assert sum(body["messages"] == messages for body in requests) == 1
    assert len(requests) == len(calls) == 2
    assert json.loads((folder/"page-errors.json").read_text("utf-8")) == []
    layout = json.loads((folder/"layout.json").read_text("utf-8"))
    assert layout["bodyWidth"] <= layout["viewport"] == 1440
    for filename in ["execution-final.png", "execution-archived.png", "execution-lost-reply.png", "execution-final-dom.txt", "execution-archived-dom.txt"]:
        assert (folder/filename).stat().st_size > 0
    assert "完整操作11" in (folder/"execution-final-dom.txt").read_text("utf-8")
    suspects = []
    for path in files:
        if path.suffix in {".json", ".txt", ".log", ".stdout", ".stderr", ".xml"}:
            if re.search(r"\bsk-[A-Za-z0-9_-]{16,}", path.read_text("utf-8", errors="replace")): suspects.append(path.relative_to(ROOT).as_posix())
    assert not suspects, suspects
    return {"research_baseline": "8efcdd99", "plans_byte_identical": True, "plan_sha256": sha(plans[0]), "gitignore_unchanged": True,
        "previous_raw_evidence_verified": prior_counts, "tests": tests, "raw_files": raw,
        "formal_editor_verified": True, "full_App_verified": True, "production_objects_verified": len(objects),
        "execution_reviews": 5, "actual_business_requests": 6, "association_model_http_calls": 0,
        "impact_physical_model_calls": 2, "cleanup_failure_separate_from_body_checks": True,
        "external_default_model_tested": False, "autonomous_agent_model_execution": False,
        "production_history_migration_run": False, "deployment_status": "unknown", "full_P0_P5_complete": False}


def verify_staged(result):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()+"/"
    related = {
        "agent_web/src/features/tools/plugins/RequirementExecutionPanel.vue",
        "agent_web/src/features/tools/plugins/useRequirementExecution.ts",
        "agent_web/src/features/tools/plugins/RequirementExecutionPanel.test.ts",
        "agent_web/src/features/tools/plugins/requirementExecutionProduct.ts",
        "agent_web/src/features/tools/plugins/RequirementMatrix.vue", "agent_web/src/services/requirements.types.ts",
        "Agent_Server/tests/requirement_execution_production_scenario.py",
        "Agent_Server/tests/requirement_execution_product_scenario.py",
        "Agent_Server/tests/test_requirement_execution_full_product.py",
        "Agent_Server/tests/test_document_impact_full_product.py",
        "docs/upgrade_2026-10-03/P4_REQUIREMENT_EXECUTION_PRODUCT_DESIGN.md",
        "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/.gitattributes",
        "docs/upgrade_2026-10-03/requirement_execution_delivery_20261006.py",
        "docs/upgrade_2026-10-03/execution_product_delivery_20261006.py",
        "docs/upgrade_2026-10-03/p4-execution-product-validation.json",
    }
    raw = {item["path"]: item for item in result["raw_files"]}
    expected = {prefix+name for name in related|set(raw)}
    staged = {name for name in subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=repo).decode().split("\0") if name}
    assert staged == expected, {"unexpected": sorted(staged-expected), "missing": sorted(expected-staged)}
    for name, item in raw.items():
        blob = subprocess.check_output(["git", "show", ":"+prefix+name], cwd=repo)
        assert len(blob) == item["size_bytes"] and sha(blob) == item["sha256"], name
    for name in related-{REPORT.relative_to(ROOT).as_posix()}:
        blob = subprocess.check_output(["git", "show", ":"+prefix+name], cwd=repo)
        assert blob.replace(b"\r\n", b"\n") == (ROOT/name).read_bytes().replace(b"\r\n", b"\n"), name
    return {"scope_verified": True, "files": len(staged), "raw_evidence_blobs_verified": len(raw)}


if __name__ == "__main__":
    if "--sync" in sys.argv:
        content = PLANS[0].read_bytes()
        for path in PLANS[1:]: assert sha(path.read_bytes()) in {OLD_PLAN_SHA, sha(content)}, "副本改变，不能覆盖"
        for path in PLANS[1:]: path.write_bytes(content)
    result = verify()
    if "--staged" in sys.argv: result["staged"] = verify_staged(result)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    assert REPORT.is_file()
    print(json.dumps({"new_raw_files": len(result["raw_files"]), "prior_raw_files": sum(result["previous_raw_evidence_verified"].values()),
        "plans_byte_identical": True, "tests": result["tests"]}, ensure_ascii=False))

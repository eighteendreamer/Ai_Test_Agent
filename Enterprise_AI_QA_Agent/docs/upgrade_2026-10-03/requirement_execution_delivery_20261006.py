"""Check fixed execution evidence, raw history and the synchronized upgrade plan."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Agent_Server"))
from src.application.documents.impact_sources import impact_record_hash
from src.application.documents.requirement_execution_checks import fixed_result
from src.application.test_cases.version_content import version_content_hash
from src.runtime.postgres_requirement_store import fixed_requirement_version, requirement_hash
from src.runtime.postgres_requirement_coverage_store import fixed_coverage
from src.runtime.postgres_requirement_execution_store import fixed_execution
from src.schemas.case_management import TestCaseBundle

PREFIX = "p4-requirement-execution-"
PLANS = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
OLD_PLAN_SHA = "3b16d44c63b3e9e60c80369fde38a00344bb1fdb49bb7ca138d94fb4ffa84779"
IGNORE_SHA = "d604cb2eb1e5da2b92ec113f2e14d05889963ae0fe9492760670f3a1c5c8b241"


def sha(data): return hashlib.sha256(data).hexdigest()


def check_execution(value):
    case = TestCaseBundle.model_validate(value["selected_case"])
    assert version_content_hash(case.version) == case.version.content_hash
    results = {result.id: result for result in map(fixed_result, value["results"])}
    jobs = {job["id"]: job for job in value["tool_jobs"]}
    reviews = value["execution_sql"] if "execution_sql" in value else [value["first"], value["second"]] if "first" in value else []
    for raw in reviews:
        review = fixed_execution(raw)
        for check in review.checks:
            result = results[check.result_id]
            assert impact_record_hash(result) == check.result_record_sha256
            assert result.payload_hash == check.result_payload_hash
            assert check.version_record_sha256 == impact_record_hash(case.version)
            assert check.case_version_id == case.version.id
            verification = next(v for v in result.actual["verification_results"] if v["id"] == check.verification_id)
            assert verification["metadata"]["phase"] == "case"
            assert verification["metadata"]["checks"][check.check_index] == check.check
            for source in check.tool_sources:
                job = jobs[source["job_id"]]
                assert job["call_id"] == source["call_id"] and job["status"] == source["status"]
                assert requirement_hash(job["output_payload"]) == source["output_sha256"]
    if "version" in value: fixed_requirement_version(value["version"])
    fixed_coverage(value["coverage"])
    return len(reviews), len(results), len(jobs)


def verify():
    plans = [p.read_bytes() for p in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT / ".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)", plans[0].decode("utf-8")):
        assert Path(re.sub(r":\d+$", "", link)).is_file(), link
    old = json.loads((DOCS/"p4-generation-pool-validation.json").read_text("utf-8"))
    old_counts = {}
    for name in [*old["previous_raw_evidence_verified"], "p4-generation-pool-validation.json"]:
        report = json.loads((DOCS/name).read_text("utf-8"))
        for item in report["raw_files"]:
            data = (ROOT/item["path"]).read_bytes()
            assert len(data) == item["size_bytes"] and sha(data) == item["sha256"], item["path"]
        old_counts[name] = len(report["raw_files"])
    files = sorted(p for p in DOCS.rglob("*") if p.is_file() and p.relative_to(DOCS).parts[0].startswith(PREFIX)
        and p.name != "p4-requirement-execution-validation.json")
    raw = [{"path": p.relative_to(ROOT).as_posix(), "size_bytes": p.stat().st_size, "sha256": sha(p.read_bytes())} for p in files]
    tests = {}
    for path in files:
        if path.suffix == ".xml":
            tree = ET.parse(path).getroot()
            suites = [tree] if tree.tag == "testsuite" else list(tree.findall("testsuite"))
            tests[path.name] = {key: sum(int(suite.get(key, "0")) for suite in suites)
                for key in ["tests", "failures", "errors", "skipped"]}
            tests[path.name]["time"] = round(sum(float(suite.get("time", "0")) for suite in suites), 3)
    checks = {}
    for path in files:
        if path.name in {"verification.json", "requirement-execution.json"}:
            value = json.loads(path.read_text("utf-8"))
            if "selected_case" in value:
                checks[path.relative_to(DOCS).as_posix()] = check_execution(value)
    production = DOCS/"p4-requirement-execution-production-identity/md-native-finish"
    original = json.loads((production/"requirement-execution.json").read_text("utf-8"))
    restart = json.loads((production/"requirement-execution-restart.json").read_text("utf-8"))
    assert original["execution_model_http_calls"] == restart["recovery_model_http_calls"] == 0
    assert restart["records"] == [original["first"], original["second"]]
    assert len(original["business_http"]) == 6
    assert original["second"]["checks"][0]["regression_source_result_id"] == original["results"][0]["id"]
    objects = 0
    for path in production.iterdir():
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            assert sha(path.read_bytes()) == path.stem
            objects += 1
    for filename in ["p4-requirement-execution-final.xml", "p4-requirement-execution-production-final.xml",
        "p4-requirement-execution-identity-verified.xml", "p4-requirement-execution-production-identity.xml",
        "p4-requirement-execution-components-first.xml"]:
        assert tests[filename]["failures"] == tests[filename]["errors"] == 0
    assert tests["p4-requirement-execution-identity-verified.xml"]["tests"] == 11
    assert tests["p4-requirement-execution-identity-verified.xml"]["skipped"] == 0
    assert tests["p4-requirement-execution-production-identity.xml"]["tests"] == 1
    assert tests["p4-requirement-execution-production-identity.xml"]["skipped"] == 0
    return {"research_baseline": "c27efe0e", "plans_byte_identical": True, "plan_sha256": sha(plans[0]),
        "gitignore_unchanged": True, "previous_raw_evidence_verified": old_counts,
        "tests": tests, "fixed_execution_checks": checks, "production_objects_verified": objects,
        "raw_files": raw, "actual_business_requests": 6, "association_model_http_calls": 0,
        "formal_editor_verified": False, "full_App_verified": False, "external_default_model_tested": False,
        "production_history_migration_run": False, "full_P0_P5_complete": False}


def verify_staged(result):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()+"/"
    related = {
        "Agent_Server/src/schemas/requirement_execution.py",
        "Agent_Server/src/application/documents/requirement_execution_checks.py",
        "Agent_Server/src/runtime/postgres_requirement_execution_store.py",
        "Agent_Server/src/schemas/requirements.py", "Agent_Server/src/runtime/postgres_requirement_store.py",
        "Agent_Server/src/application/documents/requirement_service.py", "Agent_Server/src/api/routes/requirements.py",
        "Agent_Server/tests/test_requirement_execution.py", "Agent_Server/tests/requirement_execution_production_scenario.py",
        "Agent_Server/tests/test_requirement_execution_production.py", "Agent_Server/tests/test_document_impact_production.py",
        "agent_web/src/features/tools/plugins/RequirementMatrix.vue",
        "agent_web/src/features/tools/plugins/requirementExecutionProduct.ts",
        "agent_web/src/features/tools/plugins/RequirementExecutionProduct.test.ts",
        "agent_web/src/services/requirements.api.ts", "agent_web/src/services/requirements.types.ts",
        "agent_web/src/services/requirements.api.test.ts", "docs/upgrade_2026-10-03/PLAN.md",
        "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/P4_REQUIREMENT_EXECUTION_DESIGN.md",
        "docs/upgrade_2026-10-03/.gitattributes", "docs/upgrade_2026-10-03/requirement_execution_delivery_20261006.py",
        "docs/upgrade_2026-10-03/p4-requirement-execution-validation.json",
    }
    raw = {item["path"]: item for item in result["raw_files"]}
    expected = {prefix+name for name in related|set(raw)}
    staged = {name for name in subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=repo).decode().split("\0") if name}
    assert staged == expected, {"unexpected": sorted(staged-expected), "missing": sorted(expected-staged)}
    for name, item in raw.items():
        blob = subprocess.check_output(["git", "show", ":"+prefix+name], cwd=repo)
        assert len(blob) == item["size_bytes"] and sha(blob) == item["sha256"], name
    for name in related-{"docs/upgrade_2026-10-03/p4-requirement-execution-validation.json"}:
        blob = subprocess.check_output(["git", "show", ":"+prefix+name], cwd=repo)
        assert blob.replace(b"\r\n", b"\n") == (ROOT/name).read_bytes().replace(b"\r\n", b"\n"), name
    return {"scope_verified": True, "files": len(staged), "raw_evidence_blobs_verified": len(raw)}


if __name__ == "__main__":
    if "--sync" in sys.argv:
        content = PLANS[0].read_bytes()
        for path in PLANS[1:]: assert sha(path.read_bytes()) in {OLD_PLAN_SHA, sha(content)}, "副本改变，不能覆盖"
        for path in PLANS[1:]: path.write_bytes(content)
    result = verify()
    if "--staged" in sys.argv:
        result["staged"] = verify_staged(result)
    (DOCS/"p4-requirement-execution-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"raw_files": len(result["raw_files"]), "old_raw_files": sum(result["previous_raw_evidence_verified"].values()),
        "plans_byte_identical": True, "tests": result["tests"]}, ensure_ascii=False))

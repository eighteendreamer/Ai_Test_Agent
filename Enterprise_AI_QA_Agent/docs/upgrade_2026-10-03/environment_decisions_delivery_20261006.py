"""Audit actual scoped decisions, original observations and unchanged evidence."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from requirement_execution_delivery_20261006 import ROOT, DOCS, PLANS, IGNORE_SHA

sys.path.insert(0, str(ROOT / "Agent_Server"))
from src.runtime.postgres_business_applicability_store import fixed_applicability
from src.runtime.postgres_business_environment_store import environment_digest, fixed_environment_snapshot
from src.application.test_cases.version_content import version_content_hash
from http_origin_delivery_20261006 import check_observed

PREFIX = "p4-environment-decisions-"
REPORT = DOCS / (PREFIX + "validation.json")
RELATED = {
    "Agent_Server/src/api/routes/business_applicability.py",
    "Agent_Server/src/application/projects/business_applicability_service.py",
    "Agent_Server/src/runtime/postgres_business_applicability_store.py",
    "Agent_Server/src/runtime/requirement_coverage_basis.py",
    "Agent_Server/src/runtime/postgres_requirement_execution_store.py",
    "Agent_Server/src/schemas/business_applicability.py", "Agent_Server/src/containers.py", "Agent_Server/src/main.py",
    "Agent_Server/tests/test_business_applicability.py", "Agent_Server/tests/test_business_applicability_production.py",
    "Agent_Server/tests/business_applicability_production_scenario.py", "Agent_Server/tests/test_document_impact_production.py",
    "agent_web/src/services/businessApplicability.api.ts", "agent_web/src/services/businessApplicability.types.ts",
    "agent_web/src/services/businessApplicability.api.test.ts", "agent_web/src/services/api.ts",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_APPLICABILITY_DESIGN.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_DECISIONS_DESIGN.md", "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/environment_decisions_delivery_20261006.py",
    "docs/upgrade_2026-10-03/p4-environment-decisions-validation.json",
}


def sha(data): return hashlib.sha256(data).hexdigest()
def read(path): return json.loads(path.read_bytes())


def check_review(raw, jobs):
    saved = fixed_applicability(raw)
    assert saved.model_dump(mode="json") == raw
    for observation in saved.observations:
        job = next(job for job in jobs if job["id"] == observation.tool_job_id)
        assert job["metadata"]["project_id"] == saved.project_id
        assert environment_digest(job["output_payload"]) == observation.output_sha256
        actual = job["output_payload"]
        for token in observation.output_path_tokens: actual = actual[token]
        assert type(actual) is type(observation.value) and actual == observation.value
        assert observation.value_sha256 == environment_digest(actual)
    for case in saved.cases:
        assert version_content_hash(case.version) == case.version.content_hash
    return saved


def verify():
    plans = [path.read_bytes() for path in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT / ".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)", plans[0].decode("utf-8")):
        assert Path(re.sub(r":\d+$", "", link)).is_file(), link
    previous = read(DOCS / "p4-http-origin-validation.json")
    prior_counts = {}
    for name in [*previous["previous_raw_evidence_verified"], "p4-http-origin-validation.json"]:
        report = read(DOCS / name)
        for item in report["raw_files"]:
            data = (ROOT / item["path"]).read_bytes()
            assert len(data) == item["size_bytes"] and sha(data) == item["sha256"], item["path"]
        prior_counts[name] = len(report["raw_files"])
    files = sorted(path for path in DOCS.rglob("*") if path.is_file()
        and path.relative_to(DOCS).parts[0].startswith(PREFIX) and path != REPORT)
    raw_files = [{"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size,
        "sha256": sha(path.read_bytes())} for path in files]
    tests = {}
    expected = {"offline-first": (1, 0, 1, 0), "offline-fixed": (6, 0, 0, 5), "live-first": (6, 0, 0, 0),
        "new-bases-first": (1, 1, 0, 0), "new-bases-fixed": (1, 1, 0, 0), "new-bases-final": (1, 0, 0, 0),
        "live-final": (7, 0, 0, 0), "live-verified": (7, 0, 0, 0), "production-first": (1, 0, 0, 0),
        "production-verified": (1, 0, 0, 0), "regression": (38, 0, 0, 0), "client-first": (9, 0, 0, 0)}
    for name, counts in expected.items():
        root = ET.parse(DOCS / (PREFIX + name + ".xml")).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        result = {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ["tests", "failures", "errors", "skipped"]}
        assert tuple(result.values()) == counts, (name, result)
        result["time_seconds"] = round(sum(float(suite.get("time", "0")) for suite in suites), 3)
        tests[name] = result
    folder = DOCS / (PREFIX + "live-verified")
    formats = {}
    for extension in ["md", "docx", "pdf"]:
        directory = folder / ("applicability-" + extension)
        state = read(directory / "verification.json")
        saved = [check_review(row["record"], state["jobs"]) for row in state["records"]]
        assert len(saved) == 4
        first, second = check_review(state["first"], state["jobs"]), check_review(state["second"], state["jobs"])
        assert first.request.points[0].status == "not_effective" and first.cases[0].status == "valid"
        assert second.request.points[0].status == "effective" and second.cases[0].status == "invalid"
        assert [point.point_key for point in second.request.points] == ["retention"]
        assert all(case.point_keys == ["retention"] for case in second.cases)
        assert state["revoked"]["request"]["action"] == "revoke" and state["revoked"]["request"]["points"] == []
        assert state["reconfirmed"]["revision"] == 3 and state["reconfirmed"]["previous_review_id"] == state["revoked"]["id"]
        assert [len(calls) for calls in state["actual_HTTP"]] == [2, 2]
        for observation, snapshot in zip(state["observations"][:2], state["snapshots"]):
            check_observed(observation, state["jobs"], fixed_environment_snapshot(snapshot))
        source = state["original_case"]["version"]["source_refs"][0]["metadata"]["parse_snapshot"]["seed"]["original_asset"]
        original = directory / source["filename"]
        assert sha(original.read_bytes()) == source["sha256"] and original.stat().st_size == source["size_bytes"]
        assert len(state["full_case"]["version"]["content_json"]["steps"]) == 12
        expected_values = state["full_case"]["version"]["content_json"]["expected"]
        assert expected_values["expired"] is False and expected_values["expired_count"] == 0 and expected_values["deleted_at"] is None
        assert state["matrix_unchanged"]["deployment_status"] == "unknown" and state["recovery_no_network_or_current_config"]
        formats[extension] = {"original_sha256": source["sha256"], "PG_decisions": len(saved), "actual_HTTP": 4,
            "reviewed_points": ["retention"], "unreviewed_points_not_inherited": ["boundary"]}
    for name in ["races", "boundaries", "new-bases"]:
        state = read(folder / ("applicability-"+name) / "verification.json")
        for row in state["records"]: check_review(row["record"], state["jobs"])
        if name == "races":
            assert len(state["records"]) == 3 and sorted(state["revision_statuses"]) == [201, 409]
            assert state["actual_committed_reply_lost"] and len({item["id"] for item in state["first_race"]}) == 1
        elif name == "boundaries":
            assert state["cross_unknown"]["observations"][0]["target_status"] == "mismatch" and state["sql_tamper_rejected"]
        else:
            assert state["new_coverage"]["coverage"] == state["coverage"]
            assert state["new_requirement"]["id"] != state["requirement"]["id"]
            assert state["old_source_unknown"]["observations"][0]["target_status"] == "unknown"
            assert state["replayed_unknown"]["observations"][0]["http_observation"]["acquisition"] == "completed_request"
            assert state["replay_new_HTTP"] == 0 and [len(calls) for calls in state["actual_HTTP"]] == [2, 2]
    production = DOCS / (PREFIX + "production-verified") / "md-native-finish"
    state, restarted = read(production / "environment-decisions.json"), read(production / "environment-decisions-restart.json")
    assert len(restarted["records"]) == 3 and restarted["API_restarted"] and restarted["current_target_configuration_removed"]
    for target in state["targets"]:
        check_review(target["review"], [target["job"]])
        assert len(target["HTTP"]) == 1
        assert len(target["review"]["cases"][0]["version"]["content_json"]["steps"]) == 12
    assert state["model_calls_during_decision"] == restarted["recovery_new_HTTP"] == 0
    checkpoints = read(production / "checkpoints.json")
    for ref in [checkpoints["input_checkpoint"], checkpoints["manifest"], checkpoints["reasoning_checkpoint"],
        *[check["result"] for check in checkpoints["checks"]],
        *[call[key] for call in checkpoints["calls"] for key in ["request", "response"] if call.get(key)]]:
        raw = (production / (ref["sha256"]+".json")).read_bytes()
        assert sha(raw) == ref["sha256"] and len(raw) == ref["size_bytes"]
    assert len(read(production / "physical-requests.json")) == 2
    assert "built in 8.17s" in (DOCS / (PREFIX+"build-first.log")).read_text("utf-8")
    for path in files:
        if path.suffix in {".json", ".log", ".xml", ".stdout", ".stderr"}:
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", path.read_text("utf-8", errors="replace")), path
    return {"research_baseline": "1ec34cef", "plans_byte_identical": True, "plan_sha256": sha(plans[0]),
        "gitignore_unchanged": True, "previous_raw_evidence_verified": prior_counts, "raw_files": raw_files,
        "tests": tests, "formats": formats, "production_API_restart_verified": True,
        "decision_source": "explicit_review_fixture", "automatic_deployment_inference": False,
        "decision_new_model_calls": 0, "recovery_new_HTTP": 0, "run_environment_binding_implemented": False,
        "execution_restrictions_implemented": False, "formal_environment_page_implemented": False,
        "external_default_model_tested": False, "full_P0_P5_complete": False}


def verify_staged(result):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()+"/"
    raw = {item["path"]: item for item in result["raw_files"]}
    expected = {prefix+name for name in RELATED | set(raw)}
    staged = {name for name in subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=repo).decode().split("\0") if name}
    assert staged == expected, {"unexpected": sorted(staged-expected), "missing": sorted(expected-staged)}
    for name, item in raw.items():
        blob = subprocess.check_output(["git", "show", ":"+prefix+name], cwd=repo)
        assert len(blob) == item["size_bytes"] and sha(blob) == item["sha256"], name
    for name in RELATED:
        blob = subprocess.check_output(["git", "show", ":"+prefix+name], cwd=repo)
        assert blob.replace(b"\r\n", b"\n") == (ROOT/name).read_bytes().replace(b"\r\n", b"\n"), name
    return {"scope_verified": True, "files": len(staged), "raw_evidence_blobs_verified": len(raw)}


if __name__ == "__main__":
    result = verify()
    if "--record" in sys.argv:
        if REPORT.exists(): raise RuntimeError("Preserve the previous report; use another filename")
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    else:
        assert read(REPORT) == result
    summary = {key: result[key] for key in ["plan_sha256", "tests", "production_API_restart_verified", "full_P0_P5_complete"]}
    summary.update(raw_evidence_files=len(result["raw_files"]), prior_evidence_files=sum(result["previous_raw_evidence_verified"].values()))
    if "--staged" in sys.argv: summary["index"] = verify_staged(result)
    print(json.dumps(summary, ensure_ascii=False))

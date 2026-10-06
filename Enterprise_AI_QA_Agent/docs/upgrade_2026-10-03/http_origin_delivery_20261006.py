"""Audit actual HTTP origins and saved reads without resending business requests."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from requirement_execution_delivery_20261006 import ROOT, DOCS, PLANS, IGNORE_SHA

sys.path.insert(0, str(ROOT / "Agent_Server"))
from src.runtime.postgres_business_environment_store import environment_digest, fixed_environment_snapshot
from src.modes.api_testing_mode.http_observation import HttpResponseObservation, target_in_scope

PREFIX = "p4-http-origin-"
REPORT = DOCS / (PREFIX + "validation.json")
RELATED = {
    "Agent_Server/src/modes/api_testing_mode/http_observation.py",
    "Agent_Server/src/modes/api_testing_mode/executor.py",
    "Agent_Server/src/modes/api_testing_mode/runtime.py",
    "Agent_Server/src/modes/api_testing_mode/report_builder.py",
    "Agent_Server/src/schemas/business_tool_observation.py",
    "Agent_Server/src/application/projects/business_tool_observation.py",
    "Agent_Server/src/application/projects/business_environment_service.py",
    "Agent_Server/src/api/routes/business_environments.py", "Agent_Server/src/containers.py",
    "Agent_Server/tests/test_api_http_origin.py", "Agent_Server/tests/test_api_http_origin_production.py",
    "Agent_Server/tests/test_business_environment.py",
    "agent_web/src/services/businessEnvironments.types.ts", "agent_web/src/services/businessEnvironments.api.ts",
    "agent_web/src/services/businessEnvironments.api.test.ts",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_APPLICABILITY_DESIGN.md",
    "docs/upgrade_2026-10-03/P4_HTTP_ORIGIN_EVIDENCE_DESIGN.md",
    "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/http_origin_delivery_20261006.py",
    "docs/upgrade_2026-10-03/p4-http-origin-validation.json",
}


def sha(data): return hashlib.sha256(data).hexdigest()
def read(path): return json.loads(path.read_bytes())


def check_observed(value, jobs, snapshot):
    assert value["environment_snapshot_sha256"] == snapshot.sha256
    job = next(job for job in jobs if str(job.get("id", job.get("tool_job_id"))) == value["tool_job_id"])
    assert job["metadata"]["project_id"] == value["project_id"] == snapshot.project_id
    assert value["output_sha256"] == environment_digest(job["output_payload"])
    assert job["metadata"]["tool_output_observation"]["output_hash"] == value["output_sha256"]
    actual = job["output_payload"]
    for token in value["output_path_tokens"]: actual = actual[token]
    assert type(actual) is type(value["value"]) and actual == value["value"]
    assert value["value_sha256"] == environment_digest(actual)
    origin = HttpResponseObservation.model_validate(value["http_observation"])
    assert origin.acquisition == "network" and origin.observed_at >= snapshot.captured_at
    matches = all(target_in_scope(hop.target, snapshot.configuration.payload.base_url) for hop in origin.hops)
    assert value["target_status"] == ("matched" if matches else "mismatch")
    assert value["deployment_status"] == "unknown"


def verify():
    plans = [p.read_bytes() for p in PLANS]
    assert plans[0] == plans[1] == plans[2] and sha((ROOT / ".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)", plans[0].decode("utf-8")):
        assert Path(re.sub(r":\d+$", "", link)).is_file(), link
    previous = read(DOCS / "p4-environment-foundation-validation.json")
    prior_counts = {}
    for name in [*previous["previous_raw_evidence_verified"], "p4-environment-foundation-validation.json"]:
        report = read(DOCS / name)
        for item in report["raw_files"]:
            data = (ROOT / item["path"]).read_bytes()
            assert len(data) == item["size_bytes"] and sha(data) == item["sha256"], item["path"]
        prior_counts[name] = len(report["raw_files"])
    files = sorted(p for p in DOCS.rglob("*") if p.is_file()
                   and p.relative_to(DOCS).parts[0].startswith(PREFIX) and p != REPORT)
    raw_files = [{"path": p.relative_to(ROOT).as_posix(), "size_bytes": p.stat().st_size, "sha256": sha(p.read_bytes())} for p in files]
    tests = {}
    for name, counts in {"reproduction": (2, 2), "fixed": (18, 0), "live-first": (24, 0),
        "live-final": (24, 0), "production-first": (1, 0), "regression": (46, 0), "client-first": (7, 0)}.items():
        root = ET.parse(DOCS / (PREFIX + name + ".xml")).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        result = {k: sum(int(s.get(k, "0")) for s in suites) for k in ["tests", "failures", "errors", "skipped"]}
        assert (result["tests"], result["failures"]) == counts and result["errors"] == result["skipped"] == 0
        result["time_seconds"] = round(sum(float(s.get("time", "0")) for s in suites), 3)
        tests[name] = result
    folder = DOCS / (PREFIX + "live-final")
    formats = {}
    for ext in ["md", "docx", "pdf"]:
        state = read(folder / ("live-" + ext + ".json"))
        snapshots = [fixed_environment_snapshot(raw) for raw in state["snapshots"]]
        assert state["parsed_task"]["status"] == "completed"
        original = (folder / (ext + "-requirements-v1." + ext)).read_bytes()
        assert sha(original) == state["source_sha256"] == state["document"]["original_asset"]["sha256"]
        assert len(original) == state["document"]["original_asset"]["size_bytes"]
        assert (state["parsed_task"]["project_id"], state["parsed_task"]["document_id"], state["parsed_task"]["version_id"]) == (
            state["document"]["project_id"], state["document"]["id"], state["document"]["current_version_id"])
        assert state["parsed_task"]["snapshot_id"]
        assert len(state["pg_jobs"]) == 6 and len(state["requests_old"]) + len(state["requests_new"]) == 5
        for value in [*state["observed"], *state["literal_values"]]:
            check_observed(value, state["pg_jobs"], snapshots[0])
        assert [v["value"] for v in state["observed"]] == [7, 9, 9]
        assert [type(v["value"]).__name__ for v in state["literal_values"]] == ["int", "bool", "NoneType"]
        assert len({v["value_sha256"] for v in state["literal_values"]}) == 3
        assert state["replay_observed"]["http_observation"]["acquisition"] == "completed_request"
        assert all(state[k]["target_status"] == "unknown" for k in ["replay_observed", "legacy_observed", "tampered_observed"])
        assert state["legacy_observed"]["http_observation"] is None
        assert state["archive_history_preserved"] and not state["current_config_read"] and state["inspection_new_http_calls"] == 0
        formats[ext] = {"source_sha256": sha(original), "tool_jobs": 6, "business_http": 5,
            "matched_and_mismatched_sources_verified": True, "legacy_and_checkpoint_unknown": True}
    production = read(DOCS / (PREFIX + "production-first") / "verified.json")
    snapshot = fixed_environment_snapshot(production["snapshot"])
    for value in production["observations"]: check_observed(value, production["jobs"], snapshot)
    assert [hop["method"] for hop in production["observations"][1]["http_observation"]["hops"]] == ["POST", "GET"]
    assert len(production["business_http_old"]) == 2 and len(production["business_http_new"]) == 1
    assert production["API_restart"] and production["archived_history_preserved"] and production["current_config_removed"]
    assert production["inspection_new_http_calls"] == 0 and not production["external_default_model"]
    for row, job in zip(production["pg_jobs"], production["jobs"]):
        assert all(row[k] == job[k] for k in ["input_payload", "output_payload", "metadata"])
    assert sha((DOCS / (PREFIX + "production-first") / "requirements.md").read_bytes()) == production["source_sha256"]
    checkpoint = read(folder / "checkpoint.json")
    assert checkpoint["completed_request"]["completed_request"]["http_observation_v1"]["acquisition"] == "network"
    assert checkpoint["restored"]["execution_checkpoint"]["http_observation_v1"]["acquisition"] == "completed_request"
    assert len(checkpoint["requests"]) == 1
    retry = read(folder / "failed-retry.json")
    assert retry["final"]["response_body"] is retry["final"]["response_status"] is None
    assert "http_observation_v1" not in retry["final"]["execution_checkpoint"]
    roundtrip = read(folder / "roundtrip-error.json")
    assert len(roundtrip["roundtrip"]["execution_checkpoint"]["http_observation_v1"]["hops"]) == 3
    assert "omitted-value" not in json.dumps(roundtrip["roundtrip"]["execution_checkpoint"]["http_observation_v1"])
    assert "built in 7.26s" in (DOCS / (PREFIX + "build-first.log")).read_text("utf-8")
    for path in files:
        if path.suffix in {".json", ".txt", ".log", ".stdout", ".stderr", ".xml"}:
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", path.read_text("utf-8", errors="replace")), path
    return {"research_baseline": "bd1172d6", "plans_byte_identical": True, "plan_sha256": sha(plans[0]),
        "gitignore_unchanged": True, "previous_raw_evidence_verified": prior_counts, "raw_files": raw_files,
        "tests": tests, "actual_formats": formats, "cross_target_HTTP_reproduced_before_fix": True,
        "fixed_rules_independent_of_query": True, "production_API_restart_verified": True,
        "inspection_new_HTTP_calls": 0, "frontend_typecheck_build_passed": True,
        "deployment_status": "unknown", "requirement_applicability_confirmations_implemented": False,
        "run_environment_binding_implemented": False, "external_default_model_tested": False,
        "full_P0_P5_complete": False}


def verify_staged(result):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix() + "/"
    raw = {item["path"]: item for item in result["raw_files"]}
    expected = {prefix + name for name in RELATED | set(raw)}
    staged = {p for p in subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=repo).decode().split("\0") if p}
    assert staged == expected, {"unexpected": sorted(staged - expected), "missing": sorted(expected - staged)}
    for name, item in raw.items():
        blob = subprocess.check_output(["git", "show", ":" + prefix + name], cwd=repo)
        assert sha(blob) == item["sha256"] and len(blob) == item["size_bytes"], name
    for name in RELATED:
        blob = subprocess.check_output(["git", "show", ":" + prefix + name], cwd=repo)
        assert blob.replace(b"\r\n", b"\n") == (ROOT / name).read_bytes().replace(b"\r\n", b"\n"), name
    return {"scope_verified": True, "files": len(staged), "raw_evidence_blobs_verified": len(raw)}


if __name__ == "__main__":
    result = verify()
    if "--record" in sys.argv:
        if REPORT.exists(): raise RuntimeError("Preserve the previous report; use a new stage filename")
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    else:
        assert read(REPORT) == result
    summary = {k: result[k] for k in ["plan_sha256", "tests", "deployment_status", "full_P0_P5_complete"]}
    if "--staged" in sys.argv: summary["index"] = verify_staged(result)
    print(json.dumps(summary, ensure_ascii=False))

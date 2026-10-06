"""Verify saved real environment foundation evidence without resending requests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from requirement_execution_delivery_20261006 import ROOT, DOCS, PLANS, IGNORE_SHA

sys.path.insert(0, str(ROOT / "Agent_Server"))
from src.runtime.postgres_business_environment_store import (
    environment_identity, environment_request_digest, fixed_environment_snapshot,
    fixed_target_configuration,
)
from src.schemas.business_environment import BusinessEnvironmentRecord

PREFIX = "p4-environment-foundation-"
REPORT = DOCS / (PREFIX + "validation.json")
RELATED = {
    "Agent_Server/src/schemas/business_environment.py",
    "Agent_Server/src/runtime/postgres_business_environment_store.py",
    "Agent_Server/src/application/projects/business_environment_service.py",
    "Agent_Server/src/api/routes/business_environments.py",
    "Agent_Server/src/containers.py", "Agent_Server/src/main.py",
    "Agent_Server/tests/test_business_environment.py",
    "Agent_Server/tests/test_business_environment_production.py",
    "agent_web/src/services/businessEnvironments.types.ts",
    "agent_web/src/services/businessEnvironments.api.ts",
    "agent_web/src/services/businessEnvironments.api.test.ts",
    "agent_web/src/services/api.ts",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P4_ENVIRONMENT_APPLICABILITY_DESIGN.md",
    "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/environment_foundation_delivery_20261006.py",
    "docs/upgrade_2026-10-03/p4-environment-foundation-validation.json",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def registration(raw):
    value = BusinessEnvironmentRecord.model_validate(raw)
    assert value.id == environment_identity(value.project_id, "register", value.request.request_id)
    assert value.request_sha256 == environment_request_digest(value.project_id, "register", value.request)
    return value


def linked_snapshot(raw, registered=None):
    value = fixed_environment_snapshot(raw)
    if registered:
        assert (value.project_id, value.environment_id, value.environment_key, value.registration_request_sha256) == (
            registered.project_id, registered.id, registered.request.environment_key, registered.request_sha256)
    assert value.deployment_status == "unknown"
    return value


def mysql_configuration(rows, config):
    assert len(rows) == 1
    row = rows[0]
    assert row["scope_key"] == config["environment_id"] and row["revision"] == config["revision"]
    assert json.loads(row["payload_json"]) == config["payload"]


def verify():
    plans = [path.read_bytes() for path in PLANS]
    assert plans[0] == plans[1] == plans[2]
    assert sha((ROOT / ".gitignore").read_bytes()) == IGNORE_SHA
    for link in re.findall(r"\]\((G:/[^)]+)\)", plans[0].decode("utf-8")):
        assert Path(re.sub(r":\d+$", "", link)).is_file(), link
    previous = read(DOCS / "p4-execution-product-validation.json")
    prior_counts = {}
    for name in [*previous["previous_raw_evidence_verified"], "p4-execution-product-validation.json",
                 "p4-environment-research-validation.json"]:
        report = read(DOCS / name)
        for item in report["raw_files"]:
            data = (ROOT / item["path"]).read_bytes()
            assert len(data) == item["size_bytes"] and sha(data) == item["sha256"], item["path"]
        prior_counts[name] = len(report["raw_files"])
    files = sorted(p for p in DOCS.rglob("*") if p.is_file()
                   and p.relative_to(DOCS).parts[0].startswith(PREFIX) and p != REPORT)
    raw_files = [{"path": p.relative_to(ROOT).as_posix(), "size_bytes": p.stat().st_size,
                  "sha256": sha(p.read_bytes())} for p in files]
    tests = {}
    expected = {
        "live-first": (16, 0, 0), "live-verified": (57, 0, 1), "live-final": (19, 0, 0),
        "production-first": (1, 1, 0), "production-verified": (1, 1, 0),
        "production-final": (1, 0, 0), "client-first": (6, 0, 0),
    }
    for name, counts in expected.items():
        tree = ET.parse(DOCS / (PREFIX + name + ".xml")).getroot()
        suites = [tree] if tree.tag == "testsuite" else list(tree.findall("testsuite"))
        result = {k: sum(int(s.get(k, "0")) for s in suites) for k in ["tests", "failures", "errors", "skipped"]}
        assert (result["tests"], result["failures"], result["skipped"]) == counts and result["errors"] == 0, name
        result["time_seconds"] = round(sum(float(s.get("time", "0")) for s in suites), 3)
        result["skips"] = [{"name": case.get("name"), "reason": skip.get("message")}
                           for case in tree.iter("testcase") for skip in case.findall("skipped")]
        tests[name] = result
    build = (DOCS / (PREFIX + "build-first.log")).read_text("utf-8")
    assert "vue-tsc" in build and "built in 7.13s" in build
    folder = DOCS / (PREFIX + "live-final")
    formats = {}
    for ext in ["md", "docx", "pdf"]:
        state = read(folder / ext / "verified.json")
        reg = registration(state["registration"])
        config = fixed_target_configuration(state["configuration"])
        first, later = linked_snapshot(state["snapshot"], reg), linked_snapshot(state["later_snapshot"], reg)
        assert first.configuration == config and first.configuration.revision == 1 and later.configuration.revision == 2
        assert first.id != later.id and first.sha256 != later.sha256
        mysql_configuration(state["mysql"], state["configuration"])
        for version in [1, 2]:
            path = folder / ext / f"requirements-v{version}.{ext}"
            data, metadata = path.read_bytes(), read(Path(str(path) + ".json"))
            assert sha(data) == metadata["source_sha256"] and len(data) == metadata["source_size_bytes"]
        original = (folder / ext / f"requirements-v1.{ext}").read_bytes()
        assert sha(original) == state["original_sha256"] == state["document"]["original_asset"]["sha256"]
        assert state["parsed_snapshot"]["seed"]["original_asset"] == state["document"]["original_asset"]
        assert state["configuration_current_row_removed"] and not state["recovery_uses_current_config"]
        assert not state["environment_applied_to_plan_or_run"] and state["new_model_http_calls"] == 0
        formats[ext] = {"original_sha256": sha(original), "snapshot_sha256": first.sha256,
                        "later_snapshot_sha256": later.sha256, "parser_status": state["parsed_snapshot"]["status"]}
    lost = read(folder / "lost-replies.json")
    reg = registration(lost["registration"])
    for key in ["snapshot", "concurrent_snapshot"]:
        linked_snapshot(lost[key], reg)
    assert len(lost["pg_snapshots"]) == 2 and lost["actual_four_way_writes"] and lost["new_model_http_calls"] == 0
    assert {row["record"]["sha256"] for row in lost["pg_snapshots"]} == {lost[k]["sha256"] for k in ["snapshot", "concurrent_snapshot"]}
    race = read(folder / "config-and-archive-races.json")
    linked_snapshot(race["selected_read"])
    assert race["selected_read"]["configuration"]["revision"] == 1 and race["current_after_read"]["revision"] == 2
    assert race["pg_snapshots"] == [{"record": race["selected_read"]}]
    assert race["archive_race_status"] == 409 and not race["cross_database_latest_claim"]
    corruption = read(folder / "corruption.json")
    linked_snapshot(corruption["original_snapshot"])
    assert all(v is True for k, v in corruption.items() if k != "original_snapshot")
    unknown = read(folder / "mysql-lost-reply.json")
    linked_snapshot(unknown["original_capture"])
    for key, revision in [("original_configuration", 1), ("actual_committed_unknown_configuration", 2), ("new_save_configuration", 3)]:
        assert fixed_target_configuration(unknown[key]).revision == revision
    assert not unknown["config_save_claimed_idempotent"]
    boundary = read(folder / "maximum-table-name.json")
    linked_snapshot(boundary["actual_pg_capture"])
    assert len(boundary["configured_project_table"]) == 63 and all(len(t) <= 63 for t in boundary["derived_tables"])
    assert not boundary["new_project_name_limit_introduced"]
    production = read(DOCS / (PREFIX + "production-final") / "verified.json")
    linked_snapshot(production["fixed_snapshot"], registration(production["registration"]))
    assert production["pg_snapshots"] == [{"record": production["fixed_snapshot"]}]
    mysql_configuration(production["mysql_current_before_removal"], production["later_configuration"])
    assert production["target_or_model_requests"] == [] and production["production_API_restart"] and production["current_config_removed"]
    assert not production["environment_bound_to_plan_or_run"] and not production["autonomous_agent_execution"]
    original = (DOCS / (PREFIX + "production-final") / "requirements.md").read_bytes()
    assert sha(original) == production["source_sha256"] == production["document"]["original_asset"]["sha256"]
    cleanup = read(DOCS / (PREFIX + "production-first") / "failed-upload-object-cleanup.json")
    assert cleanup["verified_exact_fixture_object"] and cleanup["deleted_after_copy"]
    assert sha((DOCS / (PREFIX + "production-first") / "original-object.md").read_bytes()) == cleanup["source_sha256"]
    for path in files:
        if path.suffix in {".json", ".txt", ".log", ".stdout", ".stderr", ".xml"}:
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", path.read_text("utf-8", errors="replace")), path
    return {"research_baseline": "c1f34368", "plans_byte_identical": True, "plan_sha256": sha(plans[0]),
            "gitignore_unchanged": True, "previous_raw_evidence_verified": prior_counts, "tests": tests,
            "raw_files": raw_files, "actual_formats": formats, "production_API_restart_verified": True,
            "production_target_or_model_requests": 0, "frontend_typecheck_build_passed": True,
            "configuration_stored_in_mysql": True, "fixed_metadata_stored_in_postgres": True,
            "unknown_commit_recovery_verified": True, "four_way_capture_one_record_verified": True,
            "deployment_status": "unknown", "environment_bound_to_plan_or_run": False,
            "formal_environment_page_verified": False, "external_default_model_tested": False,
            "production_history_migration_run": False, "full_P0_P5_complete": False}


def verify_staged(result):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix() + "/"
    raw = {item["path"]: item for item in result["raw_files"]}
    expected = {prefix + name for name in RELATED | set(raw)}
    staged = {p for p in subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=repo).decode().split("\0") if p}
    assert staged == expected, {"unexpected": sorted(staged - expected), "missing": sorted(expected - staged)}
    for name, item in raw.items():
        blob = subprocess.check_output(["git", "show", ":" + prefix + name], cwd=repo)
        assert len(blob) == item["size_bytes"] and sha(blob) == item["sha256"], name
    for name in RELATED:
        blob = subprocess.check_output(["git", "show", ":" + prefix + name], cwd=repo)
        assert blob.replace(b"\r\n", b"\n") == (ROOT / name).read_bytes().replace(b"\r\n", b"\n"), name
    return {"scope_verified": True, "files": len(staged), "raw_evidence_blobs_verified": len(raw)}


if __name__ == "__main__":
    result = verify()
    if "--record" in sys.argv:
        if REPORT.exists():
            raise RuntimeError("Preserve the previous report; use a new stage filename")
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        assert read(REPORT) == result
    summary = {k: result[k] for k in ["plan_sha256", "tests", "deployment_status", "full_P0_P5_complete"]}
    if "--staged" in sys.argv:
        summary["index"] = verify_staged(result)
    print(json.dumps(summary, ensure_ascii=False))

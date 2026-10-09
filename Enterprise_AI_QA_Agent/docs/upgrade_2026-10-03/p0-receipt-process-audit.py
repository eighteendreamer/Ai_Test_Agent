"""Audit actual killed-process receipts and preserve earlier native evidence."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    prior = read(DOCS / "p1-document-generation-validation.json")
    historical = dict(prior["artifact_hashes"])
    manifests = {"p1-document-generation-validation.json": sha(DOCS / "p1-document-generation-validation.json")}
    for name, expected in prior["historical_manifest_sha256"].items():
        assert sha(DOCS / name) == expected, name
        historical.update(read(DOCS / name)["artifact_hashes"])
        manifests[name] = expected
    protected = prior["protected_files_sha256"]
    for relative, expected in {**historical, **protected}.items():
        assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        previous = subprocess.check_output(["git", "show",
            "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode().replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {previous, current}, path
        for path in plans:
            path.write_bytes(current.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
    tests = {}
    for path in DOCS.glob("p0-receipt-process-*.xml"):
        suite = ET.parse(path).getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        tests[path.stem] = {"passed": int(suite.get("tests")) - failed - skipped,
            "failed": failed, "skipped": skipped, "seconds": float(suite.get("time"))}
    assert tests["p0-receipt-process-first"]["failed"] == 2
    assert tests["p0-receipt-process-recovery"]["failed"] == 2
    assert tests["p0-receipt-process-verified"]["passed"] == 1
    assert tests["p0-receipt-process-verified"]["failed"] == 1
    assert tests["p0-receipt-process-delivery"]["passed"] == 2
    assert tests["p0-receipt-process-delivery"]["failed"] == 0
    assert tests["p0-receipt-process-contract"]["passed"] == 82
    assert tests["p0-receipt-process-contract"]["skipped"] == 2
    assert tests["p0-receipt-process-production-regression"]["passed"] == 1
    assert tests["p0-receipt-process-production-final"]["passed"] == 1
    assert tests["p0-receipt-process-fixture"]["passed"] == 1
    assert read(DOCS / "p0-receipt-process-compile.json")["exit_code"] == 0
    observations, downloads = {}, 0
    for boundary in ("invoking", "response"):
        directory = DOCS / "p0-receipt-process-delivery" / boundary
        value = read(directory / "summary.json")
        assert value["killed_api_exit_code"] != 0
        assert value["immediate_recovery"]["recovered_count"] == 0
        assert value["expired_recovery"]["recovered_count"] in (0, 1)
        assert value["formal_status"] == ("passed" if boundary == "response" else "error")
        assert len(value["model_calls"]) == 6
        assert sum(c["phase"] == "receipt_correction" for c in value["model_calls"]) == 1
        assert [verb for verb, _ in value["business_effects"]] == ["POST", "GET", "DELETE"]
        assert value["terminal_replay_model_calls"] == value["terminal_replay_business_effects"] == 0
        assert value["remaining_business_records"] == 0
        assert value["resource_lease_seconds"] == 300
        assert value["current_default_limit"] == 18 and value["original_turn_limit"] == 8
        correction = value["correction"]
        assert correction["phase"] == boundary and correction["total_model_calls"] == 5
        assert correction["model_parameters"]["max_output_tokens"] == 4096
        assert correction["runtime_parameters"]["transport_options"] == {"timeout_seconds": 60, "max_retries": 0}
        for kind in ("request", "response"):
            ref = correction.get(kind)
            if ref:
                raw = (directory / f"receipt-{kind}.json").read_bytes()
                assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                downloads += 1
        fixed_request = read(directory / "receipt-request.json")
        physical_request = read(directory / "model-006-request.json")
        assert fixed_request["tools"] == [] and not physical_request.get("tools")
        assert fixed_request["trace_context"]["logical_call_id"] == correction["call_id"]
        assert "DO-NOT-INJECT-TEMPLATE" not in json.dumps(fixed_request)
        before, final = read(directory / "before-kill.json"), read(directory / "after-recovery.json")
        assert final["attempts"][0]["status"] == "expired"
        assert final["attempts"][1]["recovered_from_attempt_id"] == final["attempts"][0]["id"]
        for key in ("context_hash", "execution_attempt_id", "session_id", "turn_id", "receipt_correction"):
            assert final["attempts"][1]["checkpoint_payload"][key] == before["attempts"][0]["checkpoint_payload"][key]
        resources = read(directory / "resources-after-cleanup.json")
        assert resources["active"] == resources["cleanup_pending"] == []
        assert all(n == 0 for n in resources["usage"].values())
        receipts = value["resource_cleanup_receipts"]
        assert len(receipts) == 1 and receipts[0]["status"] == "skipped"
        assert datetime.fromisoformat(receipts[0]["completed_at"]) >= datetime.fromisoformat(value["original_resource_lease_expires_at"])
        if boundary == "response":
            blocked = read(directory / "blocked-insert.json")
            assert blocked and all(r["wait_event_type"] == "Lock" and not r["granted"] for r in blocked)
        observations[boundary] = {"status": value["formal_status"], "http_sends": 6,
            "correction_sends": 1, "business_effects": 3, "attempts": 2,
            "cleanup_receipt": receipts[0]["status"]}
    regression = DOCS / "p0-receipt-process-production-final/8-receipt/p2-execution-worker-cli-probe"
    completion = read(regression / "completed.json")["results"][0]
    assert completion["status"] == "passed"
    for kind in ("request", "response"):
        ref = completion["actual"]["receipt_correction"][kind]
        raw = (regression / f"receipt-{kind}.json").read_bytes()
        assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
        downloads += 1
    cleanup_directory = DOCS / "p0-receipt-process-failure-cleanup"
    cleanup = read(cleanup_directory / "summary.json")
    assert len(cleanup) == 3
    for entry in cleanup:
        raw = (cleanup_directory / entry["file"]).read_bytes()
        ref = entry["reference"]
        assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
        assert entry["download_verified"] and entry["delete_not_found_status"] == 404
        downloads += 1
    # Failed batches retain native downloads too; do not overwrite or relabel them.
    earlier = [DOCS / "p0-receipt-process-first/invoking",
        DOCS / "p0-receipt-process-verified/invoking", DOCS / "p0-receipt-process-verified/response"]
    for directory in earlier:
        correction = read(directory / "before-kill.json")["attempts"][0]["checkpoint_payload"]["receipt_correction"]
        for kind in ("request", "response"):
            ref = correction.get(kind)
            if ref:
                raw = (directory / f"receipt-{kind}.json").read_bytes()
                assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                downloads += 1
    earlier_regression = DOCS / "p0-receipt-process-production-regression/8-receipt/p2-execution-worker-cli-probe"
    correction = read(earlier_regression / "completed.json")["results"][0]["actual"]["receipt_correction"]
    for kind in ("request", "response"):
        ref = correction[kind]
        raw = (earlier_regression / f"receipt-{kind}.json").read_bytes()
        assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
        downloads += 1
    assert downloads == 14
    files = [DOCS / "P0_RECEIPT_PROCESS_RECOVERY_RESEARCH.md"]
    for path in DOCS.glob("p0-receipt-process-*"):
        if path.name == "p0-receipt-process-validation.json":
            continue
        files.extend([p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path])
    exposed = re.compile(r'(?i)"(?:api_key|authorization|password|access_token)"\s*:\s*"(?!\*|redact|<|$)[^"\s]+"')
    for path in files:
        if path.suffix in {".json", ".stdout", ".stderr", ".log"}:
            assert not exposed.search(path.read_text(encoding="utf-8-sig", errors="replace")), path
    result = {"tests": tests, "observations": observations, "actual_object_downloads_checked": downloads,
        "historical_artifacts_checked": len(historical), "historical_manifest_sha256": manifests,
        "protected_files_sha256": protected, "plans_sha256": sha(plans[0]),
        "artifact_hashes": {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in sorted(set(files))},
        "remaining": "default Step and trusted UI receipt, general unknown business effects, broader P0-P5; goal incomplete"}
    (DOCS / "p0-receipt-process-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "objects": downloads, "prior_artifacts": len(historical),
        "new_artifacts": len(set(files)), "plans_equal": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()

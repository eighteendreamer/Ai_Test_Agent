"""Verify durable performance evidence without rewriting earlier audit results."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    historical, protected = {}, {}
    for name in ("p5-perf-measurement-validation.json", "p1-import-full-product-validation.json",
                 "p0-receipt-transport-validation.json", "p0-approval-budget-validation.json",
                 "p0-tool-evidence-validation.json", "plan-receipt-current-validation.json"):
        prior = read(DOCS / name)
        base = DOCS if name in {"p0-approval-budget-validation.json", "p0-tool-evidence-validation.json"} else ROOT
        for relative, expected in prior["artifact_hashes"].items():
            assert sha(base / relative) == expected, (name, relative)
        historical[name] = len(prior["artifact_hashes"])
        for relative, expected in prior.get("protected_files_sha256", {}).items():
            assert sha(ROOT / relative) == expected, relative
            protected[relative] = expected
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        old = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode("utf-8")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            value = path.read_text(encoding="utf-8")
            assert value in {old.replace("\r\n", "\n"), current}, f"External plan changed: {path}"
        content = current.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
        for path in plans:
            path.write_bytes(content)
    assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
    assert b"\r\r\n" not in plans[0].read_bytes()
    assert "### 6.89 " in plans[0].read_text(encoding="utf-8")
    tests = {}
    counts = {"before": (0, 1), "reproduction": (0, 1), "contract-first": (77, 0),
        "verified": (0, 1), "verified-final": (0, 1), "download-verified": (1, 0),
        "delivery": (1, 0), "expanded": (123, 0), "measurement-regression": (1, 0)}
    for suffix, expected in counts.items():
        suite = ET.parse(DOCS / f"p5-perf-evidence-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0, suffix
        tests[suffix] = {"passed": passed, "failed": failed, "skipped": skipped, "seconds": float(suite.get("time"))}
    evidence = DOCS / "p5-perf-evidence-delivery"
    facts = read(evidence / "facts.json")
    assert "no Agent/model invocation" in facts["scope"]
    assert [item["passed"] for item in facts["observations"]] == [True, False, False, False]
    assert [item["engine_runs"] for item in facts["observations"]] == [2, 1, 1, 1]
    assert facts["api_restart_persisted"] and facts["restored_bytes_equal"]
    assert facts["cross_project_download_status"] == 404 and facts["tampered_same_size_status"] == 409
    assert facts["replay_added_engine_runs"] == 0
    assert facts["storage_fault_engine_runs"] == facts["storage_fault_preserved_originals"] == 1
    assert facts["storage_fault_verdict"] == "inconclusive"
    rows = read(evidence / "postgres-finally.json")["postgres_tool_job_table"]
    assert len(rows) == 4
    artifacts = {}
    for name in ("normal", "missing", "malformed", "teardown"):
        record = read(evidence / (name + "-tool-record.json"))
        row = next(row for row in rows if row["id"] == record["job_id"])
        assert row["output_payload"] == {k: v for k, v in record["output"].items()
            if k not in {"download_urls", "download_markdown"}}
        assert row["status"] == record["status"]
        for artifact in record["output"]["artifacts"]:
            if artifact.get("evidence_schema") != "performance-execution-v1":
                continue
            assert artifact["owner"]["project_id"] == facts["project_id"]
            assert artifact["owner"]["session_id"] == facts["session_id"]
            assert artifact["owner"]["trace_id"] == record["trace_id"]
            assert artifact["path"].startswith("rustfs://")
            assert artifact["sha256"] not in {None, ""}
            artifacts[artifact["path"]] = artifact
    assert len(artifacts) == 24
    downloads = read(evidence / "downloads.json")
    assert len(downloads) == len(artifacts)
    for download in downloads:
        artifact = artifacts[download["uri"]]
        original = evidence / download["saved_relative_path"]
        assert original.resolve().is_relative_to(evidence.resolve())
        assert sha(original) == download["sha256"] == artifact["sha256"]
        assert original.stat().st_size == download["bytes"] == artifact["size_bytes"]
        if artifact["relative_path"] == "execution-manifest.json":
            manifest = read(original)
            assert manifest["owner"] == artifact["owner"]
            assert manifest["execution_id"] == artifact["execution_id"]
            assert manifest["environment_names"] in ([],
                ["QA_PERF_SMOKE", "QA_PERF_SMOKE_ITERATIONS", "QA_PERF_SMOKE_VUS"])
            assert "environment" not in manifest
            for item in manifest["artifacts"]:
                assert item == artifacts[item["path"]]
    normal = read(evidence / "normal-tool-record.json")["output"]
    assert normal["run"]["smoke_result"] == normal["smoke_result"]
    assert len({a["execution_id"] for a in normal["artifacts"] if a.get("execution_id")}) == 2
    teardown = read(evidence / "teardown-load-run.json")
    teardown_report = read(evidence / "teardown-load-report.json")
    assert teardown["exit_code"] == 107 and teardown["status"] == "failed"
    assert teardown_report["sla_result"]["passed"] is True and teardown_report["verdict"] == "inconclusive"
    local = read(evidence / "local-timeout-execution.json")
    assert local["timed_out"] and "captured stdout" in local["stdout"] and "captured stderr" in local["stderr"]
    assert local["evidence"]["status"] == "partial" and local["evidence"]["missing_artifacts"] == ["engine_summary"]
    partial = read(evidence / "partial-storage-run.json")
    assert partial["evidence"]["status"] == "partial" and len(partial["evidence"]["artifacts"]) == 1
    commands = read(evidence / "docker-commands.json")
    created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
    assert len(created) == 8 and all(len(c) == 64 for c in created)
    for container_id in created:
        assert any(c["args"][0] == "ps" and "id=" + container_id in c["args"]
            and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
    assert {row["container_id"] for row in read(evidence / "postgres-allocations.json")} == set(created)
    measurements = read(DOCS / "p5-perf-evidence-measurement-regression/facts.json")
    assert [v["report_verdict"] for v in measurements["variants"]] == ["pass", "pass", "inconclusive", "fail"]
    sources = ["Agent_Server/src/" + name for name in (
        "api/routes/sessions.py", "application/artifacts/artifact_storage_service.py",
        "application/performance/evidence.py", "application/performance/perf_runner_service.py",
        "application/runtime/tool_runtime_service.py", "modes/performance_testing_mode/plan_state.py",
        "modes/performance_testing_mode/report_builder.py", "modes/performance_testing_mode/runtime.py")]
    sources += ["Agent_Server/tests/" + name for name in (
        "test_performance_evidence_contract.py", "test_performance_evidence_live.py",
        "test_performance_docker_measurement.py", "test_performance_report_builder.py")]
    files = []
    for path in DOCS.glob("p5-perf-evidence-*"):
        if path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file())
        elif path.name not in {"p5-perf-evidence-audit.py", "p5-perf-evidence-validation.json"}:
            files.append(path)
    result = {"baseline": "cf703e05", "scope": facts["scope"], "tests": tests,
        "full_P0_P5_complete": False, "real_model_acceptance_deferred": True,
        "plans_equal": True, "plan_sha256": sha(plans[0]), "downloaded_original_count": len(downloads),
        "historical_artifacts_unchanged": historical, "protected_files_sha256": protected,
        "current_source_hashes": {relative: sha(ROOT / relative) for relative in sources},
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(files)}}
    (DOCS / "p5-perf-evidence-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "artifacts": len(files), "historical": historical,
        "downloaded_originals": len(downloads), "plans_equal": True}))


if __name__ == "__main__":
    main()

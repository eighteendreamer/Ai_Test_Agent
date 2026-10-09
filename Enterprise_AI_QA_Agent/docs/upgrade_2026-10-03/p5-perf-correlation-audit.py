"""Audit real response/dependency evidence and preserve earlier validation files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def absent_containers(directory, expected):
    commands = read(directory / "docker-commands.json")
    created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
    assert len(created) == expected and all(re.fullmatch(r"[a-f0-9]{64}", c) for c in created)
    for identity in created:
        assert any(c["args"][0] == "ps" and "id=" + identity in c["args"]
            and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands), identity
    return created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    historical, protected = {}, {}
    for name in ("p5-perf-evidence-validation.json", "p5-perf-measurement-validation.json",
        "p1-import-full-product-validation.json", "p0-receipt-transport-validation.json",
        "p0-approval-budget-validation.json", "p0-tool-evidence-validation.json", "plan-receipt-current-validation.json"):
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
        old = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode("utf-8").replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {old, current}, f"External plan changed: {path}"
        content = current.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
        for path in plans:
            path.write_bytes(content)
    assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
    assert b"\r\r\n" not in plans[0].read_bytes() and "### 6.90 " in plans[0].read_text(encoding="utf-8")
    tests = {}
    for suffix, expected in {"before": (0, 1), "verified": (1, 0), "full-verified": (0, 1),
        "expanded": (140, 0), "worker-verified": (1, 0), "delivery-contract": (140, 0),
        "measurement-regression": (1, 0), "evidence-regression": (1, 0)}.items():
        suite = ET.parse(DOCS / f"p5-perf-correlation-{suffix}.xml").getroot().find("testsuite")
        failed, skipped = int(suite.get("failures")) + int(suite.get("errors")), int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0, suffix
        tests[suffix] = {"passed": passed, "failed": failed, "skipped": skipped, "seconds": float(suite.get("time"))}
    before = read(DOCS / "p5-perf-correlation-before/observations.json")
    assert [o["passed"] for o in before] == [False, True, True, False]
    interrupted = read(DOCS / "p5-perf-correlation-full-verified/observations.json")
    assert len(interrupted) == 10 and all(o["passed"] == o["expected"] for o in interrupted)
    directory = DOCS / "p5-perf-correlation-worker-verified"
    facts = read(directory / "facts.json")
    assert "no Agent/model invocation" in facts["scope"]
    observations = facts["observations"]
    assert len(observations) == 12 and all(o["passed"] == o["expected"] for o in observations)
    for item in observations:
        smoke = read(directory / (item["variant"] + "-smoke.json"))
        assert smoke["request_validation"]["status"] == ("passed" if item["expected"] else "failed"), item["variant"]
    for variant, paths in (("chain", ["/login", "/profile"] * 3),
        ("missing-chain", ["/missing", "/plain"] * 3), ("explicit-source", ["/plain", "/login", "/profile"] * 3)):
        events = next(o["events"] for o in observations if o["variant"] == variant)
        assert [e["path"] for e in events] == paths
        for i, event in enumerate(events):
            if event["path"] == "/profile":
                assert event["authorization_accepted"] and event["credential_sha256"] == events[i-1]["issued_sha256"]
            if event["path"] == "/plain":
                assert not event["authorization_present"]
    assert [o["verdict"] for o in facts["loads"]] == ["pass", "fail"]
    assert all(o["exit_code"] == 0 and o["sla_passed"] for o in facts["loads"])
    rows = read(directory / "postgres-metrics.json")
    assert len(rows) == 2 and sorted(r["verdict"] for r in rows) == ["fail", "pass"]
    usages, receipts = read(directory / "cleanup-usage.json"), read(directory / "postgres-cleanup.json")
    assert len(usages) == len(receipts) == 14
    assert all(u["exit_code"] == 0 and any(u["before"].values()) and not any(u["after"].values()) for u in usages)
    assert all(r["status"] == "completed" and r["completed_at"] and not r["error_type"] for r in receipts)
    created = absent_containers(directory, 14)
    allocations = read(directory / "postgres-allocations.json")
    assert {a["container_id"] for a in allocations} == set(created)
    assert {a["allocation_id"] for a in allocations} == {r["allocation_id"] for r in receipts}
    evidence = DOCS / "p5-perf-correlation-evidence-regression"
    delivery = read(evidence / "facts.json")
    assert delivery["api_restart_persisted"] and delivery["restored_bytes_equal"]
    assert delivery["cross_project_download_status"] == 404 and delivery["tampered_same_size_status"] == 409
    assert delivery["replay_added_engine_runs"] == 0
    normal = read(evidence / "normal-tool-record.json")["output"]
    assert normal["request_validation"]["status"] == "passed"
    assert normal["smoke_result"]["request_validation"]["status"] == "passed"
    metrics = next(a for a in normal["artifacts"] if a["type"] == "performance_metrics_json")
    assert json.loads(metrics["content"])["request_validation"] == normal["request_validation"]
    persisted = read(evidence / "postgres-finally.json")["postgres_tool_job_table"]
    assert any(r["output_payload"].get("request_validation") == normal["request_validation"] for r in persisted)
    downloads = read(evidence / "downloads.json")
    assert len(downloads) == 24
    for item in downloads:
        original = evidence / item["saved_relative_path"]
        assert original.resolve().is_relative_to(evidence.resolve())
        assert sha(original) == item["sha256"] and original.stat().st_size == item["bytes"]
    absent_containers(evidence, 8)
    measurement = read(DOCS / "p5-perf-correlation-measurement-regression/facts.json")
    assert [v["report_verdict"] for v in measurement["variants"]] == ["pass", "pass", "inconclusive", "fail"]
    sources = ["Agent_Server/src/" + name for name in (
        "application/performance/k6_engine_adapter.py", "application/performance/perf_runner_service.py",
        "application/performance/request_validation.py", "application/runtime/tool_runtime_service.py",
        "modes/performance_testing_mode/plan_state.py", "modes/performance_testing_mode/prompt_contract.py",
        "modes/performance_testing_mode/report_builder.py", "modes/performance_testing_mode/runtime.py")]
    sources += ["Agent_Server/tests/test_performance_correlation_live.py", "Agent_Server/tests/test_performance_correlation_contract.py"]
    files = []
    for path in DOCS.glob("p5-perf-correlation-*"):
        if path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file())
        elif path.name not in {"p5-perf-correlation-audit.py", "p5-perf-correlation-validation.json"}:
            files.append(path)
    for path in files:
        value = path.read_bytes().decode("utf-8", errors="replace")
        assert not re.search(r"Bearer [a-f0-9]{32}(?![a-f0-9])", value), f"Raw credential: {path}"
    result = {"baseline": "d3e36b09", "scope": facts["scope"], "tests": tests,
        "full_P0_P5_complete": False, "real_model_acceptance_deferred": True,
        "plans_equal": True, "plan_sha256": sha(plans[0]), "cleanup_receipts": len(receipts),
        "downloaded_original_count": len(downloads), "historical_artifacts_unchanged": historical,
        "protected_files_sha256": protected, "current_source_hashes": {p: sha(ROOT / p) for p in sources},
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(files)}}
    (DOCS / "p5-perf-correlation-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "artifacts": len(files), "historical": historical,
        "cleanup_receipts": len(receipts), "downloads": len(downloads), "plans_equal": True}))


if __name__ == "__main__":
    main()

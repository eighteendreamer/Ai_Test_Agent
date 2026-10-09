"""Verify project/legacy/engine facts while preserving all previous evidence."""
import argparse
import hashlib
import json
from pathlib import Path
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
    prior = read(DOCS / "p5-jmeter-report-validation.json")
    for relative, expected in prior["artifact_hashes"].items():
        assert sha(ROOT / relative) == expected, relative
    for relative, expected in prior["protected_files_sha256"].items():
        assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        old = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode("utf-8").replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {old, current}, path
        for path in plans:
            path.write_bytes(current.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
    assert b"\r\r\n" not in plans[0].read_bytes()
    tests = {}
    for suffix, expected in {"before": (0, 1), "store-verified": (2, 0), "store-delivery": (2, 0),
        "mode-verified": (1, 0), "contract-first": (45, 0), "expanded": (154, 0), "measurement-regression": (1, 0)}.items():
        suite = ET.parse(DOCS / f"p5-perf-project-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0, suffix
        tests[suffix] = {"passed": passed, "failed": failed, "seconds": float(suite.get("time"))}
    before = read(DOCS / "p5-perf-project-before/facts.json")
    assert before["b_baseline"] and before["b_listing"] and before["b_overwrite_accepted"]
    assert before["rows"][0]["verdict"] == "fail"
    store = read(DOCS / "p5-perf-project-store-delivery/final.json")
    assert len(store["rows"]) == 3
    assert sum(store["concurrent_same_id_accepted"]) == 1
    assert store["restart_scoped_reads"] and not store["cross_project_overwrite"] and not store["unknown_project_saved"]
    migration = read(DOCS / "p5-perf-project-store-delivery/migration/facts.json")
    assert migration["legacy_before"] == migration["legacy_after"] and migration["repeated_initialize"]
    assert not migration["adopt_legacy"] and migration["scoped_baseline"] is None and not migration["scoped_history"]
    directory = DOCS / "p5-perf-project-mode-verified/mode"
    facts = read(directory / "facts.json")
    assert facts["engine_runs"] == 6 and facts["replay_added_engines"] == 0
    assert facts["b_comparison"] is None and facts["a_comparison"] is not None
    rows = read(directory / "postgres-metrics.json")
    assert [row["project_id"] for row in rows] == [facts["project_a"], facts["project_b"], facts["project_a"]]
    assert all(row["target_url"] == facts["target_url"] for row in rows)
    assert len(read(directory / "executions.json")) == 6
    receipts = read(directory / "postgres-cleanup.json")
    assert len(receipts) == 7 and all(row["status"] == "completed" for row in receipts)
    downloads = read(directory / "downloads.json")
    for download in downloads:
        original = directory / download["path"]
        assert original.resolve().is_relative_to(directory.resolve())
        assert sha(original) == download["sha256"] and original.stat().st_size == download["bytes"]
    commands = read(directory / "docker-commands.json")
    created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
    assert len(created) == 7
    for identity in created:
        assert any(c["args"][0] == "ps" and "id=" + identity in c["args"] and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
    measured = read(DOCS / "p5-perf-project-measurement-regression/facts.json")
    assert [v["report_verdict"] for v in measured["variants"]] == ["pass", "pass", "inconclusive", "fail"]
    measured_rows = read(DOCS / "p5-perf-project-measurement-regression/postgres-metrics.json")
    assert all(row["project_id"] == measured["project_id"] for row in measured_rows)
    artifacts = [DOCS / "P5_PERFORMANCE_PROJECT_RESEARCH.md"]
    for suffix in ("before", "store-verified", "store-delivery", "mode-verified", "measurement-regression"):
        artifacts += [p for p in (DOCS / f"p5-perf-project-{suffix}").rglob("*") if p.is_file()]
    artifacts += list(DOCS.glob("p5-perf-project-*.log")) + list(DOCS.glob("p5-perf-project-*.xml"))
    sources = [ROOT / "Agent_Server" / name for name in (
        "src/application/performance/perf_metrics_store.py", "src/modes/performance_testing_mode/plan_state.py",
        "src/modes/performance_testing_mode/runtime.py", "src/modes/performance_testing_mode/prompt_contract.py",
        "tests/test_performance_project_baseline_live.py", "tests/test_performance_project_scope.py",
        "tests/test_performance_docker_measurement.py", "tests/test_performance_correlation_live.py")]
    result = {"scope": facts["scope"], "tests": tests, "download_count": len(downloads),
        "containers_absent": created, "plans_equal": True, "plan_sha256": sha(plans[0]),
        "historical_artifacts_unchanged": len(prior["artifact_hashes"]),
        "protected_files_sha256": prior["protected_files_sha256"],
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in artifacts},
        "remaining": "legacy ownership/production migration, baseline comparability, full Agent/App, capacity/stream/security/UI and other P0-P5; Step deferred"}
    (DOCS / "p5-perf-project-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "downloads": len(downloads), "historical": len(prior["artifact_hashes"]), "plans_equal": True}))


if __name__ == "__main__":
    main()

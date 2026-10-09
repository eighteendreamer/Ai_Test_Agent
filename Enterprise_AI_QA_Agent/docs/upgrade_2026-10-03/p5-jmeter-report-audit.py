"""Audit report recovery evidence without rewriting previous validation files."""
import argparse
import csv
import hashlib
import io
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
    prior = read(DOCS / "p5-jmeter-validation.json")
    for relative, expected in prior["artifact_hashes"].items():
        assert sha(ROOT / relative) == expected, relative
    for relative, expected in prior["protected_files_sha256"].items():
        assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        old = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode("utf-8").replace("\r\n", "\n")
        content = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {old, content}, path
        for path in plans:
            path.write_bytes(content.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
    assert b"\r\r\n" not in plans[0].read_bytes()
    tests = {}
    for suffix, expected in {"before": (0, 1), "pg-before": (0, 1), "contract-before": (0, 4),
        "contract-fixed": (26, 0), "expanded": (149, 0), "verified": (1, 0),
        "measurement-regression": (1, 0), "evidence-regression": (1, 0)}.items():
        suite = ET.parse(DOCS / f"p5-jmeter-report-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0, suffix
        tests[suffix] = {"passed": passed, "failed": failed, "seconds": float(suite.get("time"))}
    before = read(DOCS / "p5-jmeter-report-pg-before/observations.json")[-1]
    assert before["variant"] == "pg-rejected" and before["tool_status"] == "completed" and not before["metrics_row_exists"]
    directory = DOCS / "p5-jmeter-report-verified"
    facts = read(directory / "facts.json")
    observations = facts["observations"]
    assert [o["tool_status"] for o in observations] == ["completed", "completed", "partial"]
    assert [o["verdict"] for o in observations] == ["pass", "fail", "pass"]
    assert [o["metrics_row_exists"] for o in observations] == [True, True, False]
    assert facts["pg_resume_added_engines"] == facts["replay_added_engines"] == 0
    executions = read(directory / "executions.json")
    assert len(executions) == 6
    for execution in executions:
        rows = list(csv.DictReader(io.StringIO(json.loads(execution["result"]["summary_json"])["__jtl"])))
        assert all(row["success"] == "true" for row in rows)
        assert all(row["qa_extract_0"] == "true" for row in rows)
        assert all(row["qa_dependency_0"] == "true" for row in rows if row["label"] == "profile")
        assert max(int(row["allThreads"]) for row in rows) == (1 if execution["smoke"] else 3)
        assert len(rows) == 6 if execution["smoke"] else len(rows) > 6
    resumed = read(directory / "pg-resumed-record.json")
    rejected = read(directory / "pg-rejected-record.json")
    assert resumed["output"]["run_id"] == rejected["output"]["run_id"]
    assert resumed["output"]["metrics_persistence"]["status"] == "saved"
    assert rejected["output"]["metrics_persistence"]["status"] == "failed"
    rows = read(directory / "postgres-metrics-final.json")
    assert len(rows) == 3 and sorted(row["verdict"] for row in rows) == ["fail", "pass", "pass"]
    receipts = read(directory / "postgres-cleanup.json")
    assert len(receipts) == 6 and all(row["status"] == "completed" for row in receipts)
    downloads = read(directory / "downloads.json")
    saved = 0
    for download in downloads:
        if download["saved"]:
            original = directory / download["saved"]
            assert original.resolve().is_relative_to(directory.resolve())
            assert sha(original) == download["sha256"] and original.stat().st_size == download["bytes"]
            saved += 1
    commands = read(directory / "docker-commands.json")
    created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
    assert len(created) == 6
    for identity in created:
        assert any(c["args"][0] == "ps" and "id=" + identity in c["args"] and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
    artifacts = [DOCS / "P5_JMETER_REPORT_RESEARCH.md"]
    for suffix in ("before", "pg-before", "verified", "measurement-regression", "evidence-regression"):
        artifacts += [p for p in (DOCS / f"p5-jmeter-report-{suffix}").rglob("*") if p.is_file()]
    artifacts += [p for p in DOCS.glob("p5-jmeter-report-*.xml") if p.is_file()]
    artifacts += [p for p in DOCS.glob("p5-jmeter-report-*.log") if p.is_file()]
    sources = [ROOT / "Agent_Server" / name for name in (
        "src/application/performance/perf_metrics_store.py", "src/modes/performance_testing_mode/runtime.py",
        "src/modes/performance_testing_mode/prompt_contract.py", "tests/test_performance_correlation_live.py",
        "tests/test_performance_jmeter_report_live.py", "tests/test_performance_report_persistence.py")]
    result = {"scope": facts["scope"], "tests": tests, "download_count": len(downloads), "saved_originals": saved,
        "containers_absent": created, "plans_equal": True, "plan_sha256": sha(plans[0]),
        "historical_artifacts_unchanged": len(prior["artifact_hashes"]),
        "protected_files_sha256": prior["protected_files_sha256"],
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in artifacts},
        "remaining": "project baseline isolation, full Agent/App, capacity/stream/security/UI and other P0-P5; Step deferred"}
    (DOCS / "p5-jmeter-report-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "downloads": len(downloads), "saved_originals": saved,
        "historical": len(prior["artifact_hashes"]), "plans_equal": True}))


if __name__ == "__main__":
    main()

"""Audit physical requests, iteration ownership, raw downloads and historical preservation."""
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
    args = argparse.ArgumentParser()
    args.add_argument("--sync", action="store_true")
    parsed = args.parse_args()
    prior = read(DOCS / "p5-perf-project-validation.json")
    older = read(DOCS / "p5-jmeter-report-validation.json")
    for manifest in (prior, older):
        for relative, expected in manifest["artifact_hashes"].items():
            assert sha(ROOT / relative) == expected, relative
        for relative, expected in manifest["protected_files_sha256"].items():
            assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if parsed.sync:
        old = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode("utf-8").replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {old, current}, path
        for path in plans:
            path.write_bytes(current.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
    tests = {}
    for suffix, expected in {"before": (0, 1), "fixed": (1, 0), "verified": (1, 0), "delivery": (1, 0),
        "response-regression": (1, 0), "contract-first": (43, 0), "contract": (52, 0), "expanded-verified": (163, 0)}.items():
        suite = ET.parse(DOCS / f"p5-jmeter-dependencies-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0, suffix
        tests[suffix] = {"passed": passed, "failed": failed, "seconds": float(suite.get("time"))}
    assert "ERROR: file or directory not found: tests/test_mode_skills.py" in (DOCS / "p5-jmeter-dependencies-expanded.log").read_text(encoding="utf-8")
    before = read(DOCS / "p5-jmeter-dependencies-before/observations.json")
    assert before[0]["counts"] == {"/source": 3, "/bridge": 3, "/final": 3, "/independent": 3}
    assert sum(e["status"] == 401 for e in before[0]["events"]) == 6
    checked_downloads, absent = 0, []
    for suffix in ("before", "fixed", "verified", "delivery", "response-regression"):
        directory = DOCS / f"p5-jmeter-dependencies-{suffix}"
        for manifest in directory.glob("*-downloads.json"):
            for download in read(manifest):
                local = directory / (download.get("path") or download["relative_path"])
                assert local.resolve().is_relative_to(directory.resolve())
                assert sha(local) == download["sha256"] and local.stat().st_size == download["bytes"]
                checked_downloads += 1
        commands = read(directory / "docker-commands.json")
        created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
        assert len(created) == (7 if suffix in {"verified", "delivery"} else 5 if suffix == "response-regression" else 1)
        for identity in created:
            assert any(c["args"][0] == "ps" and "id=" + identity in c["args"] and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
        absent.extend(created)
    for suffix in ("verified", "delivery"):
        directory = DOCS / f"p5-jmeter-dependencies-{suffix}"
        facts = read(directory / "facts.json")
        assert [o["counts"] for o in facts["observations"]] == [
            {"/source": 3, "/independent": 3}, {"/source": 4, "/bridge": 2, "/final": 2, "/independent": 4},
            {"/source": 6, "/bridge": 6, "/final": 6, "/independent": 6},
            {"/source": 3, "/independent": 3}, {"/source": 3, "/extra": 3, "/independent": 3}]
        assert [o["passed"] for o in facts["observations"]] == [False, False, True, False, False]
        receipts = read(directory / "postgres-cleanup.json")
        assert len(receipts) == 7 and all(r["status"] == "completed" for r in receipts)
        rows = read(directory / "postgres-metrics.json")
        assert [r["verdict"] for r in rows] == ["fail", "pass"]
        assert all(r["project_id"] == facts["project_id"] for r in rows)
        for variant, count, verdict in (("missing", 6, "fail"), ("valid", 12, "pass")):
            report = read(directory / f"report-{variant}-report.json")
            assert report["metrics"]["samples"] == count and report["sla_result"]["passed"] and report["verdict"] == verdict
        if suffix == "delivery":
            for observation in facts["observations"]:
                events = observation["events"]
                issued = {(e["thread"], e["iteration"], e["path"]): e["issued_sha256"] for e in events if e["issued_sha256"]}
                for event in events:
                    assert event["thread"].isdigit() and event["iteration"].isdigit()
                    if event["path"] in {"/bridge", "/final"}:
                        source = "/source" if event["path"] == "/bridge" else "/bridge"
                        assert event["accepted"] and event["credential_sha256"] == issued[(event["thread"], event["iteration"], source)]
                    else:
                        assert not event["header_present"]
            for variant in ("missing", "mixed", "valid", "tail", "multiple"):
                summary = read(directory / (variant + "-summary.json"))
                assert int(summary["Total"]["sampleCount"]) == len(list(csv.DictReader(io.StringIO(summary["__jtl"]))))
    artifacts = [DOCS / "P5_JMETER_DEPENDENCIES_RESEARCH.md"]
    artifacts += [p for p in DOCS.glob("p5-jmeter-dependencies-*.*") if p.suffix in {".log", ".xml"}]
    for suffix in ("before", "fixed", "verified", "delivery", "response-regression"):
        artifacts += [p for p in (DOCS / f"p5-jmeter-dependencies-{suffix}").rglob("*") if p.is_file()]
    sources = [ROOT / "Agent_Server" / name for name in (
        "src/application/performance/jmeter_engine_adapter.py", "src/application/performance/request_validation.py",
        "src/modes/performance_testing_mode/report_builder.py", "src/modes/performance_testing_mode/prompt_contract.py",
        "tests/test_performance_jmeter_adapter.py", "tests/test_performance_jmeter_live.py",
        "tests/test_performance_jmeter_dependencies.py", "tests/test_performance_jmeter_dependencies_live.py")]
    result = {"tests": tests, "download_count": checked_downloads, "containers_absent": absent,
        "plan_sha256": sha(plans[0]), "plans_equal": True,
        "historical_artifacts_unchanged": len(prior["artifact_hashes"]) + len(older["artifact_hashes"]),
        "protected_files_sha256": prior["protected_files_sha256"],
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in artifacts},
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "remaining": "complex JSON/body, Agent/App, capacity/AI stream/security/UI, production history and remaining P0-P5; Step deferred"}
    (DOCS / "p5-jmeter-dependencies-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "downloads": checked_downloads, "absent": len(absent), "plans_equal": True}))


if __name__ == "__main__":
    main()

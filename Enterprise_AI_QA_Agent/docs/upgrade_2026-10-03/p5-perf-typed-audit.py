"""Verify raw typed-response evidence and preserve prior immutable artifacts."""
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
    historical = [read(DOCS / name) for name in ("p5-perf-body-validation.json",
        "p5-jmeter-dependencies-validation.json", "p5-perf-project-validation.json", "p5-jmeter-report-validation.json")]
    old_hashes = {}
    for manifest in historical:
        old_hashes.update(manifest["artifact_hashes"])
        for relative, expected in {**manifest["artifact_hashes"], **manifest["protected_files_sha256"]}.items():
            assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        previous = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode().replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {previous, current}, path
        for path in plans:
            path.write_bytes(current.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
    tests = {}
    for path in DOCS.glob("p5-perf-typed-*.xml"):
        suite = ET.parse(path).getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        assert skipped == 0, path
        tests[path.stem] = {"passed": int(suite.get("tests")) - failed, "failed": failed,
            "seconds": float(suite.get("time"))}
    assert tests["p5-perf-typed-delivery"]["passed"] == 1 and tests["p5-perf-typed-delivery"]["failed"] == 0
    assert tests["p5-perf-typed-final"]["passed"] == 182 and tests["p5-perf-typed-final"]["failed"] == 0
    for name in ("dependencies-verified", "body-regression"):
        assert tests["p5-perf-typed-" + name]["passed"] == 1 and tests["p5-perf-typed-" + name]["failed"] == 0
    observations = read(DOCS / "p5-perf-typed-delivery/observations.json")
    assert len(observations) == 36
    for o in observations:
        assert o["passed"] == o["expected"] and o["exit_code"] == 0, o
        assert o["counts"] == ({"/source": 3, "/consumer": 3, "/plain": 3} if o["expected_consumer"] else {"/source": 3, "/plain": 3}), o
        assert all(e["header_matches"] for e in o["events"] if e["path"] == "/consumer"), o
    final = DOCS / "p5-perf-typed-delivery"
    facts, metrics, receipts = read(final / "facts.json"), read(final / "postgres-metrics.json"), read(final / "postgres-cleanup.json")
    assert len(receipts) == 40 and all(r["status"] == "completed" for r in receipts)
    assert [r["verdict"] for r in metrics] == ["pass", "fail", "pass", "fail"]
    assert all(str(r["project_id"]) == facts["project_id"] for r in metrics)
    for engine in ("k6", "jmeter"):
        for variant in ("string-null", "object-header"):
            report = read(final / f"report-{engine}-{variant}-report.json")
            assert report["verdict"] == ("pass" if variant == "string-null" else "fail") and report["sla_result"]["passed"]
    legacy = read(DOCS / "p5-perf-typed-reproduction/observations.json")
    assert [o["passed"] for o in legacy[4:]] == [False, True, True, False]
    downloads, stored, absent, requests = 0, 0, [], {}
    artifacts = [DOCS / "P5_PERFORMANCE_TYPED_RESPONSE_RESEARCH.md"]
    artifacts += [p for p in DOCS.glob("p5-perf-typed-*.*") if p.suffix in {".log", ".xml"}]
    for directory in [p for p in DOCS.glob("p5-perf-typed-*") if p.is_dir()]:
        artifacts += [p for p in directory.rglob("*") if p.is_file()]
        if (directory / "target-events.json").exists():
            requests[directory.name] = len(read(directory / "target-events.json"))
        for manifest in directory.glob("*-downloads.json"):
            for item in read(manifest):
                downloads += 1
                local_name = item.get("stored_path") or item.get("path")
                if local_name:
                    local = directory / local_name
                    assert local.resolve().is_relative_to(directory.resolve()), local
                    assert sha(local) == item["sha256"] and local.stat().st_size == item["bytes"], local
                    stored += 1
        command_path = directory / "docker-commands.json"
        if command_path.exists():
            commands = read(command_path)
            identities = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
            for identity in identities:
                assert any(c["args"][0] == "ps" and "id=" + identity in c["args"] and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands), identity
            absent += identities
    assert len(set(absent)) == len(absent)
    sources = [ROOT / "Agent_Server" / name for name in ("src/application/performance/jmeter_engine_adapter.py",
        "src/application/performance/request_validation.py", "src/modes/performance_testing_mode/prompt_contract.py",
        "src/modes/performance_testing_mode/report_builder.py", "tests/test_performance_jmeter_adapter.py",
        "tests/test_performance_typed_response.py", "tests/test_performance_typed_response_live.py",
        "tests/test_performance_jmeter_dependencies_live.py")]
    output = {"tests": tests, "actual_downloads_sha_size_checked": downloads, "stored_raw_files_checked": stored,
        "physical_requests": requests, "containers_absent": absent, "plans_equal": True, "plan_sha256": sha(plans[0]),
        "historical_artifacts_unchanged": len(old_hashes), "protected_files_sha256": historical[0]["protected_files_sha256"],
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in artifacts},
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "remaining": "full P0-P5/Agent/App/capacity/security/UI and extended data contracts; Step deferred"}
    (DOCS / "p5-perf-typed-validation.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("tests", "physical_requests", "actual_downloads_sha_size_checked", "stored_raw_files_checked", "historical_artifacts_unchanged", "plans_equal")}))


if __name__ == "__main__":
    main()

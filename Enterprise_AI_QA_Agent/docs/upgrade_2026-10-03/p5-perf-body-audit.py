"""Audit actual body bytes, reports, cleanup and immutable preceding evidence."""
import argparse
import base64
from collections import Counter
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
    historical = [read(DOCS / name) for name in ("p5-jmeter-dependencies-validation.json",
        "p5-perf-project-validation.json", "p5-jmeter-report-validation.json")]
    for manifest in historical:
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
    for variant, wanted in {"before": (0, 1), "fixed": (1, 0), "verified": (1, 0), "expanded": (169, 0)}.items():
        suite = ET.parse(DOCS / f"p5-perf-body-{variant}.xml").getroot().find("testsuite")
        failure = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failure - skipped
        assert (passed, failure) == wanted and skipped == 0
        tests[variant] = {"passed": passed, "failed": failure, "seconds": float(suite.get("time"))}
    before = read(DOCS / "p5-perf-body-before/observations.json")
    assert [o["passed"] for o in before] == [False, False]
    assert all(e["bytes"] == 0 for e in before[1]["events"])
    assert all(e["actual"] == "中文 2path\nline" for e in before[0]["events"] if e["path"] == "/text")
    downloads, stored, absent, requests = 0, 0, [], {}
    for variant in ("before", "fixed", "verified"):
        directory = DOCS / f"p5-perf-body-{variant}"
        observations = read(directory / "observations.json")
        events = read(directory / "target-events.json")
        requests[variant] = len(events)
        for event in events:
            body = base64.b64decode(event["body_base64"])
            assert len(body) == event["bytes"] and hashlib.sha256(body).hexdigest() == event["body_sha256"]
        if variant != "before":
            assert all(e["matched"] for e in events)
            for observation in observations:
                assert observation["passed"] and observation["exit_code"] == 0
                assert all(n == 3 for n in Counter(e["path"] for e in observation["events"]).values())
            facts = read(directory / "facts.json")
            metrics = read(directory / "postgres-metrics.json")
            receipts = read(directory / "postgres-cleanup.json")
            assert len(metrics) == 2 and all(row["verdict"] == "pass" and str(row["project_id"]) == facts["project_id"] for row in metrics)
            assert len(receipts) == 4 and all(r["status"] == "completed" for r in receipts)
            for engine in ("k6", "jmeter"):
                report = read(directory / f"report-{engine}-report.json")
                assert report["verdict"] == "pass" and report["sla_result"]["passed"]
                assert report["metrics"]["samples"] == (21 if variant == "verified" else 6)
            if variant == "verified":
                for observation in observations:
                    plan = read(directory / (observation["engine"] + "-plan.json"))
                    expected = {t["name"]: t for t in plan["targets"]}
                    for event in observation["events"]:
                        target = expected[event["path"][1:]]
                        assert event["method"] == target["method"] and event["content_type"] == target["headers"]["Content-Type"]
                        body = target["body_template"]
                        if not isinstance(body, dict):
                            assert base64.b64decode(event["body_base64"]) == (body or "").encode()
                        else:
                            assert event["actual"] == body
        for manifest in directory.glob("*-downloads.json"):
            for item in read(manifest):
                downloads += 1
                if item["stored_path"]:
                    local = directory / item["stored_path"]
                    assert local.resolve().is_relative_to(directory.resolve())
                    assert sha(local) == item["sha256"] and local.stat().st_size == item["bytes"]
                    stored += 1
        commands = read(directory / "docker-commands.json")
        identities = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
        assert len(identities) == (2 if variant == "before" else 4)
        for identity in identities:
            assert any(c["args"][0] == "ps" and "id=" + identity in c["args"] and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
        absent += identities
    artifacts = [DOCS / "P5_PERFORMANCE_BODY_RESEARCH.md"]
    artifacts += [p for p in DOCS.glob("p5-perf-body-*.*") if p.suffix in {".log", ".xml"}]
    for variant in ("before", "fixed", "verified"):
        artifacts += [p for p in (DOCS / f"p5-perf-body-{variant}").rglob("*") if p.is_file()]
    for path in artifacts:
        assert b"\xe2\x80\x8b" not in path.read_bytes(), path
    sources = [ROOT / "Agent_Server" / name for name in ("src/application/performance/k6_engine_adapter.py",
        "src/application/performance/jmeter_engine_adapter.py", "src/modes/performance_testing_mode/prompt_contract.py",
        "src/registry/tools.py", "tests/test_performance_request_body.py", "tests/test_performance_request_body_live.py")]
    output = {"tests": tests, "actual_downloads_sha_size_checked": downloads, "stored_raw_files_checked": stored,
        "physical_request_count": requests, "containers_absent": absent, "plan_sha256": sha(plans[0]), "plans_equal": True,
        "historical_artifacts_unchanged": sum(len(m["artifact_hashes"]) for m in historical),
        "protected_files_sha256": historical[0]["protected_files_sha256"],
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in artifacts},
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "remaining": "dynamic body/CSV/form, typed complex response, full Agent/App/capacity/security/UI and remaining P0-P5; Step deferred"}
    (DOCS / "p5-perf-body-validation.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("tests", "physical_request_count", "actual_downloads_sha_size_checked", "stored_raw_files_checked", "historical_artifacts_unchanged", "plans_equal")}))


if __name__ == "__main__":
    main()

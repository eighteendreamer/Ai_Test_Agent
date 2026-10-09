"""Verify JMeter evidence without replacing prior audits or credential originals."""
import argparse
import csv
import hashlib
import io
import json
import re
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
    prior = read(DOCS / "p5-perf-correlation-validation.json")
    for relative, expected in prior["artifact_hashes"].items():
        assert sha(ROOT / relative) == expected, relative
    protected = prior.get("protected_files_sha256", {})
    for relative, expected in protected.items():
        assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        old = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode("utf-8").replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]:
            assert path.read_text(encoding="utf-8") in {old, current}, path
        content = current.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
        for path in plans:
            path.write_bytes(content)
    assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
    assert b"\r\r\n" not in plans[0].read_bytes()
    tests = {}
    for suffix, expected in {"contract-final": (78, 3), "contract-delivery": (81, 0), "contract-logged": (81, 0),
        "response-delivery": (1, 0), "measurement-regression": (1, 0), "evidence-regression": (1, 0)}.items():
        suite = ET.parse(DOCS / f"p5-jmeter-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0, suffix
        tests[suffix] = {"passed": passed, "failed": failed, "seconds": float(suite.get("time"))}
    directory = DOCS / "p5-jmeter-response-delivery"
    facts = read(directory / "facts.json")
    observations = facts["observations"]
    assert [o["passed"] for o in observations] == [True, False, True, False, True]
    assert [o["request_count"] for o in observations] == [3, 3, 3, 6, 6]
    downloads = 0
    for variant in ("normal", "wrong-status", "negative", "missing", "chain"):
        for item in read(directory / f"{variant}-downloads.json"):
            original = directory / item["relative_path"]
            assert original.resolve().is_relative_to(directory.resolve())
            assert sha(original) == item["sha256"] and original.stat().st_size == item["bytes"]
            downloads += 1
        jtl = (directory / f"{variant}-originals/result.jtl").read_text(encoding="utf-8")
        assert "qa_corr_" not in jtl.splitlines()[0]
        rows = list(csv.DictReader(io.StringIO(jtl)))
        assert all(row["grpThreads"] == "1" for row in rows)
        if variant in {"missing", "chain"}:
            assert all(row["qa_extract_0"] == ("true" if variant == "chain" else "false") for row in rows)
    commands = read(directory / "docker-commands.json")
    created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
    assert len(created) == 5
    for identity in created:
        assert any(c["args"][0] == "ps" and "id=" + identity in c["args"] and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
    measurement = read(DOCS / "p5-jmeter-measurement-regression/facts.json")
    assert [v["report_verdict"] for v in measurement["variants"]] == ["pass", "pass", "inconclusive", "fail"]
    artifacts = [DOCS / "P5_JMETER_RESPONSE_RESEARCH.md"]
    for pattern in ("p5-jmeter-response-final", "p5-jmeter-response-delivery", "p5-jmeter-measurement-regression", "p5-jmeter-evidence-regression"):
        artifacts += [p for p in (DOCS / pattern).rglob("*") if p.is_file()]
    artifacts += [p for p in DOCS.glob("p5-jmeter-*.xml") if p.is_file()]
    artifacts += [p for p in DOCS.glob("p5-jmeter-*.log") if p.is_file()]
    # Before/after credential-bearing engine originals deliberately excluded.
    artifacts += [DOCS / "p5-jmeter-response-before/observations.json"]
    sources = [ROOT / "Agent_Server" / name for name in (
        "src/application/performance/engine_adapter.py", "src/application/performance/jmeter_engine_adapter.py",
        "src/application/performance/perf_runner_service.py", "src/application/performance/request_validation.py",
        "tests/test_performance_jmeter_adapter.py", "tests/test_performance_jmeter_live.py")]
    historical_credentials = set()
    for old in DOCS.glob("p5-jmeter-response-after*/**/result.jtl"):
        for row in csv.DictReader(io.StringIO(old.read_text(encoding="utf-8"))):
            for key, value in row.items():
                if key and key.startswith("qa_corr_") and re.fullmatch(r"[a-f0-9]{32}", value or ""):
                    historical_credentials.add(value.encode())
    for path in artifacts + sources:
        content = path.read_bytes()
        assert all(value not in content for value in historical_credentials), path
    result = {"scope": facts["scope"], "tests": tests, "download_count": downloads,
        "containers_absent": created, "plans_equal": True, "plan_sha256": sha(plans[0]),
        "historical_artifacts_unchanged": len(prior["artifact_hashes"]),
        "protected_files_sha256": protected,
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in artifacts},
        "remaining": "JMeter load/production report restore, missing-dependency skip, full Agent/App, capacity/stream/security/UI and other P0-P5; Step deferred"}
    (DOCS / "p5-jmeter-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "downloads": downloads, "historical": result["historical_artifacts_unchanged"], "plans_equal": True}))


if __name__ == "__main__":
    main()

"""Audit actual performance measurements and preserve previous evidence."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    historical = {}
    protected = {}
    for name in ("p1-import-full-product-validation.json", "p0-receipt-transport-validation.json",
                 "p0-approval-budget-validation.json", "p0-tool-evidence-validation.json",
                 "plan-receipt-current-validation.json"):
        prior = json.loads((DOCS / name).read_bytes())
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
    assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
    assert b"\r\r\n" not in plans[0].read_bytes()
    assert "### 6.88 " in plans[0].read_text(encoding="utf-8")
    tests = {}
    expected_counts = {"before": (0, 1), "contract-first": (64, 0), "verified": (1, 0),
        "expanded": (96, 0), "delivery": (1, 0), "expanded-delivery": (97, 0),
        "precision-final": (21, 0), "final": (1, 0)}
    for suffix, expected in expected_counts.items():
        suite = ET.parse(DOCS / f"p5-perf-measurement-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed) == expected and skipped == 0
        tests[suffix] = {"passed": passed, "failed": failed, "skipped": skipped, "seconds": float(suite.get("time"))}
    before = DOCS / "p5-perf-measurement-before"
    original = json.loads((before / "default.js-summary.json").read_bytes())
    assert "p(99)" not in original["metrics"]["http_req_duration"]
    assert original["metrics"]["http_reqs"]["count"] == 40
    assert json.loads((before / "default-report.json").read_bytes())["verdict"] == "pass"
    evidence = DOCS / "p5-perf-measurement-final"
    facts = json.loads((evidence / "facts.json").read_bytes())
    assert "no Agent/model invocation" in facts["scope"]
    assert [v["report_verdict"] for v in facts["variants"]] == ["pass", "pass", "inconclusive", "fail"]
    rows = json.loads((evidence / "postgres-metrics.json").read_bytes())
    assert len(rows) == 4
    for name in ("default", "smoke", "missing", "violating"):
        report = json.loads((evidence / f"{name}-report.json").read_bytes())
        run = json.loads((evidence / f"{name}-run.json").read_bytes())
        output = json.loads((evidence / f"{name}-mode-output.json").read_bytes())
        row = next(row for row in rows if row["run_id"] == run["run_id"])
        assert row["verdict"] == output["verdict"] == report["verdict"]
        assert row["metrics_json"] == output["metrics"] == report["metrics"]
        assert report["metrics"]["samples"] > 0
        if name == "missing":
            assert report["metrics"]["p99_ms"] is None
            assert report["sla_result"]["passed"] is None
            assert report["sla_result"]["missing_metrics"] == ["p99_ms"]
            assert "未采集" in report["report_html"]
        else:
            assert report["metrics"]["p99_ms"] > 0
        if name == "violating":
            assert run["exit_code"] != 0 and run["status"] == "failed"
            assert run["engine_thresholds"] == {"http_req_duration:p(99)<0.001": {"ok": False}}
            assert report["sla_result"]["passed"] is False and report["engine_threshold_crosscheck"]["agree"]
    commands = json.loads((evidence / "docker-commands.json").read_bytes())
    created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
    assert len(created) == 5 and all(len(c) == 64 for c in created)
    for container_id in created:
        assert any(c["args"][0] == "ps" and "id=" + container_id in c["args"]
            and c["exit_code"] == 0 and not c["stdout"].strip() for c in commands)
    sources = ["Agent_Server/src/application/performance/" + name for name in
        ("engine_adapter.py", "k6_engine_adapter.py", "jmeter_engine_adapter.py", "perf_runner_service.py")]
    sources += ["Agent_Server/src/modes/performance_testing_mode/" + name for name in
        ("contracts.py", "plan_state.py", "prompt_contract.py", "report_builder.py", "result_parser.py")]
    sources += ["Agent_Server/tests/test_performance_measurement_contract.py", "Agent_Server/tests/test_performance_docker_measurement.py"]
    artifacts = []
    for path in DOCS.glob("p5-perf-measurement-*"):
        if path.is_dir():
            artifacts.extend(p for p in path.rglob("*") if p.is_file())
        elif path.name not in {"p5-perf-measurement-audit.py", "p5-perf-measurement-validation.json"}:
            artifacts.append(path)
    result = {"baseline": "055bf573", "scope": facts["scope"], "tests": tests,
        "full_P0_P5_complete": False, "real_model_acceptance_deferred": True,
        "plans_equal": True, "plan_sha256": sha(plans[0]),
        "historical_artifacts_unchanged": historical, "protected_files_sha256": protected,
        "current_source_hashes": {relative: sha(ROOT / relative) for relative in sources},
        "artifact_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(artifacts)}}
    (DOCS / "p5-perf-measurement-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "artifacts": len(artifacts), "historical": historical, "plans_equal": True}))


if __name__ == "__main__":
    main()

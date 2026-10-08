"""Verify this documentation revision without rewriting historical evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"
OLD_PLAN_SHA = "b858456377fda71ec619a21fde75e6ebca41d85ad951e55ca50fef2434827ac2"
PLANS = [DOCS / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
SOURCES = [
    "Agent_Server/src/application/test_runs/agent_case_receipt.py",
    "Agent_Server/src/application/test_runs/agent_case_execution.py",
    "Agent_Server/src/application/test_runs/agent_case_context.py",
    "Agent_Server/src/application/test_runs/agent_case_assessment.py",
    "Agent_Server/src/schemas/case_execution.py",
    "Agent_Server/src/schemas/case_planning.py", "Agent_Server/src/main.py",
    "Agent_Server/src/application/documents/api_docs_service.py",
    "Agent_Server/src/application/documents/git_service.py",
    "Agent_Server/tests/test_agent_case_receipt_correction.py",
    "Agent_Server/tests/test_agent_case_execution.py",
    "Agent_Server/tests/test_case_step_assessment.py",
    "Agent_Server/tests/test_execution_worker_cli.py",
    "agent_web/src/features/projects/CasePlanDialog.vue",
    "agent_web/src/features/projects/CasePlanDialog.test.ts", "agent_web/src/types.ts",
]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    return digest(path.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true")
    options = parser.parse_args()
    raw = PLANS[0].read_bytes()
    for other in PLANS[1:]:
        previous = other.read_bytes()
        if options.sync:
            assert digest(previous) in {OLD_PLAN_SHA, digest(raw)}, f"Unexpected plan edits: {other}"
            if previous != raw:
                other.write_bytes(raw)
        assert other.read_bytes() == raw, f"Plan mismatch: {other}"
    text = raw.decode("utf-8")
    assert "### 6.85 " in text and "默认关闭" in text and "上传变更可以沿现有实现完成，无需 Git 缓存" in text
    current = text[text.index("#### 2.4.1"):text.index("## 三、")]
    assert "尚未接入" not in current and "阶段尚未实现" not in current
    historical = {}
    protected = {}
    for name in ["p0-approval-budget-validation.json", "p0-tool-evidence-validation.json"]:
        prior = json.loads((DOCS / name).read_bytes())
        for relative, expected in prior["artifact_hashes"].items():
            assert file_hash(DOCS / relative) == expected, f"Historical artifact changed: {relative}"
        historical[name] = len(prior["artifact_hashes"])
        for relative, expected in prior["protected_files_sha256"].items():
            assert file_hash(ROOT / relative) == expected, f"Unrelated file changed: {relative}"
            protected[relative] = expected
    tests = {}
    for name in ["p0-receipt-correction-plan-review", "p0-receipt-correction-plan-contract",
        "p0-receipt-correction-verified", "p0-receipt-correction-default",
        "p0-receipt-correction-production", "p0-receipt-correction-production-fixed"]:
        suite = ET.fromstring((DOCS / (name + ".xml")).read_bytes()).find("testsuite")
        counts = {key: int(suite.attrib.get(key, "0")) for key in ["tests", "failures", "errors", "skipped"]}
        counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
        counts["xml_seconds"] = float(suite.attrib["time"])
        tests[name] = counts
    assert tests["p0-receipt-correction-plan-review"]["passed"] == 21
    assert tests["p0-receipt-correction-plan-review"]["skipped"] == 1
    assert tests["p0-receipt-correction-plan-contract"]["passed"] == 17
    assert tests["p0-receipt-correction-plan-contract"]["failures"] == 0
    assert tests["p0-receipt-correction-default"]["failures"] == 1
    default = json.loads((DOCS / "p0-receipt-correction-default/actual-default-provider.json").read_bytes())
    assert default["correction_calls"] == 1
    assert default["response_raw"]["response_summary"]["mode"] == "http_error"
    assert default["response_raw"]["response_summary"]["error"] == "Request timed out."
    assert default["checkpoint"]["receipt_correction"]["total_model_calls"] == 5
    production = json.loads((DOCS / "p0-receipt-correction-production-fixed/8-receipt/p2-execution-worker-cli.json").read_bytes())
    assert production["formal_status"] == "passed" and len(production["model_calls"]) == 6
    assert production["terminal_replay_model_calls"] == production["terminal_replay_effects"] == 0
    assert len(production["actual_effects"]) == 3 and production["business_records_remaining"] == 0
    passed = json.loads((DOCS / "p0-receipt-correction-plan-review/passed-postgres.json").read_bytes())
    assert len(passed["actual_effects"]) == 3 and passed["jobs"]
    assert passed["correction_calls"] == 1 and passed["checkpoint"]["receipt_correction"]["phase"] == "response"
    artifacts = sorted(path for path in DOCS.rglob("*") if path.is_file()
        and path.relative_to(DOCS).parts[0].startswith("p0-receipt-correction-"))
    # This literal is the controlled test credential, never part of saved evidence.
    assert all(b"DO-NOT-PERSIST-RECEIPT-CREDENTIAL" not in path.read_bytes() for path in artifacts)
    result = {
        "baseline": "be660b34", "scope": "plan, implementation record and verification artifacts only; existing code remains uncommitted",
        "full_P0_P5_complete": False, "default_UI_passed": False,
        "plans_equal": True, "plan_sha256": digest(raw), "plans": [str(path) for path in PLANS],
        "historical_artifacts_unchanged": historical, "protected_files_sha256": protected,
        "tests": tests, "default_provider_failed": "ProviderClientError / Request timed out.",
        "default_provider_physical_HTTP_count_captured": False,
        "production_physical_HTTP_calls": 6, "production_controlled_model": True,
        "current_source_hashes": {relative: file_hash(ROOT / relative) for relative in SOURCES},
        "artifact_hashes": {path.relative_to(ROOT).as_posix(): file_hash(path) for path in artifacts},
    }
    output = DOCS / "plan-receipt-current-validation.json"
    output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"plans_equal": True, "plan_sha256": digest(raw), "historical": historical,
        "current_artifacts": len(artifacts), "current_tests_passed": 38, "current_tests_skipped": 1}, ensure_ascii=False))


if __name__ == "__main__":
    main()

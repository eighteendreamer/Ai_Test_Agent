"""Read-only validation of preserved history and this receipt delivery."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"
SOURCES = [
    "Agent_Server/src/application/test_runs/agent_case_receipt.py",
    "Agent_Server/src/application/test_runs/agent_case_execution.py",
    "Agent_Server/src/application/test_runs/agent_case_context.py",
    "Agent_Server/src/application/test_runs/agent_case_assessment.py",
    "Agent_Server/src/application/prompting/prompt_assembly_service.py",
    "Agent_Server/src/graph/nodes/model_invoker.py", "Agent_Server/src/main.py",
    "Agent_Server/src/schemas/case_execution.py", "Agent_Server/src/schemas/case_planning.py",
    "Agent_Server/tests/test_agent_case_receipt_correction.py",
    "Agent_Server/tests/test_agent_receipt_transport.py",
    "Agent_Server/tests/test_agent_case_execution.py", "Agent_Server/tests/test_case_step_assessment.py",
    "Agent_Server/tests/test_execution_worker_cli.py",
    "agent_web/src/features/projects/CasePlanDialog.vue",
    "agent_web/src/features/projects/CasePlanDialog.test.ts", "agent_web/src/types.ts",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    history, protected = {}, {}
    for name, root in [("p0-approval-budget-validation.json", DOCS),
        ("p0-tool-evidence-validation.json", DOCS), ("plan-receipt-current-validation.json", ROOT)]:
        prior = json.loads((DOCS / name).read_bytes())
        for relative, expected in prior["artifact_hashes"].items():
            assert sha(root / relative) == expected, relative
        history[name] = len(prior["artifact_hashes"])
        for relative, expected in prior.get("protected_files_sha256", {}).items():
            assert sha(ROOT / relative) == expected, relative
            protected[relative] = expected
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
    assert "### 6.86 " in plans[0].read_text(encoding="utf-8")
    tests = {}
    for name, expected in [("p0-receipt-transport-verified", (98, 0, 4)),
        ("p0-receipt-correction-production-current", (3, 0, 0)),
        ("p0-receipt-correction-production-transport", (1, 0, 0)),
        ("p0-receipt-transport-reproduction", (2, 0, 0)),
        ("p0-receipt-correction-default-single", (0, 1, 0))]:
        suite = ET.parse(DOCS / (name + ".xml")).getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed, skipped) == expected
        tests[name] = {"passed": passed, "failed": failed, "skipped": skipped,
            "seconds": float(suite.get("time"))}
    actual = json.loads((DOCS / "p0-receipt-correction-default-single/actual-default-transport.json").read_bytes())
    assert len(actual["physical_attempts"]) == 1
    assert actual["physical_attempts"][0]["status_code"] == 403
    assert actual["sdk_clients"] == [{"max_retries": 0, "timeout_seconds": 60.0}]
    production = json.loads((DOCS / "p0-receipt-correction-production-transport/8-receipt/p2-execution-worker-cli.json").read_bytes())
    assert production["receipt_transport_options"] == {"timeout_seconds": 60, "max_retries": 0}
    assert production["formal_status"] == "passed" and len(production["model_calls"]) == 6
    assert len(production["actual_effects"]) == 3 and production["business_records_remaining"] == 0
    assert production["terminal_replay_model_calls"] == production["terminal_replay_effects"] == 0
    artifacts = sorted(path for path in DOCS.rglob("*") if path.is_file()
        and path.relative_to(DOCS).parts[0].startswith(("p0-receipt-correction-", "p0-receipt-transport-", "p0-receipt-context-"))
        and path.name not in {"p0-receipt-transport-validation.json", "p0-receipt-transport-audit.py"})
    assert all(b"DO-NOT-PERSIST-RECEIPT-CREDENTIAL" not in path.read_bytes() for path in artifacts)
    result = {"baseline": "1904fe9f", "scope": "optional durable receipt correction and frozen transport",
        "full_P0_P5_complete": False, "default_UI_passed": False,
        "default_provider_deferred_by_user": True, "default_observed_HTTP_status": 403,
        "physical_correction_attempts": 1, "tests": tests, "historical_artifacts_unchanged": history,
        "protected_files_sha256": protected, "plans_equal": True, "plan_sha256": sha(plans[0]),
        "production_controlled_model": True, "production_physical_model_calls": 6,
        "current_source_hashes": {relative: sha(ROOT / relative) for relative in SOURCES},
        "artifact_hashes": {path.relative_to(ROOT).as_posix(): sha(path) for path in artifacts}}
    (DOCS / "p0-receipt-transport-validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"historical": history, "artifacts": len(artifacts),
        "plans_equal": True, "tests": tests}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Keep failed full API acceptance separate from a narrow approval probe."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p2-api-approval-wait-validation.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


historical = {}
for name in ("p0-sdk-fault-validation.json", "p2-api-agent-worker-validation.json",
    "p2-api-credential-reference-validation.json", "p2-api-worker-auth-validation.json",
    "p2-receipt-stream-validation.json", "p5-ui-observation-validation.json"):
    values = json.loads((directory / name).read_bytes())["artifact_hashes"]
    assert all(digest(root / relative) == expected for relative, expected in values.items())
    historical[name] = len(values)
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(digest(root / relative) == expected for relative, expected in protected.items())
restored = ["Agent_Server/src/application/sessions/session_service.py",
    "Agent_Server/src/graph/nodes/tool_executor.py", "Agent_Server/src/modes/api_testing_mode/runtime.py",
    "Agent_Server/src/modes/api_testing_mode/subagent_coordinator.py"]
for relative in restored:
    result = subprocess.run(["git", "diff", "--quiet", "--", relative], cwd=root)
    assert result.returncode == 0, relative
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
artifacts = []
for path in sorted(directory.glob("p2-api-approval-wait-*")):
    artifacts.extend(path.rglob("*") if path.is_dir() else [path])
artifacts = [path for path in artifacts if path.is_file() and path != validation and path.suffix != ".py"]
tests = {}
for path in artifacts:
    if path.suffix == ".xml":
        suites = ET.parse(path).getroot().findall("testsuite")
        tests[path.name] = {key: sum(int(suite.get(key, 0)) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")}
full_failure_names = ["before", "observed", "delay-final", "delay-final2", "resume-final",
    "layer1", "layer2", "layer3"]
for name in full_failure_names:
    assert tests[f"p2-api-approval-wait-{name}.xml"] == {
        "tests": 2, "failures": 2, "errors": 0, "skipped": 0}
assert tests["p2-api-approval-wait-probe-final.xml"] == {
    "tests": 2, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p2-api-approval-wait-restored-regression.xml"] == {
    "tests": 68, "failures": 0, "errors": 0, "skipped": 22}
acceptance = (root / "Agent_Server/tests/test_api_agent_worker_live.py").read_text(encoding="utf8")
assert "QA_API_AGENT_APPROVAL_PROBE_ONLY" not in acceptance
assert "assert [e['status'] for e in events] == [200, 200, 200]" in acceptance
assert "reports[0]['output_payload']['report']['completed'] == 3" in acceptance
observations = {}
for scenario in ("in-process", "durable"):
    owned = directory / "p2-api-approval-wait-resume-final" / scenario
    observed = json.loads((owned / "observed.json").read_bytes())
    terminal = json.loads((owned / "target-events-at-cleanup.json").read_bytes())
    data = json.loads((owned / "postgres-finally.json").read_bytes())
    assert not terminal["events"] and not terminal["fixture_errors"] and terminal["external_ai_calls"] == 0
    reports = [job["output_payload"]["report"] for job in data["postgres_tool_job_table"]
        if (job.get("output_payload") or {}).get("report")]
    assert len(reports) == 1 and reports[0]["completed"] == 0 and reports[0]["failed"] == 2
    assert reports[0]["skipped"] == 1
    assert observed["approval_wait_observations"]
    assert all(not item["dispatches_before_approval"] for item in observed["approval_wait_observations"])
    assert any(approval["status"] == "pending" and approval["session_id"] != observed["parent_session_id"]
        for approval in data["postgres_approval_table"])
    observations[scenario] = {"actual_target_http": 0, "saved_report_counts": {
        key: reports[0][key] for key in ("completed", "failed", "skipped")},
        "ordinary_parent_approval_hold_only": True, "child_approvals_still_pending": True}
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, "get_secret_value"):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in path.read_bytes() for path in artifacts)
validation.write_text(json.dumps({"full_goal_complete": False, "external_ai_calls": 0,
    "full_api_acceptance_passed": False, "api_production_fix_delivered": False,
    "narrow_probe_is_full_acceptance": False, "temporary_test_early_return_removed": True,
    "tests": tests, "actual_failures": observations, "restored_production_sources": restored,
    "historical_artifacts_unchanged": historical, "protected_files_unchanged": True,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]), "configured_credentials_present": False,
    "artifact_hashes": {path.relative_to(root).as_posix(): digest(path) for path in artifacts}},
    ensure_ascii=False, indent=2), encoding="utf8")
print(f"Approval evidence audited={len(artifacts)}; full API remains failed; temporary production edits restored")

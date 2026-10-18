"""Audit observed evidence without converting known regression failures to green."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent


def outcome(name):
    suites = ET.parse(root / name).getroot()
    cases = suites.findall(".//testcase")
    failed = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case for case in cases if case.find("skipped") is not None]
    return {"total": len(cases), "passed": len(cases)-len(failed)-len(skipped), "failed": len(failed),
            "skipped": len(skipped)}, failed


before, _ = outcome("p3-api-token-role-before-redacted.xml")
after, _ = outcome("p3-api-token-role-after.xml")
storage, _ = outcome("p3-api-token-role-storage.xml")
final, _ = outcome("p3-api-token-role-final.xml")
regression, failures = outcome("p3-api-token-role-regression.xml")
assert before == {"total": 30, "passed": 9, "failed": 21, "skipped": 0}, before
assert after == {"total": 30, "passed": 30, "failed": 0, "skipped": 0}, after
assert storage == {"total": 2, "passed": 2, "failed": 0, "skipped": 0}, storage
assert final == {"total": 32, "passed": 32, "failed": 0, "skipped": 0}, final
assert regression == {"total": 209, "passed": 196, "failed": 4, "skipped": 9}, regression
assert all(case.attrib["name"].startswith("test_cookie_only_login_rotation_scope_and_terminal_replay") for case in failures)
assert all("assert all(value not in raw" in case.find("failure").text for case in failures)
observations = list((root / "p3-api-token-role-after").glob("*.json"))
assert len(observations) == 16
for path in observations:
    facts = json.loads(path.read_text(encoding="utf8"))
    available = facts["variant"] not in {"refresh-only", "id-only", "custom-missing"}
    assert facts["valid_session"] == available
    assert facts["terminal_extra_http"] == facts["external_ai_calls"] == 0
    assert len(facts["events"]) == 2 and facts["statuses"] == ["completed", "completed"]
    assert facts["events"][-1]["credential_role"] == ("access" if available else "absent")
    assert facts["events"][-1]["status"] == (200 if available else 401)
for variant in ("nested", "refresh-only"):
    facts = json.loads((root / "p3-api-token-role-storage" / f"{variant}-storage.json").read_text(encoding="utf8"))
    assert len(facts["events"]) == len(facts["jobs"]) == 2
    assert all(job["replay_same_job"] for job in facts["jobs"])
    assert facts["terminal_extra_http"] == facts["external_ai_calls"] == 0
    assert facts["encrypted_private_storage"] and facts["public_selected_credentials_redacted"]
baseline = (root / "p3-api-token-role-baseline-cookie-verified.log").read_text(encoding="utf8")
assert "baseline" in baseline and "assert all(value not in raw" in baseline
assert len(list((root / "p3-api-token-role-final").glob("*.json"))) == 18
print(json.dumps({"before": before, "token_role_functional": after, "real_storage": storage,
    "final_delivered_test_version": final, "regression": regression, "functional_HTTP_evidence_files": len(observations),
    "regression_all_green": False, "known_report_cookie_redaction_gap": True, "full_goal_complete": False}, ensure_ascii=False))

"""Audit original worker result recovery separately from full Agent failures."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p2-api-result-readback-validation.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
old_plan = "18b53ed9060ede4b4ae6543f754510252329961ba68a6891574a48674efb066f"
for plan in plans[1:]:
    assert digest(plan) in {old_plan, digest(plans[0])}, "External plan changed; do not overwrite"
for plan in plans[1:]:
    if digest(plan) != digest(plans[0]):
        plan.write_bytes(plans[0].read_bytes())
assert len({digest(plan) for plan in plans}) == 1
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(digest(root / relative) == expected for relative, expected in protected.items())
historical = {}
for name in ("p2-api-agent-worker-validation.json", "p0-sdk-fault-validation.json",
    "p2-api-credential-reference-validation.json", "p2-api-worker-auth-validation.json"):
    hashes = json.loads((directory / name).read_bytes())["artifact_hashes"]
    for relative, expected in hashes.items():
        assert digest(root / relative) == expected, "Historical artifact changed: " + relative
    historical[name] = len(hashes)
prior_import = json.loads((directory / "p1-current2-validation.json").read_bytes())["artifact_hashes"]
for relative, expected in prior_import.items():
    assert digest(directory / "p1-case-import-full-product-current2" / relative) == expected
historical["p1-current2-validation.json"] = len(prior_import)
artifacts = []
for path in sorted(directory.glob("p2-api-result-readback-*")):
    artifacts.extend(path.rglob("*") if path.is_dir() else [path])
artifacts = [path for path in artifacts if path.is_file() and path != validation and path.suffix != ".py"]
tests = {}
for path in artifacts:
    if path.suffix == ".xml":
        suites = ET.parse(path).getroot().findall("testsuite")
        tests[path.relative_to(directory).as_posix()] = {
            key: sum(int(suite.get(key, 0)) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")}
for name, expected in {
    "public-final": (1, 0, 0, 0), "deadline-final": (40, 0, 0, 1),
    "regression": (89, 0, 0, 3), "agent": (4, 2, 0, 0),
    "reproduction": (1, 1, 0, 0), "before": (1, 1, 0, 0),
    "verified": (1, 1, 0, 0), "evidence-final": (1, 1, 0, 0),
}.items():
    assert tuple(tests[f"p2-api-result-readback-{name}.xml"].values()) == expected, name
facts = json.loads((directory / "p2-api-result-readback-public-final/facts.json").read_bytes())
assert facts["response_statuses"] == [200, 200, 200]
assert facts["statuses"] == facts["restored_statuses"] == ["completed"] * 3
assert facts["missing_transcripts"] and facts["foreign_parent_rejected"]
assert facts["restored_extra_http"] == facts["external_ai_calls"] == 0
assert facts["full_api_acceptance"] is False and facts["full_goal_complete"] is False
pg = json.loads((directory / "p2-api-result-readback-public-final/postgres-readback.json").read_bytes())
assert len(pg["jobs"]) == len(pg["children"]) == 3
assert all(child["message_count"] == 0 for child in pg["children"])
assert all(job["status"] == "completed" for job in pg["jobs"])
failures = {}
for scenario in ("in-process", "durable"):
    owned = directory / "p2-api-result-readback-agent" / scenario
    terminal = json.loads((owned / "target-events-at-cleanup.json").read_bytes())
    statuses = [event["status"] for event in terminal["events"]]
    assert statuses == [200] and terminal["external_ai_calls"] == 0 and not terminal["fixture_errors"]
    assert terminal["public_credential_scan_passed"]
    failures[scenario] = {"actual_target_statuses": statuses,
        "physical_model_http": terminal["physical_model_http"], "external_ai_calls": 0}
assert "compileall_exit=0" in (directory / "p2-api-result-readback-compile.log").read_text(encoding="utf8")
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, "get_secret_value"):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in path.read_bytes() for path in artifacts), "Configured credential in evidence"
validation.write_text(json.dumps({"baseline": "cdf51dea", "full_goal_complete": False,
    "external_ai_calls": 0, "full_api_acceptance_passed": False,
    "original_job_readback_verified": True, "durable_parent_wait_implemented": False,
    "tests": tests, "real_storage_http_facts": facts, "independent_agent_failures": failures,
    "historical_artifacts_unchanged": historical, "protected_files_unchanged": True,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]),
    "configured_credentials_present": False,
    "artifact_hashes": {path.relative_to(root).as_posix(): digest(path) for path in artifacts}},
    ensure_ascii=False, indent=2), encoding="utf8")
print(f"Readback evidence audited={len(artifacts)}; full Agent acceptance remains failed; goal active")

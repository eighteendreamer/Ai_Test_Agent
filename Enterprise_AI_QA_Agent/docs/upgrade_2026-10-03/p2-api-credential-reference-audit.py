"""Audit reference scopes, actual report originals and preserved prior failures."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p2-api-credential-reference-validation.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


historical = {}
for name in ("p2-api-worker-auth-validation.json", "p2-receipt-stream-validation.json", "p5-ui-observation-validation.json"):
    saved = json.loads((directory / name).read_bytes())["artifact_hashes"]
    for relative, digest in saved.items():
        assert sha(root / relative) == digest, "Historical artifact changed: " + relative
    historical[name] = len(saved)
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(sha(root / p) == digest for p, digest in protected.items())
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({sha(p) for p in plans}) == 1
paths = []
for p in sorted(directory.glob("p2-api-credential-reference-*")):
    paths.extend(p.rglob("*") if p.is_dir() else [p])
paths = [p for p in paths if p.is_file() and p != validation and p.suffix != ".py"]
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, "get_secret_value"):
    secret = secret.get_secret_value()
for credential in (secret, settings.database.channel_credential_encryption_key):
    if credential:
        assert all(str(credential).encode() not in p.read_bytes() for p in paths), "Configured credential present"
tests = {}
for p in paths:
    if p.suffix == ".xml":
        suites = ET.parse(p).getroot().findall("testsuite")
        tests[p.name] = {k: sum(int(s.get(k, 0)) for s in suites)
            for k in ("tests", "failures", "errors", "skipped")}
assert tests["p2-api-credential-reference-types-final.xml"] == {"tests": 226, "failures": 0, "errors": 0, "skipped": 21}
assert tests["p2-api-credential-reference-case-recovery-verified.xml"] == {"tests": 32, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p2-api-credential-reference-startup.xml"] == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p2-api-credential-reference-report-originals.xml"] == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p2-api-credential-reference-recovery.xml"]["failures"] == 2
verified = {}
for scenario, count in (("core", 3), ("public", 4)):
    p = directory / "p2-api-credential-reference-types-final" / scenario / "verification.json"
    facts = json.loads(p.read_bytes())
    assert len(facts["events"]) == count and all(e["status"] == 200 for e in facts["events"])
    assert next(e for e in facts["events"] if e["path"] == "/profile")["current_credential"]
    if scenario == "public":
        assert not next(e for e in facts["events"] if e["path"] == "/public")["authorization_present"]
    assert facts["terminal_replay_new_http"] == 0 and all(j["replay_same_job"] for j in facts["jobs"])
    assert facts["opaque_worker_prompts"] and facts["generated_credentials_absent_from_public_output"]
    assert facts["fixed_dispatch_conflict_preserves_original"] and facts["parent_state_restored_from_reference"]
    assert {"foreign_job", "changed_job_input", "missing_job", "wrong_key", "missing_key", "parent_session", "project"} <= set(facts["denied_before_http"])
    verified[scenario] = facts
parent_path = directory / "p2-api-credential-reference-report-originals" / "parent" / "verification.json"
parent = json.loads(parent_path.read_bytes())
assert parent["report"]["completed"] == 3 and parent["report"]["failed"] == 0
assert len(parent["events"]) == 3 and parent["terminal_replay_new_http"] == 0
assert {a["type"] for a in parent["artifacts"]} == {"api_test_report_json", "api_test_report_md"}
for artifact in parent["artifacts"]:
    original = parent_path.parent / artifact["saved_filename"]
    assert original.stat().st_size == artifact["size_bytes"] and sha(original) == artifact["sha256"]
    assert artifact["uri"] == "inline://artifact"
assert "compileall_exit=0" in (directory / "p2-api-credential-reference-compile.log").read_text(encoding="utf8")
assert "old_contract_projection_unchanged=True" in (directory / "p2-api-credential-reference-old-contract.log").read_text(encoding="utf8")
result = {"full_goal_complete": False, "external_ai_calls": 0, "independent_agent_worker_verified": False,
    "real_tool_runtime_encrypted_auth_chain_verified": True, "parent_report_storage": "PostgreSQL inline_text",
    "tests": tests, "historical_artifacts_unchanged": historical, "protected_files_unchanged": True,
    "three_plans_equal": True, "plan_sha256": sha(plans[0]), "configured_credentials_present": False,
    "verified_scopes": verified, "parent_originals": parent,
    "artifact_hashes": {p.relative_to(root).as_posix(): sha(p) for p in paths}}
validation.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(f"Reference evidence audited={len(paths)}; previous={historical}; full goal remains incomplete")

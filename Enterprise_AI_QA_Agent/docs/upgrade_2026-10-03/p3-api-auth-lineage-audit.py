"""Audit real two-account HTTP, fixed-plan compatibility and storage evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[2]
directory = Path(__file__).resolve().parent
repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True).strip())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


tests = {}
for name, expected in [
    ("before", dict(tests=4, failures=4, errors=0, skipped=0)),
    ("after", dict(tests=36, failures=0, errors=0, skipped=3)),
    ("final", dict(tests=94, failures=0, errors=0, skipped=5)),
    ("storage", dict(tests=3, failures=0, errors=0, skipped=0)),
]:
    suite = ET.parse(directory / f"p3-api-auth-lineage-{name}.xml").getroot().find("testsuite")
    observed = {key: int(suite.get(key, "0")) for key in expected}
    assert observed == expected, (name, observed)
    tests[name] = observed

for mode in ("inline", "worker"):
    facts = read(directory / "p3-api-auth-lineage-final" / (mode+".json"))
    expected = Counter([(f"/{account}/{operation}", source) for account in ("a", "b")
        for operation, source in [("login", "absent"), ("refresh", account+"-login"), ("profile", account+"-refresh")]]
        + [("/a/profile", "absent")])
    assert Counter((r["path"], r["credential_source"]) for r in facts["requests"]) == expected
    assert all(t["status"] == "completed" and t["attempts"] == 1 for t in facts["tasks"])
    assert facts["tasks"][-1]["response_status"] == 401
    assert facts["report"]["completed"] == 7 and facts["report"]["failed"] == 0
    assert facts["external_ai_calls"] == facts["terminal_extra_http"] == 0
legacy = read(directory / "p3-api-auth-lineage-final/legacy.json")
assert legacy["frozen_dependencies_unchanged"] and legacy["statuses"] == ["completed"]*3
assert [r["credential_source"] for r in legacy["requests"]] == ["absent", "a-login", "a-refresh"]

for name in ("core", "public", "parent"):
    value = read(directory / "p3-api-auth-lineage-storage" / name / "verification.json")
    assert value["terminal_replay_new_http"] == 0 and not value["full_goal_complete"]
    assert all(event["status"] == 200 for event in value["events"])
    if name != "parent":
        for flag in ("new_service_reads_original_sessions", "opaque_worker_prompts",
                     "generated_credentials_absent_from_public_output", "fixed_dispatch_conflict_preserves_original",
                     "parent_state_restored_from_reference"):
            assert value[flag], (name, flag)
        assert value["encrypted_private_rows"] > 0
        assert all(job["replay_same_job"] for job in value["jobs"])
    else:
        assert value["public_state_uses_credential_ref"] and value["report"]["completed"] == 3

# Prior committed process-recovery artifacts must retain their original bytes.
previous = {}
paths = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "3d494671"],
    cwd=root, text=True).splitlines()
for name in paths:
    if "/p3-api-dependency-restore-" not in name:
        continue
    current = (repo / name).read_bytes()
    assert current == subprocess.check_output(["git", "show", f"3d494671:{name}"], cwd=root), name
    previous[name] = hashlib.sha256(current).hexdigest()
assert len(previous) > 100

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
artifacts = [p for p in directory.rglob("p3-api-auth-lineage-*") if p.is_file()
    and p.name != "p3-api-auth-lineage-validation.json"]
for folder in directory.glob("p3-api-auth-lineage-*"):
    if folder.is_dir():
        artifacts.extend(p for p in folder.rglob("*") if p.is_file())
artifacts.extend([directory / "P3_API_AUTH_LINEAGE_RESEARCH.md", root / "Agent_Server/tests/test_api_auth_lineage_http.py"])
validation = {"baseline": "3d494671", "full_goal_complete": False, "external_ai_calls": 0,
    "tests": tests, "default_external_ai_and_independent_api_verified": False,
    "two_accounts_real_inline_and_controlled_worker_http_verified": True,
    "original_single_account_storage_and_fresh_service_read_verified": True,
    "automatic_401_refresh_implemented": False, "legacy_frozen_dependencies_unchanged": True,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]),
    "prior_process_recovery_artifacts_unchanged": previous,
    "artifact_hashes": {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in artifacts}}
(directory / "p3-api-auth-lineage-validation.json").write_text(
    json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf8")
print("Two-account HTTP and legacy storage verified; old recovery originals intact; complete goal active.")

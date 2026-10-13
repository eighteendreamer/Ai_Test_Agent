"""Audit actual Cookie HTTP scopes, immutable payload compatibility and PG read."""
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


expected = {
    "before": (5, 4, 0), "after": (5, 0, 0), "attributes-final": (9, 0, 1),
    "storage": (1, 1, 0), "storage2": (1, 1, 0), "storage3": (1, 1, 0), "storage4": (1, 0, 0),
    "final": (76, 1, 4), "final2": (88, 0, 5), "bearer-reproduce": (1, 0, 0),
    "safety-regression": (105, 0, 0),
}
tests = {}
for name, wanted in expected.items():
    suite = ET.parse(directory / f"p3-api-cookie-{name}.xml").getroot().find("testsuite")
    observed = tuple(int(suite.get(key, "0")) for key in ("tests", "failures", "skipped"))
    assert observed == wanted and int(suite.get("errors", "0")) == 0, (name, observed)
    tests[name] = dict(zip(("tests", "failures", "skipped"), observed))

for mode in ("inline", "worker"):
    for variant in ("direct", "redirect"):
        facts = json.loads((directory / "p3-api-cookie-attributes-final" / f"{mode}-{variant}.json").read_text(encoding="utf8"))
        assert facts["statuses"] == ["completed"]*6
        assert len(facts["events"]) == (7 if variant == "redirect" else 6)
        for path, names in [("/outside", []), ("/api/check", []), ("/api/secure", ["sid"])]:
            assert next(e for e in facts["events"] if e["path"] == path)["cookie_names"] == names
        assert next(e for e in facts["events"] if e["path"] == "/api/refresh")["sid_source"] == "login"
        assert next(e for e in facts["events"] if e["path"] == "/api/profile")["sid_source"] == "refresh"
        assert facts["external_ai_calls"] == facts["terminal_extra_http"] == 0

storage = json.loads((directory / "p3-api-cookie-storage4/verification.json").read_text(encoding="utf8"))
assert storage["statuses"] == ["completed"]*3 and storage["fresh_service_profile_status"] == 200
assert len(storage["events"]) == 5
assert storage["external_ai_calls"] == storage["terminal_extra_http"] == 0
for name in ("cookie_attribute_roundtrip_equal", "opaque_worker_prompts", "encrypted_rows_without_plain_values",
             "public_output_no_plain_cookie_values", "old_payload_idempotent_with_unchanged_hash"):
    assert storage[name]

# All artifacts committed for the preceding auth-lineage batch stay unchanged.
previous = {}
names = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "f6402ec9"],
    cwd=root, text=True).splitlines()
for name in names:
    if "/p3-api-auth-lineage-" not in name:
        continue
    current = (repo / name).read_bytes()
    assert current == subprocess.check_output(["git", "show", f"f6402ec9:{name}"], cwd=root), name
    previous[name] = hashlib.sha256(current).hexdigest()
assert len(previous) > 60

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
artifacts = [p for p in directory.glob("p3-api-cookie-*") if p.is_file() and p.name != "p3-api-cookie-validation.json"]
for folder in directory.glob("p3-api-cookie-*"):
    if folder.is_dir():
        artifacts.extend(p for p in folder.rglob("*") if p.is_file())
artifacts += [directory / "P3_API_COOKIE_RESEARCH.md", root / "Agent_Server/tests/test_api_cookie_session_http.py"]
report = {"baseline": "f6402ec9", "full_goal_complete": False, "tests": tests, "external_ai_calls": 0,
    "actual_cookie_http_and_private_pg_read_verified": True,
    "independent_api_or_process_kill_verified_by_this_batch": False,
    "browser_samesite_partitioned_semantics_implemented": False,
    "automatic_401_or_token_expiry_repair_implemented": False,
    "bearer_transient_failure_not_stably_reproduced": True,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]),
    "previous_auth_artifacts_unchanged": previous,
    "artifact_hashes": {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in artifacts}}
(directory / "p3-api-cookie-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
print("Cookie HTTP and encrypted storage verified; old auth artifacts unchanged; full goal remains active.")

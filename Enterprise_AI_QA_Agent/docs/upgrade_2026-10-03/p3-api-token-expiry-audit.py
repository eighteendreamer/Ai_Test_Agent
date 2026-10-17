"""Check lifetime evidence, retained negative requests and unchanged Cookie originals."""
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


expected = {"before": (11, 11, 0), "after": (11, 0, 0), "functional": (17, 0, 0),
            "storage": (1, 0, 0), "regression": (124, 0, 8)}
tests = {}
for name, wanted in expected.items():
    suite = ET.parse(directory / f"p3-api-token-expiry-{name}.xml").getroot().find("testsuite")
    observed = tuple(int(suite.get(key, "0")) for key in ("tests", "failures", "skipped"))
    assert observed == wanted and int(suite.get("errors", "0")) == 0, (name, observed)
    tests[name] = dict(zip(("tests", "failures", "skipped"), observed))

for batch in ("functional", "regression"):
    for mode in ("inline", "worker"):
        facts = json.loads((directory / f"p3-api-token-expiry-{batch}" / f"{mode}.json").read_text(encoding="utf8"))
        assert facts["statuses"] == ["completed"] * 2 and facts["response_status"] == 401
        assert not facts["available"] and facts["expiry_roundtrip_equal"]
        assert facts["terminal_extra_http"] == facts["external_ai_calls"] == 0
        assert len(facts["requests"]) == 2
        assert facts["requests"][1]["issued_token_used"]  # Invalid-token test must not become missing-token test.
    negative = json.loads((directory / f"p3-api-token-expiry-{batch}" / "negative.json").read_text(encoding="utf8"))
    assert negative["expiry_recorded"] and not negative["available"] and negative["attempts"] == 1
    live = json.loads((directory / f"p3-api-token-expiry-{batch}" / "live.json").read_text(encoding="utf8"))
    assert live["expiry_recorded"] and live["available"] and not live["past_session_available"]

storage = json.loads((directory / "p3-api-token-expiry-storage/storage.json").read_text(encoding="utf8"))
assert len(storage["requests"]) == 2 and storage["negative_status"] == 401 and not storage["restored_available"]
assert all(storage[key] for key in ("expiry_roundtrip_equal", "old_hash_compatible", "encrypted_without_plain_values"))
assert storage["external_ai_calls"] == 0
assert json.loads((directory / "p3-api-token-expiry-compile.json").read_text(encoding="utf8"))["exit_code"] == 0

previous = {}
for name in subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "4fd85832"], cwd=root, text=True).splitlines():
    if "/p3-api-cookie-" not in name:
        continue
    current = (repo / name).read_bytes()
    assert current == subprocess.check_output(["git", "show", f"4fd85832:{name}"], cwd=root), name
    previous[name] = hashlib.sha256(current).hexdigest()
assert len(previous) > 50

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
artifacts = [p for p in directory.glob("p3-api-token-expiry-*") if p.is_file() and p.name != "p3-api-token-expiry-validation.json"]
for folder in directory.glob("p3-api-token-expiry-*"):
    if folder.is_dir():
        artifacts.extend(p for p in folder.rglob("*") if p.is_file())
artifacts += [directory / "P3_API_TOKEN_EXPIRY_RESEARCH.md", root / "Agent_Server/tests/test_api_token_expiry_http.py",
              root / "Agent_Server/src/modes/api_testing_mode/credential_manager.py"]
report = {"baseline": "4fd85832", "full_goal_complete": False, "tests": tests,
    "real_http_and_pg_expiry_roundtrip_verified": True, "negative_401_keeps_original_credential": True,
    "automatic_401_repair_or_independent_process_kill_verified": False, "external_ai_calls": 0,
    "frontend_changed_or_build_run": False, "schema_or_configuration_changed": False,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]), "previous_cookie_originals_unchanged": previous,
    "artifact_hashes": {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in artifacts}}
(directory / "p3-api-token-expiry-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
print("Token expiry and preserved negative intent verified; prior Cookie originals unchanged; full goal remains active.")

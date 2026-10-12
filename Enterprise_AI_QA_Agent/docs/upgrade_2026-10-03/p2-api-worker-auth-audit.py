"""Verify scoped API Worker evidence without treating the live failure as passed."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.channel_config_store import ChannelCredentialCodec
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p2-api-worker-auth-validation.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


history = {}
for name in ("p2-receipt-stream-validation.json", "p5-ui-observation-validation.json"):
    old = json.loads((directory / name).read_bytes())["artifact_hashes"]
    for relative, expected in old.items():
        assert sha(root / relative) == expected, f"Historical evidence changed: {relative}"
    history[name] = len(old)
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(sha(root / relative) == expected for relative, expected in protected.items())

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({sha(p) for p in plans}) == 1
paths = []
for p in sorted(directory.glob("p2-api-worker-auth-*")):
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
assert tests["p2-api-worker-auth-before.xml"] == {"tests": 4, "failures": 2, "errors": 0, "skipped": 0}
assert tests["p2-api-worker-auth-safety-final.xml"] == {"tests": 176, "failures": 0, "errors": 0, "skipped": 1}
assert tests["p2-api-worker-auth-consumers.xml"] == {"tests": 27, "failures": 0, "errors": 0, "skipped": 3}
assert tests["p2-api-worker-auth-storage-redacted.xml"] == {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}

for backend in ("direct", "worker"):
    for scenario in ("refresh", "no-broadcast"):
        facts = json.loads((directory / "p2-api-worker-auth-verified" / f"{backend}-{scenario}.json").read_bytes())
        assert all(t["status"] == "completed" for t in facts["tasks"])
        profile = next(e for e in facts["events"] if e["path"] == "/profile")
        assert profile["current_credential"] and not profile["old_credential"]
        if scenario == "no-broadcast":
            assert not next(e for e in facts["events"] if e["path"] == "/public")["authorization_present"]

boundary = json.loads((directory / "p2-api-worker-auth-storage-redacted" / "observed-boundary.json").read_bytes())
assert [e["status"] for e in boundary["events"]] == [200, 401]
assert boundary["task_statuses"] == ["completed", "failed", "skipped"]
assert all(j["credential_container_redacted"] and not j["credential_token_nonempty"] for j in boundary["jobs"])
compile_log = directory / "p2-api-worker-auth-compile.log"
assert "compileall_exit=0" in compile_log.read_text(encoding="utf8")
result = {"full_goal_complete": False, "live_tool_runtime_worker_auth_passed": False,
    "independent_worker_or_default_ai_verified": False, "external_ai_calls_this_batch": 0,
    "tests": tests, "historical_artifacts_unchanged": history,
    "protected_files_unchanged": True, "three_plans_equal": True, "plan_sha256": sha(plans[0]),
    "configured_credentials_present_in_evidence": False,
    "existing_channel_codec_available": ChannelCredentialCodec(settings.database.channel_credential_encryption_key).available,
    "observed_live_boundary": boundary,
    "artifact_hashes": {p.relative_to(root).as_posix(): sha(p) for p in paths}}
validation.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(f"API Worker artifacts audited={len(paths)}; preserved history={history}; live auth remains failed")

"""Audit actual SDK fault evidence while preserving historical failures."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p0-sdk-fault-validation.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


historical = {}
for name in ("p2-api-agent-worker-validation.json", "p2-api-credential-reference-validation.json",
    "p2-api-worker-auth-validation.json", "p2-receipt-stream-validation.json", "p5-ui-observation-validation.json"):
    artifacts = json.loads((directory / name).read_bytes())["artifact_hashes"]
    for relative, expected in artifacts.items():
        assert digest(root / relative) == expected, "Historical artifact changed: " + relative
    historical[name] = len(artifacts)
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(digest(root / relative) == expected for relative, expected in protected.items())
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
artifacts = []
for path in sorted(directory.glob("p0-sdk-fault-*")):
    artifacts.extend(path.rglob("*") if path.is_dir() else [path])
artifacts = [path for path in artifacts if path.is_file() and path != validation and path.suffix != ".py"]
tests = {}
for path in artifacts:
    if path.suffix == ".xml":
        suites = ET.parse(path).getroot().findall("testsuite")
        tests[path.name] = {key: sum(int(suite.get(key, 0)) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")}
assert tests["p0-sdk-fault-before.xml"] == {"tests": 30, "failures": 6, "errors": 0, "skipped": 0}
assert tests["p0-sdk-fault-verified.xml"] == {"tests": 30, "failures": 2, "errors": 0, "skipped": 0}
assert tests["p0-sdk-fault-expanded.xml"] == {"tests": 61, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p0-sdk-fault-regression.xml"] == {"tests": 155, "failures": 0, "errors": 0, "skipped": 3}
assert tests["p0-sdk-fault-final.xml"] == {"tests": 55, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p0-sdk-fault-protocol-final.xml"] == {"tests": 87, "failures": 0, "errors": 0, "skipped": 0}
assert tests["p0-sdk-fault-regression-final.xml"] == {"tests": 155, "failures": 0, "errors": 0, "skipped": 3}
matrix = []
for path in sorted((directory / "p0-sdk-fault-protocol-final").glob("*/facts.json")):
    facts = json.loads(path.read_bytes())
    requests = list(path.parent.glob("request-*.json"))
    assert len(requests) == facts["physical_requests"]
    assert facts["logical_runtime_invocations"] == 1 and facts["external_ai_calls"] == 0
    assert not facts["fixture_errors"] and facts["escaped_exception_type"] is None
    assert facts["authentication_headers_saved"] is False and facts["sdk_defaults_preserved"]
    if facts["fault"] in {"disconnect", "timeout", "cancel", "forbidden"}:
        assert len(requests) == 1
    for request in requests:
        record = json.loads(request.read_bytes())
        body = record["body"]
        assert "Authorization" not in body and "api_key" not in body
        native = "openai_chat_completions" if facts["transport"] == "langchain_chat" else facts["transport"]
        expected_path = (f"/v1beta/models/fixture-model:{'streamGenerateContent' if facts['streaming'] else 'generateContent'}"
            if native.startswith("google") else {"anthropic_messages": "/v1/messages",
                "openai_responses": "/v1/responses", "openai_chat_completions": "/v1/chat/completions"}[native])
        assert urlsplit(record["path"]).path == expected_path
        if native == "anthropic_messages":
            assert "messages" in body and "input" not in body
    sequences = {json.loads(request.read_bytes())["sequence"] for request in requests}
    replies = facts["response_events"]
    assert len({reply["request_sequence"] for reply in replies}) == len(replies)
    for reply in replies:
        assert reply["request_sequence"] in sequences
        raw = path.parent / f"response-{reply['request_sequence']}.bin"
        assert raw.stat().st_size == reply["size_bytes"] and digest(raw) == reply["sha256"]
    if facts["fault"] == "pre-header-disconnect":
        assert all(reply["request_sequence"] != 1 for reply in replies)
    else:
        assert len(replies) == len(requests)
    matrix.append({key: facts[key] for key in ("transport", "fault", "streaming", "physical_requests")})
assert len(matrix) == 55
assert "compileall_exit=0" in (directory / "p0-sdk-fault-compile.log").read_text(encoding="utf8")
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, "get_secret_value"):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in path.read_bytes() for path in artifacts), "Configured credential in evidence"
validation.write_text(json.dumps({"full_goal_complete": False, "external_ai_calls": 0,
    "real_default_model_quality_verified": False, "full_agent_request_tracing_verified": False,
    "optional_google_aiohttp_backend_verified": False, "independent_api_approval_recovery_verified": False,
    "tests": tests, "controlled_http_matrix": matrix,
    "historical_artifacts_unchanged": historical, "protected_files_unchanged": True,
    "three_plans_equal": True, "plan_sha256": digest(plans[0]),
    "configured_credentials_present": False,
    "artifact_hashes": {path.relative_to(root).as_posix(): digest(path) for path in artifacts}},
    ensure_ascii=False, indent=2), encoding="utf8")
print(f"SDK evidence audited={len(artifacts)}; actual HTTP matrix=55; full goal remains active")

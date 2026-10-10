"""Verify acceptance originals and preserve earlier batches without rerunning their audits."""
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def read(path): return json.loads(path.read_bytes())
def digest(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


counts = {}
for name, tests, failures, skipped in [
    ("http",35,5,0), ("verified",35,0,0), ("regression",272,2,41), ("delivery",275,0,41),
    ("wire-final",39,1,0), ("tools-before",2,1,0), ("wire-delivery",63,0,0),
    ("product",1,1,0), ("product-verified",1,0,0), ("storage",1,1,0),
    ("storage-verified",1,0,0), ("storage-final",1,0,0),
]:
    suite = ET.parse(docs / f"p0-model-request-profile-{name}.xml").getroot().find("testsuite")
    assert tuple(int(suite.attrib[key]) for key in ("tests","failures","skipped","errors")) == (tests, failures, skipped, 0), (name,suite.attrib)
    counts[name] = suite.attrib
assert "compileall_exit=0" in (docs / "p0-model-request-profile-compile-final.log").read_text()
assert "3 passed" in (docs / "p0-model-request-profile-frontend.log").read_text()

wire = docs / "p0-model-request-profile-wire-delivery"
for path in wire.glob("*-sync.json"):
    facts = read(path)
    assert len(facts["physical_requests"]) == 1 and facts["result"]["response_summary"]["mode"] == "ok"
assert len(list(wire.glob("*-sync.json"))) == 14
assert len(list(wire.glob("*-stream.json"))) == 14
for path in wire.glob("*-tools-and-format.json"):
    facts = read(path)
    body = facts["requests"][0]["body"]
    assert len(facts["requests"]) == 1 and facts["result"]["response_summary"]["mode"] == "ok"
    assert body["tools"][0]["function"]["name"] == "inspect" and body["response_format"] == {"type":"json_object"}
assert len(list(wire.glob("*-tools-and-format.json"))) == 4
assert len(read(wire / "http-400.json")["requests"]) == 1
for option in ("output_format","reasoning"):
    facts = read(wire / (option+"-undeclared.json"))
    assert facts["rejected"]["response_summary"]["mode"] == "unsupported_request"
    assert len(facts["requests"]) == 1 and facts["fallback"]["response_summary"]["mode"] == "ok"

product = docs / "p0-model-request-profile-product-verified"
saved = read(product / "restart-and-clear.json")
assert saved["reloaded"]["request_profile"]["revision"] == 3
assert saved["cleared"]["request_profile"] is None and saved["sends_after_rejected_resume"] == 1
assert read(product / "page-errors.json") == []
assert "built in" in (product / "product-build.stdout").read_text()
assert (product / "product-build.stderr").read_bytes() == b""
storage = docs / "p0-model-request-profile-storage-final"
facts = read(storage / "facts.json")
assert facts["guard_additional_sends"] == 0 and facts["actual_sdk_http_calls"] == 1
assert facts["draft"]["case"]["lifecycle_status"] == "draft"
assert facts["draft"]["version"]["content_json"]["expected_status"] == 404
assert len(facts["draft"]["version"]["content_json"]["steps"]) == 8
assert digest(storage / "rustfs-prepared.json")["sha256"] == facts["request_sha256"]
assert digest(storage / "rustfs-response.json")["sha256"] == facts["response_sha256"]

plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
assert b"\r\r\n" not in plans[0].read_bytes()
previous = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json",
    "p5-security-report-validation.json", "p5-perf-report-validation.json", "p5-recording-knowledge-validation.json",
    "p5-recording-overview-validation.json", "p0-model-completion-validation.json", "p0-model-stream-terminal-validation.json"):
    artifacts = read(docs / name)["artifacts"]
    for item in artifacts: assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]
    previous[name] = len(artifacts)
perf = read(docs / "p5-perf-evidence-validation.json")["artifact_hashes"]
for path, expected in perf.items(): assert digest(root / path)["sha256"] == expected
previous["p5-perf-evidence-validation.json"] = len(perf)
protected = read(docs / "p0-model-stream-terminal-validation.json")["protected_files"]
for item in protected: assert digest(root / item["path"])["sha256"] == item["sha256"]

artifacts = sorted({path for item in docs.glob("p0-model-request-profile-*")
    for path in ([item] if item.is_file() else item.rglob("*")) if path.is_file() and path.name != "p0-model-request-profile-validation.json"})
sources = ["Agent_Server/src/schemas/model_request_options.py", "Agent_Server/src/schemas/model_config.py", "Agent_Server/src/schemas/settings.py",
    "Agent_Server/src/application/model_clients/request_mapping.py", "Agent_Server/src/application/model_clients/openai_client.py",
    "Agent_Server/src/application/model_clients/openai_responses_client.py", "Agent_Server/src/application/model_clients/anthropic_client.py",
    "Agent_Server/src/application/model_clients/google_client.py", "Agent_Server/src/application/model_adapters/langchain_model_adapter.py",
    "Agent_Server/src/application/models/model_runtime_service.py", "Agent_Server/src/infrastructure/model_config_store.py",
    "Agent_Server/src/api/routes/settings.py", "Agent_Server/src/application/test_cases/generation_pipeline.py",
    "Agent_Server/src/application/test_cases/planning_workflow.py", "Agent_Server/src/application/documents/impact_calls.py",
    "Agent_Server/src/application/documents/impact_analysis_context.py", "Agent_Server/tests/test_model_request_profile.py",
    "Agent_Server/tests/test_model_request_profile_product.py", "Agent_Server/tests/test_model_request_profile_storage.py",
    "Agent_Server/tests/test_model_failure_lifecycle.py", "agent_web/src/types.ts",
    "agent_web/src/features/settings/plugins/ModelSettingsPlugin.vue", "agent_web/src/locales/en-US.ts",
    "agent_web/src/locales/zh-CN.ts", "agent_web/src/locales/zh-TW.ts", "docs/upgrade_2026-10-03/PLAN.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/P0_MODEL_REQUEST_PROFILE_RESEARCH.md",
    "docs/upgrade_2026-10-03/.gitattributes"]
patterns = [re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"), re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"Authorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._-]{20,}", re.I)]
for path in artifacts+[root/name for name in sources]:
    assert not any(pattern.search(path.read_bytes()) for pattern in patterns), path
result = {"baseline":"06a66a17", "junit":counts, "plans_equal":3, "previous_artifacts_checked":previous,
    "protected_files":protected, "compileall":"exit0", "frontend_tests":3,
    "sources":[digest(root/name) for name in sources], "artifacts":[digest(path) for path in artifacts],
    "full_goal_complete":False, "secret_pattern_file_hits":0,
    "not_proven":["external providers/default Agent full execution", "multimodal/more reasoning/adaptive/level governance",
        "independent invoke_structured/full pilot", "production history and concurrent DDL",
        "manual DeepSeek/recording cleaning omissions/scenes/aliases", "API renewal/perf capacity/AI streaming/other security tools",
        "preparation/unknown effects/revision/other modes", "business requirement quality/all-phase HTTP/old worker originals"]}
(docs / "p0-model-request-profile-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts":len(artifacts),"wire_delivery":counts["wire-delivery"],"plans_equal":3,"previous":previous}))

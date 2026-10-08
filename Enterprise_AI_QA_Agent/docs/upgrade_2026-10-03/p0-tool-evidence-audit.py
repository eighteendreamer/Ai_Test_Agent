"""Audit this batch against stored originals; no full-upgrade completion claim."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Agent_Server"))

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    return json.loads(path.read_bytes())

plans = [EVIDENCE / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
previous = read(EVIDENCE / "p0-recording-agent-validation.json")
for path, digest in previous["protected_files_sha256"].items():
    assert sha((ROOT / path).read_bytes()) == digest, path
for path, digest in previous["artifact_hashes"].items():
    assert sha((EVIDENCE / path).read_bytes()) == digest, path
old_summary = subprocess.check_output(["git", "show",
    "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-regression-planning-worker-cli.json"], cwd=ROOT)
assert (EVIDENCE / "p2-regression-planning-worker-cli.json").read_bytes() == old_summary

tests = {}
for pattern in ("p0-tool-evidence-*.xml", "p0-browser-command-*.xml"):
    for path in sorted(EVIDENCE.glob(pattern)):
        suites = list(ET.parse(path).getroot().iter("testsuite"))
        tests[path.name] = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")}
        tests[path.name]["seconds"] = sum(float(s.attrib.get("time", 0)) for s in suites)
assert [tests["p0-tool-evidence-verified.xml"][k] for k in ("tests", "failures", "errors", "skipped")] == [216, 0, 0, 1]
assert tests["p0-browser-command-malformed.xml"]["failures"] == 0
real = read(EVIDENCE / "p0-browser-command-target-verified/verification.json")
assert real["blocked"]["tool_result"]["status"] == "denied"
assert real["blocked"]["tool_result"]["job_id"] is None
assert real["blocked"]["tool_result"]["output"]["permission_reason_code"] == "untrusted_private_target"
assert real["admitted"]["tool_result"]["status"] == "completed" and real["requests"]
assert real["confirmation"] in real["persisted_job"]["output_payload"]["stdout"]

production = {}
for root in sorted(EVIDENCE.glob("p0-tool-evidence-production*")):
    if not root.is_dir():
        continue
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        sends = read(folder / "model-sends.json")
        for send in sends:
            prefix = folder / f"model-{send['index']:03d}"
            request = prefix.with_name(prefix.name + "-request.bin").read_bytes()
            assert sha(request) == send["request_sha256"]
            assert json.loads(request) == read(prefix.with_name(prefix.name + "-request.json"))
            if "response_sha256" in send:
                response = prefix.with_name(prefix.name + "-response.bin").read_bytes()
                assert sha(response) == send["response_sha256"]
                assert len(response) == send["response_bytes"]
        plan, graph = read(folder / "plan.json"), read(folder / "recording-graph.json")
        knowledge = [row for row in plan["evidence"] if row["kind"] == "recording"]
        assert len(knowledge) == 1 and knowledge[0]["payload"]["graph"] == graph
        assert all(knowledge[0]["id"] in item["evidence_ids"] for item in plan["decision"]["items"] if item["kind"] == "target")
        assert (folder / "recorded.png").read_bytes() == (folder / "recorded-rustfs.png").read_bytes()
        bodies = [read(p) for p in folder.glob("model-*-request.json")]
        executions = [b for b in bodies if "Complete Frozen Case and Preparation Data" in json.dumps(b)]
        assert executions and any(knowledge[0]["source_id"] in json.dumps(b) and "page_response" in json.dumps(b) for b in executions)
        assert not any("DO-NOT-INJECT-TEMPLATE" in json.dumps(b) for b in executions)
        messages = [m for b in bodies for m in b.get("messages", []) if m.get("role") == "tool"]
        for message in messages:
            payload = json.loads(message["content"])
            assert payload["call_id"] == message["tool_call_id"]
            ref = payload["output_reference"]
            if ref is not None:
                assert payload["job_id"] and ref == {"call_id": payload["call_id"], "output_path_tokens": []}
                assert payload["status"] in {"completed", "partial", "failed", "denied"}
        summary = {"physical_sends": len(sends), "send_statuses": [s["status"] for s in sends],
            "page_effects": len(read(folder / "page-effects.json")), "full_recording_frozen": True,
            "screenshot_readback_equal": True, "model_visible_identity_messages": len(messages),
            "failure_preserved": (folder / "failure.json").exists()}
        if (folder / "run-final.json").exists():
            run = read(folder / "run-final.json")
            summary["run_item_status"] = run["items"][0]["status"]
        if (folder / "verification.json").exists():
            summary["verification"] = read(folder / "verification.json")
        production[str(folder.relative_to(EVIDENCE))] = summary

sources = ["docs/upgrade_2026-10-03/.gitattributes",
    "Agent_Server/src/application/runtime/tool_messages.py", "Agent_Server/src/application/runtime/browser_commands.py",
    "Agent_Server/src/application/runtime/runtime_service.py", "Agent_Server/src/application/runtime/python_playwright_cli.py",
    "Agent_Server/src/application/context/mcp_runtime_service.py", "Agent_Server/src/application/security/execution_safety_policy.py",
    "Agent_Server/src/application/test_runs/agent_case_contract.py", "Agent_Server/src/graph/nodes/tool_executor.py",
    "Agent_Server/tests/test_tool_message_evidence.py", "Agent_Server/tests/test_browser_command_target_policy.py",
    "Agent_Server/tests/test_recording_agent_production.py", "Agent_Server/tests/test_intent_safety_routing.py"]
files = []
for pattern in ("p0-tool-evidence-*", "p0-browser-command-*"):
    for path in EVIDENCE.glob(pattern):
        files.extend([path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()])
files = sorted(p for p in files if p.name != "p0-tool-evidence-validation.json")
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
model = MySQLModelConfigStore(get_settings()).get_default_active()
if model.api_key:
    assert not any(model.api_key.encode() in p.read_bytes() for p in files), "credential in evidence"

result = {"baseline": "f2ba964e", "previous_goal_turn": "progress", "full_P0_P5_complete": False,
    "plans_sha256": sha(plans[0].read_bytes()), "tests": tests, "production": production,
    "real_browser_graph_admission_verified": True, "configured_provider_credential_not_in_evidence": True,
    "historical_artifacts_unchanged": len(previous["artifact_hashes"]),
    "protected_files_sha256": previous["protected_files_sha256"],
    "source_hashes": {p: sha((ROOT / p).read_bytes()) for p in sources},
    "artifact_hashes": {str(p.relative_to(EVIDENCE)): sha(p.read_bytes()) for p in files}}
(EVIDENCE / "p0-tool-evidence-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(files), "sources": len(sources), "tests": tests,
    "production": {k: {f: v[f] for f in ["physical_sends", "page_effects", "run_item_status"] if f in v} for k, v in production.items()},
    "plans_sha256": result["plans_sha256"]}))

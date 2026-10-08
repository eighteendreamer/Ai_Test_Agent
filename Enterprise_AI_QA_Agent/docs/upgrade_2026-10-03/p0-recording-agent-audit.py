"""Check this batch's originals; never treat a passed subset as full completion."""
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


def read(name):
    return json.loads((EVIDENCE / name).read_bytes())


plans = [EVIDENCE / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
protected = {".gitignore": "d604cb2eb1e5da2b92ec113f2e14d05889963ae0fe9492760670f3a1c5c8b241",
    "docs/upgrade_2026-10-03/p2-planning-worker-cli.json": "e7b5158ba537b6036f4500218ca88135e355612d7d3037ecfdbd7d01137e1fe8",
    "docs/upgrade_2026-10-03/p2-planning-worker-probe.json": "a555eb443ba5943d63433b959e61cc0286edf69798c415e471da057beec4ab10"}
for path, digest in protected.items():
    assert sha((ROOT / path).read_bytes()) == digest, path
old_summary = subprocess.check_output(["git", "show",
    "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-regression-planning-worker-cli.json"], cwd=ROOT)
assert (EVIDENCE / "p2-regression-planning-worker-cli.json").read_bytes() == old_summary

tests = {}
for path in sorted(EVIDENCE.glob("p0-recording-agent-*.xml")):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    tests[path.name] = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")}
    tests[path.name]["seconds"] = sum(float(s.attrib.get("time", 0)) for s in suites)
final = tests["p0-recording-agent-regression-final.xml"]
assert [final[k] for k in ("tests", "failures", "errors", "skipped")] == [173, 0, 0, 1]
assert tests["p0-recording-agent-owned-production.xml"]["failures"] == 1

production = {}
for folder in sorted(EVIDENCE.glob("p0-recording-agent-*-production")):
    sends = read(folder.name + "/model-sends.json")
    verified = []
    for send in sends:
        index = send["index"]
        request_raw = (folder / f"model-{index:03d}-request.bin").read_bytes()
        assert sha(request_raw) == send["request_sha256"]
        assert json.loads(request_raw) == json.loads((folder / f"model-{index:03d}-request.json").read_bytes())
        response_path = folder / f"model-{index:03d}-response.bin"
        if "response_sha256" in send:
            response_raw = response_path.read_bytes()
            assert sha(response_raw) == send["response_sha256"]
            assert len(response_raw) == send["response_bytes"]
            assert b"data: [DONE]" in response_raw
        verified.append({"index": index, "status": send["status"],
            "request_sha256": sha(request_raw), "response_original_available": response_path.exists()})
    plan = read(folder.name + "/plan.json")
    graph = read(folder.name + "/recording-graph.json")
    recording = read(folder.name + "/recording.json")
    knowledge = [entry for entry in plan["evidence"] if entry["kind"] == "recording"]
    assert len(knowledge) == 1 and knowledge[0]["payload"]["graph"] == graph
    assert knowledge[0]["source_id"] == recording["id"]
    assert all(knowledge[0]["id"] in item["evidence_ids"] for item in plan["decision"]["items"] if item["kind"] == "target")
    assert (folder / "recorded.png").read_bytes() == (folder / "recorded-rustfs.png").read_bytes()
    execution_bodies = [json.loads(p.read_bytes()) for p in folder.glob("model-*-request.json")
        if "Complete Frozen Case and Preparation Data" in p.read_text(encoding="utf-8")]
    assert execution_bodies
    assert any(recording["id"] in json.dumps(body) and "page_response" in json.dumps(body) for body in execution_bodies)
    assert not any("DO-NOT-INJECT-TEMPLATE" in json.dumps(body) for body in execution_bodies)
    production[folder.name] = {"physical_sends": verified, "full_recording_reference": True,
        "screenshot_readback_equal": True, "full_execution_request_available": True}
owned = EVIDENCE / "p0-recording-agent-owned-production"
run = read(owned.name + "/run-final.json")
effects = read(owned.name + "/page-effects.json")
assert len(effects) == 1 and run["items"][0]["status"] == "error"
assert len(production[owned.name]["physical_sends"]) == 7
assert all(item["status"] == 200 for item in production[owned.name]["physical_sends"])
snapshots = read(owned.name + "/postgres-execution.json")["tables"]["postgres_snapshot_table"]["records"]
final_snapshot = next(row for row in snapshots if row["stage"] == "completed")
denied = [record for record in final_snapshot["graph_state"]["tool_results"]
    if record["status"] == "denied" and record["output"].get("permission_reason_code") == "untrusted_private_target"]
assert {record["tool_key"] for record in denied} == {"ui-automation-runner", "browser-control", "browser-automation"}

sources = ["Agent_Server/src/application/test_cases/planning_required_sources.py",
    "Agent_Server/src/application/test_cases/planning_service.py",
    "Agent_Server/src/application/test_cases/planning_workflow.py",
    "Agent_Server/src/application/test_runs/agent_regression_service.py",
    "Agent_Server/src/schemas/case_planning.py", "Agent_Server/src/schemas/case_planning_task.py",
    "agent_web/src/types.ts", "Agent_Server/tests/test_planning_required_sources.py",
    "Agent_Server/tests/test_recording_agent_production.py", "Agent_Server/tests/test_recording_agent_observer.py",
    "Agent_Server/tests/test_regression_planning_tasks.py", "Agent_Server/tests/test_regression_planning_worker.py"]
files = [p for p in EVIDENCE.glob("p0-recording-agent-*") if p.is_file()]
files += [p for folder in EVIDENCE.glob("p0-recording-agent-*") if folder.is_dir() for p in folder.rglob("*") if p.is_file()]
files = [p for p in files if p.name != "p0-recording-agent-validation.json"]
# Existing public model serialization excludes secrets. Check the actual
# configured provider credential by value without putting it into any output.
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
model = MySQLModelConfigStore(get_settings()).get_default_active()
if model.api_key:
    assert not any(model.api_key.encode() in p.read_bytes() for p in files), "provider credential in evidence"

result = {"baseline": "d9702edd", "previous_goal_turn": "progress", "full_P0_P5_complete": False,
    "plans_sha256": sha(plans[0].read_bytes()), "protected_files_sha256": protected,
    "historical_regression_worker_summary_restored_HEAD_sha256": sha(old_summary), "tests": tests,
    "production": production, "default_ui_execution": {"status": "error", "recording_effects": 1,
        "execution_effects": 0, "passed_terminal_replay_verified": False,
        "limitations": ["local target denied by existing policy", "receipt referenced nonexistent call IDs",
            "provider internal sends unknown", "human DeepSeek and full App not tested"]},
    "source_hashes": {path: sha((ROOT / path).read_bytes()) for path in sources},
    "artifact_hashes": {str(p.relative_to(EVIDENCE)): sha(p.read_bytes()) for p in sorted(files)},
    "configured_provider_credential_not_in_evidence": True}
(EVIDENCE / "p0-recording-agent-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"files": len(files), "source_files": len(sources), "final_regression": final,
    "default_UI_execution_passed": False, "plans_sha256": result["plans_sha256"]}))

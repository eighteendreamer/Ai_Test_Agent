"""Audit preserved failures, actual PG/HTTP budgets and synchronized plans."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Agent_Server"))


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


plans = [DOCS / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
previous = read(DOCS / "p0-tool-evidence-validation.json")
for path, digest in previous["artifact_hashes"].items():
    assert sha(DOCS / path) == digest, path
for path, digest in previous["protected_files_sha256"].items():
    assert sha(ROOT / path) == digest, path
assert (DOCS / "p2-regression-planning-worker-cli.json").read_bytes() == subprocess.check_output(
    ["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-regression-planning-worker-cli.json"], cwd=ROOT)

tests = {}
for path in sorted(DOCS.glob("p0-approval-budget-*.xml")):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    tests[path.name] = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")}
    tests[path.name]["seconds"] = sum(float(s.attrib.get("time", 0)) for s in suites)
for name in ["batch-fixed", "batch-final", "production-final", "regression-verified"]:
    summary = tests[f"p0-approval-budget-{name}.xml"]
    assert summary["tests"] > 0 and summary["failures"] == summary["errors"] == 0, name
assert tests["p0-approval-budget-production-final.xml"]["tests"] == 2
assert tests["p0-approval-budget-batch-before.xml"]["failures"] == 2

for name in ("memory", "postgres"):
    failed = read(DOCS / f"p0-approval-budget-batch-before/batch-first-{name}.json")
    assert failed["calls"] == 1 and len(failed["effects"]) == 1
    states = failed["snapshots"]
    assert states[0]["stage"] == "waiting_approval" and len(states[0]["graph_state"]["pending_approvals"]) == 2
    assert states[-1]["stage"] == "resumable"
    assert not states[-1]["graph_state"]["pending_approvals"]
    assert len(states[-1]["graph_state"]["pending_turn"]["pending_approval_ids"]) == 1
    fixed = read(DOCS / f"p0-approval-budget-batch-final/batch-first-{name}.json")
    assert fixed["snapshots"][-1]["stage"] == "waiting_approval"
    assert len(fixed["snapshots"][-1]["graph_state"]["pending_approvals"]) == 1
    assert fixed["calls"] == 1
    passed = read(DOCS / f"p0-approval-budget-batch-final/batch-{name}.json")
    assert passed["calls"] == passed["snapshot"]["model_call_count"] == passed["snapshot"]["max_iterations"] == 3
    assert passed["completion"]["status"] == "passed"
    assert passed["terminal_replay_model_calls"] == passed["terminal_replay_effects"] == 0
    context = read(DOCS / f"p0-approval-budget-regression-verified/context-{name}.json")
    assert context["controlled_provider_usage"] and context["calls"] == 1
    assert context["snapshot"]["termination_reason"] == "context_budget_exhausted"
    assert context["snapshot"]["turn_token_usage"]["prompt_tokens"] == 950
    assert context["snapshot"]["model_context_window"] == 1000

production = {}
for limit in [3, 8]:
    folder = DOCS / "p0-approval-budget-production-final" / str(limit)
    result = read(folder / "p2-execution-worker-cli.json")
    probe = folder / "p2-execution-worker-cli-probe"
    tables = read(probe / "postgres-execution.json")
    snapshots = tables["postgres_snapshot_table"]
    final = [row for row in snapshots if row["stage"] == "completed"]
    assert len(final) == 1
    state = final[0]["graph_state"]
    assert state["model_call_count"] == min(limit, 4) and state["max_iterations"] == limit
    assert result["original_turn_limit"] == limit and result["restarted_process_default"] == limit + 10
    assert result["terminal_replay_model_calls"] == result["terminal_replay_effects"] == 0
    assert result["formal_status"] == ("error" if limit == 3 else "passed")
    assert [row[0] for row in result["actual_effects"]] == ["POST", "GET", "DELETE"]
    assert not result["business_records_remaining"]
    http = read(probe / "http.json")
    assert not http["errors"] and len(http["calls"]) == 1 + min(limit, 4)
    requests = [read(p) for p in sorted(probe.glob("model-*-request.json"))]
    assert len(requests) == len(http["calls"])
    executions = [r for r in requests if "Complete Frozen Case and Preparation Data" in json.dumps(r)]
    assert len(executions) == min(limit, 4)
    assert not any("DO-NOT-INJECT-TEMPLATE" in json.dumps(r) for r in requests)
    for snapshot in snapshots:
        st = snapshot["graph_state"]
        assert st["max_iterations"] == limit and st["model_call_count"] <= limit
        assert st["pending_approvals"] or snapshot["stage"] == "completed"
        assert all(str(row["id"]) in st["pending_turn"]["pending_approval_ids"] for row in st["pending_approvals"])
    production[str(limit)] = {"formal_status": result["formal_status"], "original_limit": limit,
        "restarted_default": limit + 10, "logical_execution_calls": state["model_call_count"],
        "physical_http_sends": len(http["calls"]), "actual_effects": len(http["effects"]),
        "terminal_replay_model_calls": 0, "terminal_replay_effects": 0}

web = (DOCS / "p0-approval-budget-web-verified.log").read_text(encoding="utf-8")
assert "4 passed" in web
build = (DOCS / "p0-approval-budget-web-build-verified.log").read_text(encoding="utf-8")
assert "built in" in build and "error TS" not in build
assert "error TS" in (DOCS / "p0-approval-budget-web-build.log").read_text(encoding="utf-8")

sources = ["Agent_Server/src/application/runtime/" + name for name in
    ["agent_loop.py", "runtime_service.py", "state_persistence.py", "state_types.py"]]
sources += ["Agent_Server/src/graph/nodes/" + name for name in ["finalizer.py", "model_invoker.py", "tool_executor.py"]]
sources += ["Agent_Server/src/graph/state.py", "Agent_Server/tests/test_approval_model_budget.py",
    "Agent_Server/tests/test_turn_budget_persistence.py", "Agent_Server/tests/test_execution_worker_cli.py",
    "agent_web/src/components/chat/SnapshotTracePanel.vue", "agent_web/tests/SnapshotTracePanel.test.ts",
    "agent_web/src/locales/en-US.ts", "agent_web/src/locales/zh-CN.ts", "docs/upgrade_2026-10-03/.gitattributes"]
files = sorted(f for p in DOCS.glob("p0-approval-budget-*")
    for f in ([p] if p.is_file() else p.rglob("*")) if f.is_file() and f.name != "p0-approval-budget-validation.json")
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
model = MySQLModelConfigStore(get_settings()).get_default_active()
if model.api_key:
    assert not any(model.api_key.encode() in p.read_bytes() for p in files), "credential in evidence"

report = {"baseline": "fc7f94bd", "full_P0_P5_complete": False, "plans_equal": True,
    "plan_sha256": sha(plans[0]), "historical_artifacts_unchanged": len(previous["artifact_hashes"]),
    "protected_files_sha256": previous["protected_files_sha256"], "tests": tests, "production": production,
    "web_tests_passed": 4, "web_build_passed": True, "configured_provider_credential_not_in_evidence": True,
    "scope": "actual HTTP/PG and isolated production API/Worker; controlled models, no new default UI pass",
    "source_hashes": {p: sha(ROOT / p) for p in sources},
    "artifact_hashes": {str(p.relative_to(DOCS)): sha(p) for p in files}}
(DOCS / "p0-approval-budget-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"files": len(files), "tests": tests, "production": production, "plan_sha256": report["plan_sha256"]}))

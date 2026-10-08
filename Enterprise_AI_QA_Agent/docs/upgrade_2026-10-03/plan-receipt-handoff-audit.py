"""Read-only reproduction of stored receipts and synchronized plan revision."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Agent_Server"))

from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.literal_evidence import literal_value
from src.schemas.model_config import ModelInvocationResult


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


plans = [DOCS / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
previous = read(DOCS / "p0-tool-evidence-validation.json")
for relative, digest in previous["artifact_hashes"].items():
    assert sha(DOCS / relative) == digest, relative
for relative, digest in previous["protected_files_sha256"].items():
    assert sha(ROOT / relative) == digest, relative

report = {"code_baseline": "e7610fc4", "production_code_changed": False,
    "new_live_provider_execution": False, "plans_equal": True, "plan_sha256": sha(plans[0]),
    "historical_artifacts_unchanged": len(previous["artifact_hashes"]), "scenarios": {}}
for name in ["trusted-parent", "untrusted-target"]:
    folder = DOCS / "p0-tool-evidence-production-final" / name
    tables = read(folder / "postgres-execution.json")["tables"]
    state = tables["postgres_snapshot_table"]["records"][-1]["graph_state"]
    response = ModelInvocationResult(text=state["model_response_text"],
        response_summary=state["model_response_summary"])
    run = read(folder / "run-final.json")
    sends = read(folder / "model-sends.json")
    effects = read(folder / "page-effects.json")
    execution_requests = [path for path in folder.glob("model-*-request.json")
        if "Complete Frozen Case and Preparation Data" in path.read_text(encoding="utf-8")]
    summary = {"physical_sends": len(sends), "execution_requests": len(execution_requests),
        "page_effects": len(effects), "formal_status": run["items"][0]["status"],
        "snapshot_loop_iteration": state["loop_iteration"], "snapshot_max_iterations": state["max_iterations"]}
    if name == "trusted-parent":
        try:
            AgentCaseAssessmentService.parse_response(response)
        except ValueError as exc:
            assert "line=1, column=1" in str(exc), str(exc)
            summary["reproduced_parse_error"] = str(exc)
        else:
            raise AssertionError("Captured mixed report must remain rejected")
        assert len(sends) == 22 and len(execution_requests) == 20 and len(effects) == 4
        assert summary["formal_status"] == "error"
        job = next(row for row in tables["postgres_tool_job_table"]["records"]
            if row["call_id"] == "chatcmpl-tool-8abc21c9897a75fe")
        uri = literal_value(job["output_payload"], ["artifacts", "0", "uri"])
        assert uri.startswith("rustfs://") and uri.endswith("screenshot.png")
        summary["screenshot_literal_path_exists"] = True
        summary["cumulative_execution_requests_exceed_snapshot_limit"] = len(execution_requests) > state["max_iterations"]
    else:
        receipt = AgentCaseAssessmentService.parse_response(response)
        assert receipt.steps[0].status == "blocked" and receipt.steps[0].checks == []
        assert len(sends) == 4 and len(effects) == 1 and summary["formal_status"] == "blocked"
        verified = read(folder / "verification.json")
        assert verified["terminal_replay_sends"] == verified["terminal_replay_effects"] == 0
        summary["original_terminal_replay_sends"] = 0
        summary["original_terminal_replay_effects"] = 0
    report["scenarios"][name] = summary
report["scope"] = "stored real HTTP/PG/page evidence and current strict parser; no new product acceptance"
(DOCS / "plan-receipt-handoff-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))

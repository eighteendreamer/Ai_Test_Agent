"""Replay the captured default-model failure and verify actual stored evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_messages import build_tool_message
from src.graph.nodes.tool_executor import apply_tool_message_budget
from src.schemas.tool_runtime import ToolExecutionRecord
from test_agent_case_assessment import execution, assess, business_http
from test_case_plan_run_creation import LIVE


def failed_state():
    source = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-recording-agent-owned-production/postgres-execution.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    return data["tables"]["postgres_snapshot_table"]["records"][-1]["graph_state"]


def test_actual_failed_trajectory_exposes_only_settled_job_references_on_both_paths():
    state = failed_state()
    records = [ToolExecutionRecord.model_validate(row) for row in state["tool_results"]]
    assert len(records) == 5 and sum(record.job_id is None for record in records) == 3
    for record in records:
        message = build_tool_message(record)
        assert RuntimeService._build_tool_message(None, record) == message
        payload = json.loads(message["content"])
        assert payload["call_id"] == message["tool_call_id"] == record.call_id
        assert payload["job_id"] == record.job_id
        assert payload["instruction_authority"] == "none"
        expected = {"call_id": record.call_id, "output_path_tokens": []} if record.job_id else None
        assert payload["output_reference"] == expected


@pytest.mark.parametrize("status", ["waiting_approval", "waiting_resource"])
def test_unfinished_job_identity_is_visible_but_not_citable(status):
    record = ToolExecutionRecord(call_id="actual-pending", job_id="actual-job", tool_key="demo",
        tool_name="Demo", status=status, summary="not finished", output={})
    payload = json.loads(build_tool_message(record)["content"])
    assert payload["job_id"] == record.job_id and payload["output_reference"] is None


@pytest.mark.parametrize("job_id", [None, ""])
def test_diagnostic_denial_without_job_is_not_citable(job_id):
    record = ToolExecutionRecord(call_id="actual-denial", job_id=job_id, tool_key="demo",
        tool_name="Demo", status="denied", summary="not executed", output={"error": "denied"})
    payload = json.loads(build_tool_message(record)["content"])
    assert payload["output_reference"] is None and payload["output"] == record.output


def test_budget_preserves_identity_and_valid_json_for_broad_output_and_long_summary():
    record = ToolExecutionRecord.model_validate(failed_state()["tool_results"][1])
    record.summary = "s" * 50000
    record.output = {f"branch-{i}": {f"field-{j}": "x" * 5000 for j in range(40)} for i in range(40)}
    before = deepcopy(record.output)
    message = build_tool_message(record)
    reduced = apply_tool_message_budget(message, 4000)
    payload = json.loads(reduced["content"])
    assert len(reduced["content"]) <= 4000
    assert payload["truncated"] and payload["output_digest"].startswith("...(output omitted")
    assert payload["output_reference"] == {"call_id": record.call_id, "output_path_tokens": []}
    assert payload["call_id"] == record.call_id and payload["job_id"] == record.job_id
    assert payload["content_provenance"] == "tool_output" and payload["instruction_authority"] == "none"
    assert record.output == before


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_message_reference_resolves_real_http_output_from_persisted_job(tmp_path, business_http, live):
    async with execution(tmp_path, business_http, live) as env:
        context, jobs, records, session, turn, attempt, *_ = env
        from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
        manifest = await AgentCaseAssessmentService(jobs).evidence_manifest(context=context,
            tool_records=records, session_id=session, turn_id=turn, attempt_id=attempt)
        for record in records:
            payload = json.loads(build_tool_message(record)["content"])
            persisted = next(row for row in manifest if row["call_id"] == record.call_id)
            assert payload["output_reference"] == persisted["output_reference"]
            assert payload["job_id"] == persisted["job_id"]
            assert payload["output"]["task_result"] == persisted["output"]["task_result"]
        assert (await assess(env)).completion.status == "passed"


@pytest.mark.asyncio
async def test_captured_fake_receipt_still_rejected_and_unexecuted_checks_need_no_fake_evidence(tmp_path, business_http):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.schemas.model_config import ModelInvocationResult
    state = failed_state()
    captured = AgentCaseAssessmentService.parse_response(ModelInvocationResult(
        text=state["final_response"], response_summary={"mode": "ok"}))
    assert captured.steps[0].status == "blocked"
    async with execution(tmp_path, business_http, perform_tools=False) as env:
        # Preserve the actual faulty receipt and fixed expected source; only
        # rebind its item identity to the newly owned test run.
        data = {"schema_version": 1, "steps": [
            {"item_key": "seed", "status": "blocked", "summary": "未执行", "checks": []},
            {**captured.steps[0].model_dump(mode="json"), "item_key": "target"},
            {"item_key": "cleanup", "status": "blocked", "summary": "未执行", "checks": []},
        ]}
        env[0].steps[1].content_json = deepcopy(state["context_bundle"]["case_execution"]["steps"][0]["content_json"])
        with pytest.raises(ValueError, match="不存在的工具调用"):
            await assess(env, data)
        data["steps"][1]["checks"] = []
        outcome = await assess(env, data)
        assert outcome.completion.status == "blocked"
        assert all(row.status.value == "not_run" for row in outcome.verification_results)
        assert not any(row.evidence_type == "tool_job" for row in outcome.completion.evidence_refs)

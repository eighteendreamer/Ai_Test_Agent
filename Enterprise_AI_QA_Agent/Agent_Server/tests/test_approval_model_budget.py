"""Actual HTTP/PG approval continuation must share the initial model limit."""
import json
import os
from pathlib import Path

import pytest

from test_agent_case_assessment import business_http
from test_agent_case_execution import adapter_environment, approval_continuation
from test_case_plan_run_creation import LIVE
from src.application.test_runs.agent_case_execution import AgentCaseApprovalWait
from test_agent_case_session import ControlledAgent
from src.schemas.model_config import ModelConfigRecord
from src.schemas.tool_runtime import ModelToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_approval_calls_stop_at_original_limit_without_repeating_effects(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, writer, reader, sessions, runs, agent, adapter, args, _, _ = values
        sessions._runtime_service._max_iterations = 3
        pending = await adapter().execute(**await args())
        assert isinstance(pending, AgentCaseApprovalWait)
        # A later process/config default must not donate a larger budget.
        sessions._runtime_service._max_iterations = 99
        for count in range(2):
            pending = await approval_continuation(runs, reader, adapter, args, pending)
            assert isinstance(pending, AgentCaseApprovalWait)
            assert len(agent.requests) == count + 2
            assert len(business_http[1]) == count + 1
        with pytest.raises(ValueError, match="budget_exhausted"):
            try:
                await approval_continuation(runs, reader, adapter, args, pending)
            finally:
                if os.getenv("QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"):
                    root = Path(os.environ["QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"])
                    root.mkdir(parents=True, exist_ok=True)
                    original = await reader.list_snapshots(pending.session_id, include_graph_state=True)
                    (root / ("postgres.json" if live else "memory.json")).write_text(json.dumps({
                        "calls": len(agent.requests), "effects": business_http[1],
                        "snapshots": [row.model_dump(mode="json") for row in original],
                    }, ensure_ascii=False, default=str), encoding="utf-8")
        assert len(agent.requests) == 3
        assert [method for method, _ in business_http[1]] == ["POST", "GET", "DELETE"]
        snapshot = await reader.get_latest_snapshot(pending.session_id)
        state = snapshot.graph_state
        assert state["model_call_count"] == state["max_iterations"] == 3
        assert state["termination_reason"] == "max_iterations"
        assert all(record["status"] == "completed" for record in state["tool_results"])
        before = len(agent.requests), list(business_http[1])
        with pytest.raises(ValueError, match="budget_exhausted"):
            await adapter().execute(**await args())
        assert (len(agent.requests), business_http[1]) == before
        (tmp_path / "approval-model-budget.json").write_text(json.dumps({
            "real_postgres": live, "calls": len(agent.requests), "effects": business_http[1],
            "snapshot": state, "replay_extra_calls": 0, "replay_extra_effects": 0,
        }, ensure_ascii=False, default=str), encoding="utf-8")
        if os.getenv("QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"):
            root = Path(os.environ["QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"])
            root.mkdir(parents=True, exist_ok=True)
            (root / ("postgres.json" if live else "memory.json")).write_bytes(
                (tmp_path / "approval-model-budget.json").read_bytes())


class ContextLimitedAgent(ControlledAgent):
    def get_default_model_config(self):
        return self.get_model_config("controlled-case-agent")

    def get_model_config(self, key):
        return ModelConfigRecord(key=key, name="Controlled context metadata", provider="test",
            transport="openai_chat_completions", model_id="controlled", api_base_url="http://unused",
            context_window=1000)

    async def invoke(self, key, request):
        response = await super().invoke(key, request)
        response.response_summary["usage"] = {"prompt_tokens": 950, "completion_tokens": 10, "total_tokens": 960}
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_approval_retains_provider_context_budget_and_stops_before_next_model(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live, agent=ContextLimitedAgent()) as values:
        _, writer, reader, sessions, runs, agent, adapter, args, _, _ = values
        pending = await adapter().execute(**await args())
        assert isinstance(pending, AgentCaseApprovalWait) and len(agent.requests) == 1
        with pytest.raises(ValueError, match="budget_exhausted"):
            await approval_continuation(runs, reader, adapter, args, pending)
        state = (await reader.get_latest_snapshot(pending.session_id)).graph_state
        assert state["model_call_count"] == 1 and state["termination_reason"] == "context_budget_exhausted"
        assert state["turn_token_usage"]["prompt_tokens"] == 950 and state["model_context_window"] == 1000
        assert len(agent.requests) == 1 and [method for method, _ in business_http[1]] == ["POST"]
        if os.getenv("QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"):
            root = Path(os.environ["QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"])
            root.mkdir(parents=True, exist_ok=True)
            (root / ("context-postgres.json" if live else "context-memory.json")).write_text(json.dumps({
                "real_postgres": live, "controlled_provider_usage": True, "calls": len(agent.requests),
                "effects": business_http[1], "snapshot": state,
            }, ensure_ascii=False, default=str), encoding="utf-8")


class BatchedApprovalAgent(ControlledAgent):
    def __init__(self, business):
        super().__init__()
        self.business = business

    async def invoke(self, key, request):
        response = await super().invoke(key, request)
        if len(self.requests) == 1:
            url = self.business[0] + "/orders/" + self.business[2]
            response.tool_calls.append(ModelToolCall(id="target", name="api-test-runner", arguments={
                "worker_action": "execute_task", "task": {"task_id": "target", "method": "GET",
                    "path": url, "full_url": url, "timeout_seconds": 5}}))
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_multiple_approvals_share_one_model_call_and_can_finish_at_the_limit(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live, agent=BatchedApprovalAgent(business_http)) as values:
        _, writer, reader, sessions, runs, agent, adapter, args, _, _ = values
        sessions._runtime_service._max_iterations = 3
        pending = await adapter().execute(**await args())
        approvals = await reader.list_approvals(pending.session_id)
        assert len(approvals) == 2 and len(agent.requests) == 1
        try:
            pending = await approval_continuation(runs, reader, adapter, args, pending)
        finally:
            if os.getenv("QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"):
                root = Path(os.environ["QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"])
                root.mkdir(parents=True, exist_ok=True)
                snapshots = await reader.list_snapshots(pending.session_id, include_graph_state=True)
                session = await reader.get_session(pending.session_id)
                (root / ("batch-first-postgres.json" if live else "batch-first-memory.json")).write_text(json.dumps({
                    "real_postgres": live, "calls": len(agent.requests), "effects": business_http[1],
                    "snapshots": [row.model_dump(mode="json") for row in snapshots],
                    "pending_turn": session.metadata.get("pending_turn"),
                }, ensure_ascii=False, default=str), encoding="utf-8")
        assert len(agent.requests) == 1 and len(business_http[1]) == 1
        state = (await reader.get_latest_snapshot(pending.session_id)).graph_state
        assert state["model_call_count"] == 1
        pending = await approval_continuation(runs, reader, adapter, args, pending)
        assert len(agent.requests) == 2 and len(business_http[1]) == 2
        session_id = pending.session_id
        outcome = await approval_continuation(runs, reader, adapter, args, pending)
        assert outcome.completion.status == "passed" and len(agent.requests) == 3
        assert [method for method, _ in business_http[1]] == ["POST", "GET", "DELETE"]
        state = (await reader.get_latest_snapshot(session_id)).graph_state
        assert state["model_call_count"] == state["max_iterations"] == 3
        replay = await adapter().execute(**await args())
        assert replay.completion.status == "passed" and len(agent.requests) == 3
        assert len(business_http[1]) == 3
        if os.getenv("QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"):
            root = Path(os.environ["QA_APPROVAL_MODEL_BUDGET_EVIDENCE_DIR"])
            root.mkdir(parents=True, exist_ok=True)
            (root / ("batch-postgres.json" if live else "batch-memory.json")).write_text(json.dumps({
                "real_postgres": live, "calls": len(agent.requests), "effects": business_http[1],
                "completion": outcome.completion.model_dump(mode="json"), "snapshot": state,
                "terminal_replay_model_calls": 0, "terminal_replay_effects": 0,
            }, ensure_ascii=False, default=str), encoding="utf-8")

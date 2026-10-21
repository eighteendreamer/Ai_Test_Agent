from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.runtime.runtime_service import RuntimeService, RuntimeTurnResult
from src.application.security.approval_scope_service import ApprovalScopeService
from src.domain.models import SessionRecord
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.session import (
    ExecutionRequest,
    RuntimeMode,
    SessionMode,
    SessionStatus,
)
from src.schemas.tool_runtime import ToolExecutionRecord


class _ModelRuntimeStub:
    def get_model_config(self, _model_key):
        return None

    def get_default_model_config(self):
        return None

class _ToolRuntimeStub:
    def __init__(self):
        self.call_ids = []

    async def execute(self, *, tool, call, context):
        self.call_ids.append(call.id)
        return ToolExecutionRecord(
            call_id=call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="completed",
            summary=f"completed {call.id}",
            input=dict(call.arguments),
            output={"call_id": call.id, "ok": True},
        )


def _make_session() -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="session-approval-checkpoint",
        title="approval checkpoint regression",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="security_testing",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_partial_approval_resume_preserves_all_tool_results_and_call_pairs(monkeypatch):
    tool_runtime = _ToolRuntimeStub()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=tool_runtime,
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
    )
    session = _make_session()
    request = ExecutionRequest(
        turn_id="turn-approval-checkpoint",
        session_id=session.id,
        user_message="look up nginx exploit references",
        normalized_input="look up nginx exploit references",
        mode_key="security_testing",
        context={"safety_assessment": {"decision": "allow", "authorization_status": "verified"}},
    )
    state = runtime._build_initial_state(session, request)
    prior_record = ToolExecutionRecord(
        call_id="prior-call",
        tool_key="knowledge-rag",
        tool_name="Knowledge RAG",
        status="completed",
        summary="prior tool call completed",
        input={},
        output={"ok": True},
    )
    call_ids = ["prior-call", "approval-call-1", "approval-call-2"]
    assistant_tool_message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "knowledge-rag" if index == 0 else "exploit-workbench-runner", "arguments": "{}"},
            }
            for index, call_id in enumerate(call_ids)
        ],
    }
    state["assistant_tool_call_message"] = assistant_tool_message
    state["runtime_messages"] = [assistant_tool_message]
    state["tool_results"] = [prior_record.model_dump(mode="python")]
    state["tool_messages"] = [runtime._build_tool_message(prior_record)]

    tool = runtime._tool_registry.get("exploit-workbench-runner")
    arguments_by_id = {
        "approval-1": {"command_profile": "searchsploit_exploit_lookup", "target": "nginx 1.18"},
        "approval-2": {"command_profile": "searchsploit_exploit_lookup", "target": "nginx 1.19"},
    }
    approvals = []
    for approval_id, call_id in zip(("approval-1", "approval-2"), call_ids[1:]):
        arguments = arguments_by_id[approval_id]
        scope_hash = ApprovalScopeService().build_hash(
            mode_key="security_testing",
            tool_key=tool.key,
            arguments=arguments,
            context=request.context,
        )
        approvals.append({
            "id": approval_id,
            "status": "approved",
            "tool_key": tool.key,
            "tool_name": tool.name,
            "metadata": {
                "call_id": call_id,
                "arguments": arguments,
                "approval_scope_hash": scope_hash,
            },
        })

    state["pending_approvals"] = [
        {key: value for key, value in approval.items() if key != "status"}
        for approval in approvals
    ]
    state["control_state"] = "waiting_approval"
    state["termination_reason"] = "waiting_approval"
    session.metadata["pending_turn"] = runtime._build_pending_turn(state, stage="waiting_approval")

    async def capture_without_model(session_arg, _request, resumed_state, _executor):
        resumed_state["termination_reason"] = ""
        resumed_state["control_state"] = "completed"
        resumed_state["pending_turn"] = {}
        resumed_state["final_response"] = "captured after approval resume"
        snapshot = runtime._build_snapshot(session_arg, resumed_state, session_arg.snapshot_count + 1)
        return RuntimeTurnResult(
            output_text=resumed_state["final_response"],
            events=runtime._events_from_log(session_arg.id, resumed_state["event_log"]),
            snapshot=snapshot,
            approvals=[],
            state=resumed_state,
            tool_messages=[],
            pending_turn={},
        )

    monkeypatch.setattr(runtime, "_execute_observed", capture_without_model)

    first = await runtime.resume_after_approval(session, approvals[0])
    assert first is not None and first.snapshot.stage == "waiting_approval"
    first_checkpoint = first.pending_turn
    assert first_checkpoint["graph_state"]["tool_results"] == first_checkpoint["tool_results"]
    assert first_checkpoint["graph_state"]["tool_messages"] == first_checkpoint["tool_messages"]
    assert [item["call_id"] for item in first_checkpoint["tool_results"]] == [
        "prior-call",
        "approval-call-1",
    ]

    session.metadata["pending_turn"] = first_checkpoint
    second = await runtime.resume_after_approval(session, approvals[1])

    assert second is not None
    assert [item["call_id"] for item in second.state["tool_results"]] == call_ids
    assert tool_runtime.call_ids == call_ids[1:]
    assistant_ids = runtime._latest_assistant_tool_call_ids(second.state["runtime_messages"])
    result_ids = [item.get("tool_call_id") for item in second.state["runtime_messages"] if item.get("role") == "tool"]
    assert assistant_ids == call_ids
    assert result_ids == call_ids

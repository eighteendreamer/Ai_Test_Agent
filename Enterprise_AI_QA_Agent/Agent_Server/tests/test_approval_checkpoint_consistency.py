from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import httpx
import pytest

from src.application.runtime.runtime_service import RuntimeService, RuntimeTurnResult
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.security.approval_scope_service import ApprovalScopeService
from src.domain.models import SessionRecord
from src.graph.nodes.tool_executor import build_tool_executor_node
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
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            body = json.dumps({"path": self.path, "observed": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        await _test_partial_approval_resume_against_local_http(server, requests, monkeypatch)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()


async def _test_partial_approval_resume_against_local_http(server, requests, monkeypatch):
    tool_runtime = ToolRuntimeService()
    tool_registry = ToolRegistry()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=tool_runtime,
        tool_registry=tool_registry,
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
    prior_assistant_tool_message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_ids[0],
                "type": "function",
                "function": {"name": "knowledge-rag", "arguments": "{}"},
            }
        ],
    }
    assistant_tool_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "knowledge-rag", "arguments": "{}"},
            }
            for call_id in call_ids[1:]
        ],
    }
    prior_tool_message = runtime._build_tool_message(prior_record)
    state["assistant_tool_call_message"] = assistant_tool_message
    state["runtime_messages"] = [
        {"role": "user", "content": request.user_message},
        prior_assistant_tool_message,
        prior_tool_message,
    ]
    state["tool_results"] = [prior_record.model_dump(mode="python")]
    state["tool_messages"] = [prior_tool_message]

    tool = runtime._tool_registry.get("knowledge-rag")

    async def read_local_probe(arguments, _context):
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(str(arguments["query"]))
        response.raise_for_status()
        return {"status": "completed", "summary": "Read local probe", "result": response.json()}

    tool_runtime._handlers[tool.key] = read_local_probe
    arguments_by_id = {
        "approval-1": {"query": f"http://127.0.0.1:{server.server_port}/approved/1"},
        "approval-2": {"query": f"http://127.0.0.1:{server.server_port}/approved/2"},
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
        pending_record = ToolExecutionRecord(
            call_id=call_id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="waiting_approval",
            summary="Waiting for approval",
            input=arguments,
            approval_id=approval_id,
        )
        state["tool_results"].append(pending_record.model_dump(mode="python"))
        state["tool_messages"].append(runtime._build_tool_message(pending_record))

    state["pending_approvals"] = [
        {key: value for key, value in approval.items() if key != "status"}
        for approval in approvals
    ]
    state["control_state"] = "waiting_approval"
    state["termination_reason"] = "waiting_approval"
    session.metadata["pending_turn"] = runtime._build_pending_turn(state, stage="waiting_approval")
    session.metadata["pending_turn"]["conversation_messages"] = [
        *state["runtime_messages"], assistant_tool_message,
    ]

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
    assert first_checkpoint["conversation_messages"][-1]["tool_calls"] == assistant_tool_message["tool_calls"]
    assert first_checkpoint["graph_state"]["runtime_messages"][-1]["tool_calls"] == assistant_tool_message["tool_calls"]
    assert first_checkpoint["graph_state"]["tool_results"] == first_checkpoint["tool_results"]
    assert first_checkpoint["graph_state"]["tool_messages"] == first_checkpoint["tool_messages"]
    first_results = {item["call_id"]: item for item in first_checkpoint["tool_results"]}
    assert set(first_results) == set(call_ids)
    assert first_results["prior-call"]["status"] == "completed"
    assert first_results["approval-call-1"]["status"] == "completed"
    assert first_results["approval-call-2"]["status"] == "waiting_approval"

    session.metadata["pending_turn"] = first_checkpoint
    second = await runtime.resume_after_approval(session, approvals[1])

    assert second is not None
    assert [item["call_id"] for item in second.state["tool_results"]] == call_ids
    assistant_ids = runtime._latest_assistant_tool_call_ids(second.state["runtime_messages"])
    result_ids = [item.get("tool_call_id") for item in second.state["runtime_messages"] if item.get("role") == "tool"]
    assert assistant_ids == call_ids[1:]
    assert result_ids == call_ids
    assert len(set(result_ids)) == len(result_ids)
    assert requests == ["/approved/1", "/approved/2"]
    completed_results = {
        item["call_id"]: item
        for item in second.state["tool_results"]
        if item["status"] == "completed"
    }
    assert [completed_results[call_id]["output"]["result"]["path"] for call_id in call_ids[1:]] == requests


@pytest.mark.asyncio
async def test_tool_executor_persists_approval_batch_in_both_checkpoint_transcripts(monkeypatch):
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=_ToolRuntimeStub(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
    )
    session = _make_session()
    request = ExecutionRequest(
        turn_id="turn-tool-executor-checkpoint",
        session_id=session.id,
        user_message="run two approval-gated calls",
        normalized_input="run two approval-gated calls",
        mode_key="security_testing",
        context={"safety_assessment": {"decision": "allow", "authorization_status": "verified"}},
    )
    state = runtime._build_initial_state(session, request)
    tool = runtime._tool_registry.get("knowledge-rag")
    call = {
        "id": "pending-call-1",
        "name": tool.key,
        "arguments": {"query": "first"},
    }
    assistant_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call["id"],
            "type": "function",
            "function": {"name": tool.key, "arguments": json.dumps(call["arguments"])},
        }],
    }
    state["model_tool_calls"] = [call]
    state["assistant_tool_call_message"] = assistant_message
    state["runtime_messages"] = [{"role": "user", "content": request.user_message}]
    record = ToolExecutionRecord(
        call_id=call["id"],
        tool_key=tool.key,
        tool_name=tool.name,
        status="waiting_approval",
        summary="Waiting for approval",
        input=call["arguments"],
        approval_id="approval-call-1",
    )
    approval = {
        "id": "approval-call-1",
        "tool_key": tool.key,
        "tool_name": tool.name,
        "metadata": {"call_id": call["id"], "arguments": call["arguments"]},
    }

    async def resolve_call(**_kwargs):
        return {
            "tool_result": record.model_dump(mode="python"),
            "tool_message": runtime._build_tool_message(record),
            "approval": approval,
        }

    monkeypatch.setattr("src.graph.nodes.tool_executor._resolve_tool_call", resolve_call)
    tool_executor = build_tool_executor_node(
        tool_registry=runtime._tool_registry,
        permission_service=None,
        tool_runtime_service=None,
    )
    result = await tool_executor(state)
    checkpoint = result["pending_turn"]

    assert result["runtime_messages"] == [{"role": "user", "content": request.user_message}, assistant_message]
    assert checkpoint["conversation_messages"] == result["runtime_messages"]
    assert checkpoint["graph_state"]["runtime_messages"] == result["runtime_messages"]

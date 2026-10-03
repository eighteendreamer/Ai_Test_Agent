from __future__ import annotations

import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.graph.nodes.prompt_assembler import build_prompt_assembler_node
from src.registry.agents import AgentRegistry
from src.registry.tools import ToolRegistry
from src.schemas.model_config import ModelConfigRecord


def _state(messages):
    return {
        "session_id": "history-session",
        "turn_id": "history-turn",
        "trace_id": "history-trace",
        "selected_agent_key": "coordinator",
        "selected_model_key": "local-model",
        "selected_model_name": "local-model",
        "selected_model_provider": "openai",
        "mode_key": "default",
        "loop_iteration": 0,
        "available_tool_keys": [],
        "model_visible_tool_keys": [],
        "allowed_tool_keys": [],
        "approval_required_tool_keys": [],
        "denied_tool_keys": [],
        "user_message": "Read the uploaded requirements and explain the changes.",
        "normalized_input": "Read the uploaded requirements and explain the changes.",
        "runtime_messages": deepcopy(messages),
        "context_bundle": {},
    }


def _assemble(state):
    return build_prompt_assembler_node(PromptAssemblyService(), AgentRegistry())(state)


def test_reassembly_preserves_history_and_legitimate_repeated_user_messages():
    # Regression input: the service returns the full history, while the node
    # previously appended that same history on every loop iteration.
    history = [
        {"role": "user", "content": "Check again"},
        {"role": "assistant", "content": "The first check completed."},
        {"role": "user", "content": "Check again"},
    ]
    state = _state(history)
    for iteration in range(4):
        state["loop_iteration"] = iteration
        _assemble(state)
        assert state["runtime_messages"] == history


@pytest.mark.parametrize("normalized", ["", "  original request  ", "original request"])
def test_fallback_does_not_repeat_an_unchanged_normalized_request(normalized):
    state = _state([])
    state.update(user_message="original request", normalized_input=normalized)
    result = PromptAssemblyService().build_for_turn(state, ["coordinator"])
    assert "normalized_input" not in [section.key for section in result.runtime_message_sections]
    assert result.runtime_messages[0]["content"].count("original request") == 1


def test_fallback_preserves_a_distinct_normalized_request():
    state = _state([])
    state.update(user_message="/test orders", normalized_input="Test the order API in staging.")
    result = PromptAssemblyService().build_for_turn(state, ["coordinator"])
    sections = {section.key: section.content for section in result.runtime_message_sections}
    assert sections["user_request"] == "/test orders"
    assert sections["normalized_input"] == "Test the order API in staging."


@pytest.fixture
def model_http_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            response = json.dumps({
                "id": f"response-{len(requests)}",
                "object": "chat.completion",
                "created": 1,
                "model": "local-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Checked."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("langchain_enabled", [False, True])
def test_actual_http_preserves_tool_history_across_reassembly(model_http_server, langchain_enabled, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    url, requests = model_http_server
    config = ModelConfigRecord(
        key="local-model", name="local-model", provider="openai",
        transport="openai_chat_completions", model_id="local-model",
        api_base_url=url, api_key=uuid4().hex,
    )
    runtime = ModelRuntimeService(
        model_registry=SimpleNamespace(get_runtime_config=lambda key: config),
        settings=SimpleNamespace(model=SimpleNamespace(
            llm_request_timeout_seconds=10,
            langchain_model_adapter_enabled=langchain_enabled,
        )),
    )
    history = [
        {"role": "user", "content": "Read the uploaded requirements."},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "read-doc-1", "name": "attachment-reader", "arguments": {"attachment_id": "doc-1"},
        }]},
        {"role": "tool", "tool_call_id": "read-doc-1", "name": "attachment-reader", "content": '{"text":"Timeout changed from 30 to 60 seconds."}'},
    ]
    state = _state(history)
    invoker = build_model_invoker_node(runtime, ToolRegistry())

    async def scenario():
        for iteration in range(3):
            state["loop_iteration"] = iteration
            _assemble(state)
            await invoker(state)
            assert state["model_response_summary"]["mode"] == "ok"
            assert state["runtime_messages"] == history

    asyncio.run(scenario())
    # Real loopback TCP + the production SDK/adapter; one successful send per
    # explicit invocation. This does not claim external-provider availability.
    assert len(requests) == 3
    for payload in requests:
        messages = [message for message in payload["messages"] if message["role"] != "system"]
        assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
        assert messages[0]["content"] == history[0]["content"]
        assert messages[1]["tool_calls"][0]["id"] == "read-doc-1"
        assert messages[2]["tool_call_id"] == "read-doc-1"
        assert "Timeout changed from 30 to 60 seconds." in messages[2]["content"]

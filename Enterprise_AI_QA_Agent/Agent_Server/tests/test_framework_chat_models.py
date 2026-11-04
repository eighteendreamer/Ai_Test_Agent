"""P0 contracts: official SDKs must emit the selected native protocol."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage, messages_from_dict, messages_to_dict
from langchain_core.tools import tool

from src.application.model_adapters.official_chat_model_factory import OfficialChatModelFactory
from src.application.model_adapters.langchain_model_adapter import LangChainModelAdapter
from src.application.models.model_runtime_service import ModelRuntimeService
from src.core.config import get_settings
from src.runtime.execution_context import current_graph_event_sink, graph_event_scope
from src.schemas.model_config import ModelConfigRecord
from src.schemas.model_request_options import ModelReasoningOptions, ModelRequestProfile


TRANSPORTS = (
    "openai_chat_completions", "openai_responses", "anthropic_messages",
    "google_gemini_generate_content",
)


def _reply(transport):
    if transport == "openai_chat_completions":
        return {"id": "chatcmpl_fixture", "object": "chat.completion", "created": 1,
            "model": "fixture-model", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "fixture answer"},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
    if transport == "openai_responses":
        return {"id": "resp_fixture", "object": "response", "created_at": 1,
            "model": "fixture-model", "status": "completed", "output": [{
                "id": "msg_fixture", "type": "message", "status": "completed",
                "role": "assistant", "content": [{"type": "output_text",
                    "text": "fixture answer", "annotations": []}]}],
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}}
    if transport == "anthropic_messages":
        return {"id": "msg_fixture", "type": "message", "role": "assistant",
            "model": "fixture-model", "content": [{"type": "text", "text": "fixture answer"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 3}}
    return {"responseId": "gemini_fixture", "candidates": [{"index": 0,
        "content": {"role": "model", "parts": [{"text": "fixture answer"}]},
        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10}}


def _stream_events(transport, response):
    if transport == "openai_chat_completions":
        message = response["choices"][0]["message"]
        delta = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            delta["tool_calls"] = [{"index": index, **call} for index, call in enumerate(message["tool_calls"])]
        return [{**response, "object": "chat.completion.chunk", "choices": [{"index": 0,
            "delta": delta, "finish_reason": response["choices"][0]["finish_reason"]}]}]
    if transport == "openai_responses":
        item = response["output"][0]
        return [
            {"type": "response.created", "response": {**response, "output": [], "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0,
                "item": {**item, "content": [], "status": "in_progress"}},
            {"type": "response.content_part.added", "output_index": 0, "content_index": 0,
                "item_id": item["id"], "part": {"type": "output_text", "text": "", "annotations": []}},
            {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0,
                "content_index": 0, "delta": "fixture answer"},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
    if transport == "anthropic_messages":
        return [
            {"type": "message_start", "message": {**response, "content": [], "stop_reason": None,
                "usage": {"input_tokens": 7, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "fixture answer"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
    return [response]


@contextmanager
def _provider(transport, *, status=200, first_reply=None, stream_events=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append({"path": self.path,
                "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                "fixture_header": self.headers.get("x-qa-fixture")})
            response = (first_reply if first_reply is not None and len(requests) == 1
                else _reply(transport)) if status == 200 else {
                "type": "error", "error": {"code": status, "status": "UNAVAILABLE",
                    "type": "api_error", "message": "controlled fixture failure"}}
            streaming = status == 200 and (requests[-1]["body"].get("stream")
                or ":streamGenerateContent" in self.path)
            if streaming:
                events = _stream_events(transport, response)
                if stream_events is not None:
                    events = stream_events(events)
                raw = "".join((f"event: {event['type']}\n" if transport == "anthropic_messages" else "")
                    + "data: " + json.dumps(event) + "\n\n" for event in events).encode()
            else:
                raw = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        suffix = "/v1" if transport.startswith("openai_") else ""
        yield f"http://127.0.0.1:{server.server_port}{suffix}", requests
    finally:
        server.shutdown()
        server.server_close()
        serving.join(5)
        assert not serving.is_alive()


def _config(transport, url, **updates):
    values = dict(key="framework-fixture", name="Framework fixture", provider=(
        "google" if transport.startswith("google") else
        "anthropic" if transport.startswith("anthropic") else "openai"),
        transport=transport, model_id="fixture-model", api_base_url=url,
        api_key="local-fixture-marker", max_output_tokens=512,
        temperature=0.2, extra_headers={"x-qa-fixture": "framework"})
    values.update(updates)
    return ModelConfigRecord(**values)


def _text(message):
    if isinstance(message.content, str):
        return message.content
    return "".join(part.get("text", "") for part in message.content if isinstance(part, dict))


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_official_model_preserves_native_endpoint_parameters_and_usage(transport):
    with _provider(transport) as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(_config(transport, url),
            "local-fixture-marker")
        result = await model.ainvoke([HumanMessage(content="fixture request")])
        assert _text(result) == "fixture answer"
        assert result.usage_metadata["total_tokens"] == 10
        assert len(requests) == 1
        request, = requests
        assert request["fixture_header"] == "framework"
        body = request["body"]
        if transport == "openai_chat_completions":
            assert request["path"] == "/v1/chat/completions"
            assert body["max_completion_tokens"] == 512 and "input" not in body
        elif transport == "openai_responses":
            assert request["path"] == "/v1/responses"
            assert body["max_output_tokens"] == 512 and "messages" not in body
        elif transport == "anthropic_messages":
            assert request["path"] == "/v1/messages"
            assert body["max_tokens"] == 512
        else:
            assert request["path"] == "/v1beta/models/fixture-model:generateContent"
            assert body["generationConfig"]["maxOutputTokens"] == 512


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_no_hidden_sdk_retry_when_shared_policy_owns_retries(transport):
    with _provider(transport, status=503) as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5, max_retries=0).build_model(
            _config(transport, url), "local-fixture-marker")
        with pytest.raises(Exception, match="controlled fixture failure"):
            await model.ainvoke([HumanMessage(content="fixture request")])
        assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("token_field", ["max_tokens", "max_completion_tokens"])
async def test_explicit_chat_token_profile_is_preserved_on_the_wire(token_field):
    with _provider("openai_chat_completions") as (url, requests):
        config = _config("openai_chat_completions", url, model_id="codex-fixture-model",
            request_profile=ModelRequestProfile(chat_token_parameter=token_field,
                reasoning_style="effort", reasoning_efforts=["low"],
                default_reasoning=ModelReasoningOptions(effort="low")))
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(config, "local-fixture-marker")
        await model.ainvoke([HumanMessage(content="fixture request")])
        body = requests[0]["body"]
        assert requests[0]["path"] == "/v1/chat/completions"
        assert body[token_field] == 512 and body["reasoning_effort"] == "low"
        other = "max_tokens" if token_field == "max_completion_tokens" else "max_completion_tokens"
        assert other not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_database_backed_framework_model_resolver_uses_official_factory(transport):
    with _provider(transport) as (url, requests):
        config = _config(transport, url)

        class Registry:
            def get_runtime_config(self, key):
                assert key == config.key
                return config

        runtime = ModelRuntimeService(Registry(), get_settings())
        model = await runtime.resolve_langchain_chat_model(config.key)
        result = await model.ainvoke([HumanMessage(content="fixture request")])
        assert _text(result) == "fixture answer" and len(requests) == 1


@contextmanager
def _pause_model_construction(monkeypatch, *, error=None):
    """Gate the measured synchronous boundary; the timeout only prevents a hang."""
    loop = asyncio.get_running_loop()
    entered, finished, release = asyncio.Event(), threading.Event(), threading.Event()
    build, attempts = LangChainModelAdapter.build_model, []

    def paused(adapter, config, api_key):
        attempt = SimpleNamespace(config=config, sink=current_graph_event_sink(),
            thread_id=threading.get_ident(), released_by_loop=False)
        attempts.append(attempt)
        loop.call_soon_threadsafe(entered.set)
        try:
            attempt.released_by_loop = release.wait(5)
            if error is not None:
                raise error
            return build(adapter, config, api_key)
        finally:
            finished.set()

    monkeypatch.setattr(LangChainModelAdapter, "build_model", paused)
    try:
        yield SimpleNamespace(entered=entered, finished=finished, release=release, attempts=attempts)
    finally:
        release.set()


def _model_runtime(config):
    class Registry:
        def get_runtime_config(self, key):
            assert key == config.key
            return config

    return ModelRuntimeService(Registry(), get_settings())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["returned", "failed"])
async def test_model_resolution_leaves_loop_available_and_preserves_context_and_error(
    monkeypatch, caplog, outcome,
):
    from src.application.models.framework_model_call_policy import model_config_fingerprint

    error = RuntimeError("private fixture constructor detail") if outcome == "failed" else None
    with _provider("openai_chat_completions") as (url, requests):
        config = _config("openai_chat_completions", url)
        sink, loop_thread = object(), threading.get_ident()
        with _pause_model_construction(monkeypatch, error=error) as gate, graph_event_scope(sink):
            task = asyncio.create_task(_model_runtime(config).resolve_langchain_chat_model(config.key))
            try:
                await asyncio.wait_for(gate.entered.wait(), 10)
                # This coroutine must run while the synchronous constructor is
                # still gated, as the resource-renewal coroutine must in reality.
                assert not gate.finished.is_set(), "Model construction blocked the event loop"
                attempt, = gate.attempts
                assert attempt.thread_id != loop_thread and attempt.sink is sink
                assert current_graph_event_sink() is sink
                gate.release.set()
                if error is None:
                    model = await task
                    assert model.metadata["qa_model_config_fingerprint"] == model_config_fingerprint(config)
                else:
                    with pytest.raises(RuntimeError) as raised:
                        await task
                    assert raised.value is error
                    record, = [r for r in caplog.records
                        if r.getMessage() == "framework_chat_model_construction_failed"]
                    assert record.model_key == config.key and record.error_type == "RuntimeError"
                assert attempt.released_by_loop and len(gate.attempts) == 1
                assert requests == []
            finally:
                gate.release.set()
                await asyncio.gather(task, return_exceptions=True)
        assert current_graph_event_sink() is None
        assert "private fixture constructor detail" not in caplog.text
        assert "local-fixture-marker" not in caplog.text


@pytest.mark.asyncio
async def test_model_resolution_fingerprint_matches_snapshot_during_concurrent_config_change(monkeypatch):
    from src.application.models.framework_model_call_policy import model_config_fingerprint

    with _provider("openai_chat_completions") as (url, requests):
        config = _config("openai_chat_completions", url)
        original = config.model_copy(deep=True)
        with _pause_model_construction(monkeypatch) as gate:
            task = asyncio.create_task(_model_runtime(config).resolve_langchain_chat_model(config.key))
            try:
                await asyncio.wait_for(gate.entered.wait(), 10)
                assert not gate.finished.is_set(), "Model construction blocked the event loop"
                config.temperature = 0.7
                config.extra_headers["x-qa-fixture"] = "changed"
                gate.release.set()
                model = await task
                attempt, = gate.attempts
                assert model.temperature == original.temperature
                assert model.default_headers == original.extra_headers
                assert model.metadata["qa_model_config_fingerprint"] == model_config_fingerprint(original)
                assert model.metadata["qa_model_config_fingerprint"] != model_config_fingerprint(config)
                assert attempt.config is not config
                assert attempt.config == original and attempt.released_by_loop
                assert requests == []
            finally:
                gate.release.set()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("outcome", ["returned", "failed"])
async def test_cancelled_model_resolution_never_continues_or_retries_construction(
    monkeypatch, caplog, transport, outcome,
):
    from src.application.models import model_runtime_service

    loop, failure_logged = asyncio.get_running_loop(), asyncio.Event()
    warning = model_runtime_service.logger.warning

    def observe_warning(message, *args, **kwargs):
        warning(message, *args, **kwargs)
        if message == "framework_chat_model_construction_failed":
            loop.call_soon_threadsafe(failure_logged.set)

    monkeypatch.setattr(model_runtime_service.logger, "warning", observe_warning)
    error = RuntimeError("private late constructor failure") if outcome == "failed" else None
    with _provider(transport) as (url, requests):
        config = _config(transport, url)
        continued = []
        with _pause_model_construction(monkeypatch, error=error) as gate:
            async def invoke_after_resolution():
                model = await _model_runtime(config).resolve_langchain_chat_model(config.key)
                continued.append(True)
                await model.ainvoke([HumanMessage(content="must not send")])

            task = asyncio.create_task(invoke_after_resolution())
            try:
                await asyncio.wait_for(gate.entered.wait(), 10)
                assert not gate.finished.is_set(), "Model construction blocked the event loop"
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not gate.finished.is_set()  # Cancellation cannot stop the constructor thread.
                record, = [r for r in caplog.records
                    if r.getMessage() == "framework_chat_model_construction_cancelled"]
                assert record.model_key == config.key and record.construction_may_continue is True
                gate.release.set()
                assert await asyncio.to_thread(gate.finished.wait, 10)
                if error is not None:
                    await asyncio.wait_for(failure_logged.wait(), 10)
                    failed, = [r for r in caplog.records
                        if r.getMessage() == "framework_chat_model_construction_failed"]
                    assert failed.model_key == config.key and failed.error_type == "RuntimeError"
                assert continued == [] and requests == []
                attempt, = gate.attempts
                assert attempt.released_by_loop
            finally:
                gate.release.set()
                await asyncio.gather(task, return_exceptions=True)
                assert await asyncio.to_thread(gate.finished.wait, 10)
        assert "private late constructor failure" not in caplog.text
        assert "local-fixture-marker" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_declared_default_reasoning_survives_harness_tool_binding(transport):
    with _provider(transport) as (url, requests):
        effort = transport.startswith("openai_")
        profile = ModelRequestProfile(reasoning_style="effort" if effort else "budget",
            reasoning_efforts=["low"] if effort else [],
            default_reasoning=ModelReasoningOptions(effort="low") if effort
                else ModelReasoningOptions(budget_tokens=1024))
        config = _config(transport, url, temperature=None, max_output_tokens=2048,
            request_profile=profile)
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(config, "local-fixture-marker")

        @tool
        def inspect_source(path: str) -> str:
            """Read a synthetic source file."""
            return path

        await model.bind_tools([inspect_source]).ainvoke([HumanMessage(content="fixture request")])
        body = requests[0]["body"]
        if transport == "openai_chat_completions":
            assert body["reasoning_effort"] == "low"
        elif transport == "openai_responses":
            assert body["reasoning"] == {"effort": "low"}
        elif transport == "anthropic_messages":
            assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        else:
            assert body["generationConfig"]["thinkingConfig"] == {"thinking_budget": 1024}


def _tool_reply(transport):
    reply = _reply(transport)
    if transport == "openai_chat_completions":
        reply["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant",
            "content": None, "tool_calls": [{"id": "native-call", "type": "function",
                "function": {"name": "inspect_source", "arguments": '{"path":"main.py"}'}}]})
    elif transport == "openai_responses":
        reply["output"] = [{"type": "function_call", "id": "fc_fixture", "status": "completed",
            "call_id": "native-call", "name": "inspect_source", "arguments": '{"path":"main.py"}'}]
    elif transport == "anthropic_messages":
        reply.update(stop_reason="tool_use", content=[
            {"type": "thinking", "thinking": "synthetic reasoning", "signature": "fixture-signature"},
            {"type": "tool_use", "id": "native-call", "name": "inspect_source", "input": {"path": "main.py"}}])
    else:
        reply["candidates"][0]["content"]["parts"] = [{"functionCall": {
            "id": "native-call", "name": "inspect_source", "args": {"path": "main.py"}},
            "thoughtSignature": "Zml4dHVyZS1zaWduYXR1cmU="}]
    return reply


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("with_artifact", [False, True])
async def test_native_tool_call_and_signature_survive_archive_and_tool_result(transport, with_artifact):
    with _provider(transport, first_reply=_tool_reply(transport)) as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(_config(transport, url),
            "local-fixture-marker")

        @tool
        def inspect_source(path: str) -> str:
            """Read a synthetic source file."""
            return "synthetic file"

        bound = model.bind_tools([inspect_source])
        user = HumanMessage(content="fixture request")
        first = await bound.ainvoke([user])
        call, = first.tool_calls
        assert call["name"] == "inspect_source" and call["args"] == {"path": "main.py"}
        restored, = messages_from_dict(messages_to_dict([first]))
        assert restored.model_dump() == first.model_dump()
        material = {"enterprise_qa_tool_projection": {"application_only": "not-for-provider-projection-material"}}
        tool_result = ToolMessage(content="synthetic file", tool_call_id=call["id"], name=call["name"],
            artifact=material if with_artifact else None)
        archived_tool, = messages_from_dict(messages_to_dict([tool_result]))
        assert archived_tool == tool_result
        result = await bound.ainvoke([user, restored, archived_tool])
        assert _text(result) == "fixture answer" and len(requests) == 2
        body = requests[1]["body"]
        assert "not-for-provider-projection-material" not in json.dumps(body)
        assert "enterprise_qa_tool_projection" not in json.dumps(body)
        assert "synthetic file" in json.dumps(body)
        if transport == "openai_chat_completions":
            assert body["messages"][-1]["tool_call_id"] == call["id"]
        elif transport == "openai_responses":
            assert body["input"][-1]["call_id"] == call["id"]
        elif transport == "anthropic_messages":
            assert body["messages"][-1]["content"][0]["tool_use_id"] == call["id"]
            assert body["messages"][-2]["content"][0]["signature"] == "fixture-signature"
        else:
            assert body["contents"][-1]["parts"][0]["functionResponse"]["name"] == call["name"]
            assert body["contents"][-2]["parts"][0]["thoughtSignature"] == "Zml4dHVyZS1zaWduYXR1cmU="


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_native_stream_retains_usage_and_terminal_metadata(transport):
    with _provider(transport) as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(_config(transport, url),
            "local-fixture-marker")
        chunks = [chunk async for chunk in model.astream([HumanMessage(content="fixture request")])]
        assert chunks
        result = chunks[0]
        for chunk in chunks[1:]:
            result += chunk
        assert _text(result) == "fixture answer"
        assert result.usage_metadata["total_tokens"] == 10
        assert len(requests) == 1
        if transport == "openai_responses":
            assert result.response_metadata["status"] == "completed"
        elif transport == "anthropic_messages":
            assert result.response_metadata["stop_reason"] == "end_turn"
        else:
            assert result.response_metadata["finish_reason"] == (
                "STOP" if transport.startswith("google") else "stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("final_input", [None, 0, 7])
@pytest.mark.parametrize("multiple_deltas", [False, True])
async def test_anthropic_cumulative_partial_and_cached_usage_is_not_lost_or_doubled(final_input, multiple_deltas):
    def events_with_usage(events):
        events[0]["message"]["usage"].update(cache_read_input_tokens=5, cache_creation_input_tokens=2)
        if final_input is not None:
            events[-2]["usage"]["input_tokens"] = final_input
        if multiple_deltas:
            events.insert(-2, {"type": "message_delta", "delta": {
                "stop_reason": None, "stop_sequence": None}, "usage": {"output_tokens": 1}})
        return events

    with _provider("anthropic_messages", stream_events=events_with_usage) as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(
            _config("anthropic_messages", url), "local-fixture-marker")
        result = None
        async for chunk in model.astream([HumanMessage(content="fixture request")]):
            result = chunk if result is None else result + chunk
        expected_input = (7 if final_input is None else final_input) + 5 + 2
        assert result.usage_metadata["input_tokens"] == expected_input
        assert result.usage_metadata["output_tokens"] == 3
        assert result.usage_metadata["total_tokens"] == expected_input + 3
        assert result.usage_metadata["input_token_details"]["cache_read"] == 5
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_anthropic_concurrent_streams_have_independent_usage():
    with _provider("anthropic_messages") as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(
            _config("anthropic_messages", url), "local-fixture-marker")

        async def stream_usage():
            result = None
            async for chunk in model.astream([HumanMessage(content="fixture request")]):
                result = chunk if result is None else result + chunk
            return result.usage_metadata

        usages = await asyncio.gather(stream_usage(), stream_usage())
        assert [usage["total_tokens"] for usage in usages] == [10, 10]
        assert len(requests) == 2


def test_anthropic_sync_stream_uses_the_same_usage_contract():
    with _provider("anthropic_messages") as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(
            _config("anthropic_messages", url), "local-fixture-marker")
        chunks = list(model.stream([HumanMessage(content="fixture request")]))
        result = chunks[0]
        for chunk in chunks[1:]:
            result += chunk
        assert result.usage_metadata["total_tokens"] == 10
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_anthropic_stream_error_is_visible_and_not_retried():
    def failed_stream(events):
        return events[:2] + [{"type": "error", "error": {
            "type": "overloaded_error", "message": "fixture stream failure"}}]

    with _provider("anthropic_messages", stream_events=failed_stream) as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(
            _config("anthropic_messages", url), "local-fixture-marker")
        with pytest.raises(Exception, match="fixture stream failure"):
            async for _ in model.astream([HumanMessage(content="fixture request")]):
                pass
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_anthropic_stream_can_be_closed_by_cancellation_cleanup_task():
    with _provider("anthropic_messages") as (url, requests):
        model = OfficialChatModelFactory(timeout_seconds=5).build_model(
            _config("anthropic_messages", url), "local-fixture-marker")
        stream = model._astream([HumanMessage(content="fixture request")])
        await anext(stream)
        # Framework cleanup may run outside the task that consumed the stream.
        await asyncio.create_task(stream.aclose())
        result = None
        async for chunk in model.astream([HumanMessage(content="next request")]):
            result = chunk if result is None else result + chunk
        assert result.usage_metadata["total_tokens"] == 10
        assert len(requests) == 2

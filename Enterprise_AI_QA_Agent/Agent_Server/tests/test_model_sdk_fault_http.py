"""Faults on real TCP/HTTP must preserve the runtime's multi-SDK boundary."""
import asyncio
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import httpx
import httpx2

from test_model_completion_http import Registry, provider
from src.application.models.model_runtime_service import ModelRuntimeService
from src.core.config import get_settings
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, is_model_failure_summary


TRANSPORTS = ["openai_chat_completions", "openai_responses", "anthropic_messages",
    "google_gemini_generate_content", "langchain_chat"]


@contextmanager
def fault_gateway(transport, fault):
    """Do not replace SDK methods; drop/stall an actual response connection."""
    sends, replies, errors = [], [], []
    release = Event()
    with provider(transport, False, stream_variant=(
        "missing-terminal" if fault in {"disconnect", "timeout", "cancel"} else "complete")) as (backend, _, _):
        address = urlsplit(backend)

        class Gateway(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    raw = self.rfile.read(int(self.headers["Content-Length"]))
                    sends.append({"sequence": len(sends) + 1, "path": self.path,
                        "sha256": hashlib.sha256(raw).hexdigest(), "body": json.loads(raw)})
                    if fault == "pre-header-disconnect" and len(sends) == 1:
                        self.close_connection = True
                        return
                    if fault == "forbidden" or (fault in {"rate-limit", "unavailable"} and len(sends) == 1):
                        status = {"forbidden": 403, "rate-limit": 429, "unavailable": 503}[fault]
                        response = json.dumps({"error": {"code": status, "type": "fixture_failure",
                            "status": "PERMISSION_DENIED" if status == 403 else "UNAVAILABLE",
                            "message": "controlled HTTP fault"}}).encode()
                        streaming = False
                    else:
                        connection = HTTPConnection(address.hostname, address.port, timeout=5)
                        try:
                            connection.request("POST", self.path, body=raw,
                                headers={"Content-Type": "application/json"})
                            result = connection.getresponse()
                            status, response = result.status, result.read()
                            streaming = "text/event-stream" in result.getheader("Content-Type", "")
                        finally:
                            connection.close()
                    replies.append({"request_sequence": sends[-1]["sequence"], "status": status, "body": response})
                    self.send_response(status)
                    self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
                    self.send_header("Content-Length", str(len(response) + (
                        100 if fault in {"disconnect", "timeout", "cancel"} else 0)))
                    if fault == "rate-limit":
                        self.send_header("Retry-After", "1")
                    self.end_headers()
                    self.wfile.write(response)
                    self.wfile.flush()
                    if fault in {"timeout", "cancel"}:
                        release.wait(12)
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    # Expected once the consumer cancels or its read expires.
                    self.close_connection = True
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    self.close_connection = True

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        server.daemon_threads = True
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", sends, replies, errors
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            serving.join(5)
            assert not serving.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("fault,streaming", [
    (fault, True) for fault in ["rate-limit", "unavailable", "forbidden", "disconnect", "timeout", "cancel", "pre-header-disconnect"]
] + [(fault, False) for fault in ["rate-limit", "unavailable", "forbidden", "pre-header-disconnect"]])
async def test_real_sdk_faults_have_one_logical_outcome_and_no_midstream_resend(transport, fault, streaming, caplog):
    native = "openai_chat_completions" if transport == "langchain_chat" else transport
    settings = get_settings().model_copy(deep=True)
    settings.model.langchain_model_adapter_enabled = transport == "langchain_chat"
    settings.model.llm_request_timeout_seconds = 2
    deltas, response, escaped = [], None, None
    first_delta = asyncio.Event()
    key = uuid4().hex
    facts = {"transport": transport, "fault": fault, "streaming": streaming, "logical_runtime_invocations": 1,
        "external_ai_calls": 0, "full_P0_P5_complete": False,
        "dependency_versions": {name: importlib.metadata.version(name) for name in (
            "openai", "anthropic", "google-genai", "httpx", "langchain-openai")}}

    async def receive(delta):
        deltas.append(delta)
        first_delta.set()

    with fault_gateway(native, fault) as (url, sends, replies, fixture_errors):
        config = ModelConfigRecord(key="fixture", name="Actual fault fixture", provider=(
            "google" if native.startswith("google") else "anthropic" if native.startswith("anthropic") else "openai"),
            transport=native, model_id="fixture-model", api_base_url=(
                url.removesuffix("/v1") if native == "anthropic_messages" else url),
            api_key=key, max_output_tokens=512)
        runtime = ModelRuntimeService(Registry(config), settings)
        request = ModelInvocationRequest(system_prompt="Return the supplied JSON.",
            messages=[{"role": "user", "content": "Real HTTP fault acceptance"}])
        try:
            async with runtime.stream_handler(receive if streaming else None):
                if fault == "cancel":
                    task = asyncio.create_task(runtime.invoke("fixture", request))
                    try:
                        await asyncio.wait_for(first_delta.wait(), 5)
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                        facts["cancellation_preserved"] = True
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                else:
                    response = await runtime.invoke("fixture", request)
        except Exception as exc:
            escaped = type(exc).__name__
        finally:
            facts.update(physical_requests=len(sends), fixture_errors=fixture_errors,
                escaped_exception_type=escaped, visible_deltas=deltas,
                response=response.model_dump(mode="json") if response else None,
                response_events=[{"request_sequence": item["request_sequence"], "status": item["status"],
                    "size_bytes": len(item["body"]), "sha256": hashlib.sha256(item["body"]).hexdigest()}
                    for item in replies],
                sdk_defaults_preserved=True, authentication_headers_saved=False)
            if name := os.getenv("QA_MODEL_SDK_FAULT_DIR"):
                directory = Path(name) / f"{transport}-{fault}{'' if streaming else '-native'}"
                directory.mkdir(parents=True, exist_ok=False)
                for index, item in enumerate(sends, 1):
                    (directory / f"request-{index}.json").write_text(
                        json.dumps(item, ensure_ascii=False, indent=2), encoding="utf8")
                for item in replies:
                    (directory / f"response-{item['request_sequence']}.bin").write_bytes(item["body"])
                (directory / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")
                assert all(key not in path.read_text(encoding="utf8") for path in directory.glob("*.json"))

        assert not fixture_errors
        assert escaped is None, facts
        expected_path = (f"/v1beta/models/fixture-model:{'streamGenerateContent' if streaming else 'generateContent'}"
            if native.startswith("google") else {"anthropic_messages": "/v1/messages",
                "openai_responses": "/v1/responses", "openai_chat_completions": "/v1/chat/completions"}[native])
        assert all(urlsplit(item["path"]).path == expected_path for item in sends), facts
        if native == "anthropic_messages":
            assert all("messages" in item["body"] and "input" not in item["body"] for item in sends)
        if fault == "cancel":
            assert facts["cancellation_preserved"] and len(sends) == 1 and deltas
        elif fault in {"rate-limit", "unavailable", "pre-header-disconnect"}:
            if native == "google_gemini_generate_content":
                # Production HttpOptions.retry_options is None. The installed
                # official retry_args(None) explicitly stops after one attempt.
                assert len(sends) == 1 and not deltas, facts
                assert response.response_summary["status_code"] == (
                    429 if fault == "rate-limit" else 503 if fault == "unavailable" else None), facts
                assert is_model_failure_summary(response.response_summary)
            else:
                assert len(sends) == 2 and sends[0]["sha256"] == sends[1]["sha256"], facts
                assert response.response_summary["completion_status"] == "complete", facts
                if streaming:
                    assert response.text == "".join(deltas).strip() and len(deltas) == 1, facts
                else:
                    assert response.text and not deltas, facts
        else:
            assert len(sends) == 1, facts
            assert response.response_summary["mode"] == "http_error", facts
            assert is_model_failure_summary(response.response_summary)
            assert not response.tool_calls
            if fault == "forbidden":
                assert response.response_summary["status_code"] == 403 and not deltas
            else:
                assert deltas and response.response_summary["status_code"] is None
                assert response.response_summary["cause_error_type"] in {
                    "RemoteProtocolError", "ReadTimeout", "APIConnectionError", "APITimeoutError",
                    "OpenAIConnectionError", "OpenAITimeoutError"}
                observed = [record for record in caplog.records if record.message == "model_invocation_failed"]
                assert observed and observed[-1].cause_error_type == response.response_summary["cause_error_type"]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["anthropic_messages", "google_gemini_generate_content"])
@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx2.ReadError])
async def test_consumer_network_failure_is_not_misclassified_as_provider_failure(transport, error_type):
    settings = get_settings().model_copy(deep=True)
    settings.model.langchain_model_adapter_enabled = False
    failure = error_type("controlled consumer sink failure")
    with provider(transport, False) as (url, sends, _):
        config = ModelConfigRecord(key="fixture", name="Callback boundary", provider=(
            "google" if transport.startswith("google") else "anthropic"), transport=transport,
            model_id="fixture-model", api_base_url=(
                url.removesuffix("/v1") if transport == "anthropic_messages" else url), api_key=uuid4().hex)
        runtime = ModelRuntimeService(Registry(config), settings)

        async def receive(_delta):
            raise failure

        async with runtime.stream_handler(receive):
            with pytest.raises(error_type) as raised:
                await runtime.invoke("fixture", ModelInvocationRequest(system_prompt="Actual callback boundary.",
                    messages=[{"role": "user", "content": "Return a visible delta."}]))
        assert raised.value is failure and len(sends) == 1

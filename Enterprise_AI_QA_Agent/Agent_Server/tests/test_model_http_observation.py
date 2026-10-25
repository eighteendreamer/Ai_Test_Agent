from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from src.application.model_clients.http_observation import (
    create_observed_http_client,
    observe_provider_call,
)


@contextmanager
def gateway(*, streaming: bool):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(body)
            payload = (b'data: {"delta":"part-1"}\n\ndata: {"delta":"part-2"}\n\n'
                if streaming else b'{"answer":"ok"}')
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Provider-Request-Id", "fixture-request")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/test", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.asyncio
async def test_observation_captures_exact_non_stream_wire_bodies_without_secret_headers():
    with gateway(streaming=False) as (url, requests):
        with observe_provider_call("logical-wire", "openai_chat_completions") as observation:
            client = create_observed_http_client()
            try:
                response = await client.post(url, content=b'{"prompt":"fixture"}',
                    headers={"Authorization": "Bearer secret", "X-Trace": "safe"})
                assert json.loads(response.content) == {"answer": "ok"}
            finally:
                await client.aclose()
        assert requests == [b'{"prompt":"fixture"}']
        assert observation.request_body == b'{"prompt":"fixture"}'
        assert observation.response_body == b'{"answer":"ok"}'
        assert observation.request_headers["x-trace"] == "safe"
        assert "authorization" not in observation.request_headers
        assert {key: observation.response_headers[key] for key in (
            "content-type", "content-length", "x-provider-request-id"
        )} == {
            "content-type": "application/json",
            "content-length": str(len(b'{"answer":"ok"}')),
            "x-provider-request-id": "fixture-request",
        }


@pytest.mark.asyncio
async def test_observation_captures_stream_wire_bytes_after_consumer_reads_stream():
    with gateway(streaming=True) as (url, _):
        with observe_provider_call("logical-stream-wire", "openai_chat_completions") as observation:
            client = create_observed_http_client()
            try:
                async with client.stream("POST", url, content=b"stream-request") as response:
                    chunks = [chunk async for chunk in response.aiter_bytes()]
            finally:
                await client.aclose()
        assert b"".join(chunks) == observation.response_body
        assert observation.response_body.startswith(b"data: ")
        assert observation.response_complete is True

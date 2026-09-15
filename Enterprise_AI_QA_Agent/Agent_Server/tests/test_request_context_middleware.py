from __future__ import annotations

import pytest

from src.api.request_context_middleware import RequestContextMiddleware
from src.core.request_context import get_request_context


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "headers": headers or []}


async def _receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_request_context_generates_and_returns_ids() -> None:
    seen = {}
    sent = []

    async def app(scope, receive, send):
        context = get_request_context()
        seen["context"] = context
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    await RequestContextMiddleware(app)(_scope(), _receive, send)

    assert seen["context"].request_id.startswith("req_")
    assert seen["context"].trace_id.startswith("trace_")
    headers = dict(sent[0]["headers"])
    assert headers[b"x-request-id"] == seen["context"].request_id.encode()
    assert headers[b"x-trace-id"] == seen["context"].trace_id.encode()
    assert get_request_context() is None


@pytest.mark.asyncio
async def test_request_context_forwards_client_ids() -> None:
    seen = {}

    async def app(scope, receive, send):
        seen["context"] = get_request_context()
        await send({"type": "http.response.start", "status": 204, "headers": []})

    async def send(message):
        seen["message"] = message

    await RequestContextMiddleware(app)(
        _scope([(b"x-request-id", b"req-client"), (b"x-trace-id", b"trace-client")]),
        _receive,
        send,
    )

    assert seen["context"].request_id == "req-client"
    assert seen["context"].trace_id == "trace-client"
    assert dict(seen["message"]["headers"]) == {
        b"x-request-id": b"req-client",
        b"x-trace-id": b"trace-client",
    }

"""ASGI middleware that establishes request-level correlation IDs."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.core.request_context import (
    new_id,
    reset_request_context,
    set_request_context,
)


Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class RequestContextMiddleware:
    """Propagate request/trace IDs through one ASGI request."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        request_id = headers.get("x-request-id") or new_id("req")
        trace_id = headers.get("x-trace-id") or new_id("trace")
        token = set_request_context(request_id=request_id, trace_id=trace_id)

        async def send_with_context(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers") or [])
                response_headers.extend(
                    [
                        (b"x-request-id", request_id.encode("latin-1")),
                        (b"x-trace-id", trace_id.encode("latin-1")),
                    ]
                )
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            reset_request_context(token)

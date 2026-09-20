"""ASGI middleware exporting inbound HTTP traffic metrics for Prometheus scrapes.

Counters are resolved through the registry on every request instead of being cached
on the instance, so a registry reset can never leave the middleware writing into
detached metric objects. SSE connection gauges are owned by the session routes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from src.application.observability.metrics import (
    Counter,
    Histogram,
    get_or_create_counter,
    get_or_create_histogram,
)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
LOGGER = logging.getLogger(__name__)

REQUESTS_TOTAL = "qa_http_requests_total"
REQUESTS_TOTAL_HELP = "Inbound HTTP requests by method, route template and status class."
REQUEST_DURATION = "qa_http_request_duration_seconds"
REQUEST_DURATION_HELP = "Inbound HTTP request duration in seconds by method and route template."
UNMATCHED_ROUTE = "unmatched"


def _requests_total() -> Counter:
    return get_or_create_counter(
        REQUESTS_TOTAL,
        REQUESTS_TOTAL_HELP,
        ("method", "route", "status_class"),
    )


def _request_duration() -> Histogram:
    return get_or_create_histogram(
        REQUEST_DURATION,
        REQUEST_DURATION_HELP,
        ("method", "route"),
    )


def route_template(scope: Scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return UNMATCHED_ROUTE


def status_class(status: int) -> str:
    if 100 <= status <= 599:
        return f"{status // 100}xx"
    return "5xx"


class HttpMetricsMiddleware:
    """Record request counts and latency without buffering the response body."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app
        _requests_total()
        _request_duration()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "UNKNOWN")
        started = time.perf_counter()
        response_status: dict[str, int] = {}

        async def send_with_status(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and "status" not in response_status:
                response_status["status"] = int(message.get("status") or 0)
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except BaseException:
            response_status.setdefault("status", 500)
            raise
        finally:
            try:
                self._record(method, scope, response_status, time.perf_counter() - started)
            except Exception:
                LOGGER.exception(
                    "http_metrics_record_failed",
                    extra={"http_path": scope.get("path"), "http_method": method},
                )

    @staticmethod
    def _record(method: str, scope: Scope, response_status: dict[str, int], elapsed: float) -> None:
        route = route_template(scope)
        status = response_status.get("status", 500)
        _requests_total().labels(
            method=method,
            route=route,
            status_class=status_class(status),
        ).inc()
        _request_duration().labels(method=method, route=route).observe(elapsed)

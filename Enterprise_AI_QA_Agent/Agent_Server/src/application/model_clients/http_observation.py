from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Iterator


logger = logging.getLogger(__name__)


@dataclass
class ProviderCallObservation:
    logical_call_id: str
    transport: str
    trace_id: str = ""
    _attempt_sequence: int = 0
    _token: Token[ProviderCallObservation | None] | None = field(default=None, repr=False)

    async def _request(self, request: Any) -> None:
        self._attempt_sequence += 1
        started = time.perf_counter()
        request.extensions["qa_model_http_observation"] = {
            "logical_call_id": self.logical_call_id,
            "transport": self.transport,
            "trace_id": self.trace_id,
            "attempt_sequence": self._attempt_sequence,
            "started": started,
        }
        logger.info("model_http_request_attempt_started", extra={
            "logical_call_id": self.logical_call_id,
            "trace_id": self.trace_id,
            "transport": self.transport,
            "attempt_sequence": self._attempt_sequence,
            "http_method": str(request.method),
            "http_route": _safe_route(str(request.url.path)),
        })

    async def _response(self, response: Any) -> None:
        details = response.request.extensions.get("qa_model_http_observation") or {}
        if details.get("logical_call_id") != self.logical_call_id:
            return
        logger.info("model_http_response_received", extra={
            "logical_call_id": self.logical_call_id,
            "trace_id": self.trace_id,
            "transport": self.transport,
            "attempt_sequence": details.get("attempt_sequence"),
            "http_method": str(response.request.method),
            "http_route": _safe_route(str(response.request.url.path)),
            "status_code": response.status_code,
            "elapsed_ms": round((time.perf_counter() - details["started"]) * 1000, 3),
        })

    @property
    def request_attempt_count(self) -> int:
        return self._attempt_sequence

    def http_event_hooks(self) -> dict[str, list[Any]]:
        return {"request": [self._request], "response": [self._response]}


_current_call: ContextVar[ProviderCallObservation | None] = ContextVar(
    "provider_call_observation", default=None
)


@contextmanager
def observe_provider_call(
    logical_call_id: str,
    transport: str,
    *,
    trace_id: str = "",
) -> Iterator[ProviderCallObservation]:
    observation = ProviderCallObservation(
        logical_call_id=logical_call_id,
        transport=transport,
        trace_id=trace_id,
    )
    token = _current_call.set(observation)
    try:
        yield observation
    finally:
        _current_call.reset(token)


def create_observed_http_client() -> Any | None:
    observation = _current_call.get()
    if observation is None:
        return None
    try:
        import httpx2
    except ModuleNotFoundError as exc:
        if exc.name != "httpx2":
            raise
        logger.error("model_http_observer_unavailable", extra={
            "logical_call_id": observation.logical_call_id,
            "transport": observation.transport,
            "dependency": "httpx2",
        })
        return None
    return httpx2.AsyncClient(event_hooks=observation.http_event_hooks())


def current_provider_request_attempt_count() -> int:
    observation = _current_call.get()
    return observation.request_attempt_count if observation is not None else 0


def _safe_route(path: str) -> str:
    if path.endswith("/chat/completions"):
        return "/chat/completions"
    if path.endswith("/responses"):
        return "/responses"
    if path.endswith("/messages"):
        return "/messages"
    if "/models/" in path and path.endswith(":generateContent"):
        return "/models/{model}:generateContent"
    if "/models/" in path and path.endswith(":streamGenerateContent"):
        return "/models/{model}:streamGenerateContent"
    return "/<redacted>"

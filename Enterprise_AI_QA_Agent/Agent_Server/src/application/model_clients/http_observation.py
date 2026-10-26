from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Iterator

try:
    import httpx2
except ModuleNotFoundError as exc:
    if exc.name != "httpx2":
        raise
    httpx2 = None

try:
    import httpx
except ModuleNotFoundError as exc:
    if exc.name != "httpx":
        raise
    httpx = None


_DEFAULT_BODY_CAPTURE_LIMIT = 8 * 1024 * 1024


class _ObservedAsyncStreamMixin:
    """Tee an SDK response stream without consuming it in the response hook."""

    def __init__(self, stream, on_chunk: Callable[[bytes], None], on_complete: Callable[[bool], None]):
        self._stream = stream
        self._on_chunk = on_chunk
        self._on_complete = on_complete

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                self._on_chunk(bytes(chunk))
                yield chunk
        except BaseException:
            self._on_complete(False)
            raise
        else:
            self._on_complete(True)

    async def aclose(self):
        await self._stream.aclose()


class _ObservedHttpx2AsyncStream(
    _ObservedAsyncStreamMixin, httpx2.AsyncByteStream if httpx2 is not None else object
):
    pass


class _ObservedHttpxAsyncStream(
    _ObservedAsyncStreamMixin, httpx.AsyncByteStream if httpx is not None else object
):
    pass


logger = logging.getLogger(__name__)


@dataclass
class ProviderCallObservation:
    logical_call_id: str
    transport: str
    trace_id: str = ""
    attempt_id: str = ""
    body_capture_limit: int = _DEFAULT_BODY_CAPTURE_LIMIT
    _attempt_sequence: int = 0
    _request_body: bytes | None = field(default=None, repr=False)
    _response_body: bytearray = field(default_factory=bytearray, repr=False)
    _request_body_truncated: bool = False
    _response_body_truncated: bool = False
    _response_complete: bool = False
    _request_headers: dict[str, str] = field(default_factory=dict, repr=False)
    _response_headers: dict[str, str] = field(default_factory=dict, repr=False)
    _response_status_code: int | None = None
    _token: Token[ProviderCallObservation | None] | None = field(default=None, repr=False)

    async def _request(self, request: Any) -> None:
        self._attempt_sequence += 1
        started = time.perf_counter()
        try:
            body = request.content
        except Exception:
            body = None
        if isinstance(body, bytes):
            self._request_body = body[: self.body_capture_limit]
            self._request_body_truncated = len(body) > self.body_capture_limit
        self._request_headers = _safe_headers(request.headers)
        request.extensions["qa_model_http_observation"] = {
            "logical_call_id": self.logical_call_id,
            "transport": self.transport,
            "trace_id": self.trace_id,
            "attempt_id": self.attempt_id,
            "attempt_sequence": self._attempt_sequence,
            "started": started,
        }
        logger.info("model_http_request_attempt_started", extra={
            "logical_call_id": self.logical_call_id,
            "trace_id": self.trace_id,
            "transport": self.transport,
            "attempt_id": self.attempt_id,
            "attempt_sequence": self._attempt_sequence,
            "http_method": str(request.method),
            "http_route": _safe_route(str(request.url.path)),
        })

    async def _response(self, response: Any) -> None:
        details = response.request.extensions.get("qa_model_http_observation") or {}
        if details.get("logical_call_id") != self.logical_call_id:
            return
        self._response_status_code = response.status_code
        self._response_headers = _safe_headers(response.headers)
        if "text/event-stream" in str(response.headers.get("content-type", "")).lower():
            stream_class = (
                _ObservedHttpx2AsyncStream
                if httpx2 is not None and isinstance(response, httpx2.Response)
                else _ObservedHttpxAsyncStream
            )
            response.stream = stream_class(
                response.stream, self._capture_response_chunk, self._complete_response)
        else:
            body = await response.aread()
            self._capture_response_chunk(body)
            self._complete_response(True)
        logger.info("model_http_response_received", extra={
            "logical_call_id": self.logical_call_id,
            "trace_id": self.trace_id,
            "transport": self.transport,
            "attempt_id": self.attempt_id,
            "attempt_sequence": details.get("attempt_sequence"),
            "http_method": str(response.request.method),
            "http_route": _safe_route(str(response.request.url.path)),
            "status_code": response.status_code,
            "elapsed_ms": round((time.perf_counter() - details["started"]) * 1000, 3),
        })

    @property
    def request_attempt_count(self) -> int:
        return self._attempt_sequence

    @property
    def request_body(self) -> bytes | None:
        return self._request_body

    @property
    def response_body(self) -> bytes:
        return bytes(self._response_body)

    @property
    def request_body_truncated(self) -> bool:
        return self._request_body_truncated

    @property
    def response_body_truncated(self) -> bool:
        return self._response_body_truncated

    @property
    def response_complete(self) -> bool:
        return self._response_complete

    @property
    def request_headers(self) -> dict[str, str]:
        return dict(self._request_headers)

    @property
    def response_headers(self) -> dict[str, str]:
        return dict(self._response_headers)

    @property
    def response_status_code(self) -> int | None:
        return self._response_status_code

    def _capture_response_chunk(self, chunk: bytes) -> None:
        remaining = self.body_capture_limit - len(self._response_body)
        if remaining > 0:
            self._response_body.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            self._response_body_truncated = True

    def _complete_response(self, complete: bool) -> None:
        self._response_complete = complete

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
    attempt_id: str = "",
) -> Iterator[ProviderCallObservation]:
    observation = ProviderCallObservation(
        logical_call_id=logical_call_id,
        transport=transport,
        trace_id=trace_id,
        attempt_id=attempt_id,
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
    client_module = httpx if observation.transport == "google_gemini_generate_content" else httpx2
    dependency = "httpx" if client_module is httpx else "httpx2"
    if client_module is None:
        logger.error("model_http_observer_unavailable", extra={
            "logical_call_id": observation.logical_call_id,
            "transport": observation.transport,
            "dependency": dependency,
        })
        return None
    return client_module.AsyncClient(event_hooks=observation.http_event_hooks())


def current_provider_request_attempt_count() -> int:
    observation = _current_call.get()
    return observation.request_attempt_count if observation is not None else 0


def current_provider_call_observation() -> ProviderCallObservation | None:
    """Return the active observation without exposing it in logs or metadata."""
    return _current_call.get()


def _safe_headers(headers: Any) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in headers.items():
        normalized = str(name).lower()
        if any(marker in normalized for marker in ("authorization", "cookie", "token", "api-key", "apikey", "secret")):
            continue
        safe[normalized] = str(value)
    return safe


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

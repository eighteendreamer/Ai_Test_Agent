"""Request-scoped correlation context for API and worker observability."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    trace_id: str


_context: ContextVar[RequestContext | None] = ContextVar(
    "enterprise_ai_qa_request_context", default=None
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def get_request_context() -> RequestContext | None:
    return _context.get()


def set_request_context(*, request_id: str | None = None, trace_id: str | None = None) -> object:
    return _context.set(
        RequestContext(
            request_id=(request_id or new_id("req")),
            trace_id=(trace_id or new_id("trace")),
        )
    )


def reset_request_context(token: object) -> None:
    _context.reset(token)  # type: ignore[arg-type]


def correlation_fields(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    context = get_request_context()
    fields: dict[str, Any] = {}
    if context is not None:
        fields.update(request_id=context.request_id, trace_id=context.trace_id)
    if extra:
        fields.update(extra)
    return fields

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
    session_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    run_item_id: str | None = None
    attempt_id: str | None = None
    worker_id: str | None = None
    resource_id: str | None = None


_context: ContextVar[RequestContext | None] = ContextVar(
    "enterprise_ai_qa_request_context", default=None
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def get_request_context() -> RequestContext | None:
    return _context.get()


def set_request_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    run_id: str | None = None,
    run_item_id: str | None = None,
    attempt_id: str | None = None,
    worker_id: str | None = None,
    resource_id: str | None = None,
) -> object:
    return _context.set(
        RequestContext(
            request_id=(request_id or new_id("req")),
            trace_id=(trace_id or new_id("trace")),
            session_id=session_id,
            turn_id=turn_id,
            run_id=run_id,
            run_item_id=run_item_id,
            attempt_id=attempt_id,
            worker_id=worker_id,
            resource_id=resource_id,
        )
    )


def reset_request_context(token: object) -> None:
    _context.reset(token)  # type: ignore[arg-type]


def correlation_fields(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    context = get_request_context()
    fields: dict[str, Any] = {}
    if context is not None:
        fields.update(request_id=context.request_id, trace_id=context.trace_id)
        for name in (
            "session_id",
            "turn_id",
            "run_id",
            "run_item_id",
            "attempt_id",
            "worker_id",
            "resource_id",
        ):
            value = getattr(context, name)
            if value is not None:
                fields[name] = value
    if extra:
        fields.update(extra)
    return fields

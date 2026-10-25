"""Process-local execution resources, excluded from graph/checkpoint values."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from src.schemas.runtime_binding import RuntimeExecutionOwner


_event_sink: ContextVar[Any | None] = ContextVar("graph_event_sink", default=None)
_framework_owner: ContextVar[RuntimeExecutionOwner | None] = ContextVar("framework_owner", default=None)


@contextmanager
def framework_owner_scope(owner: RuntimeExecutionOwner | None) -> Iterator[None]:
    token = _framework_owner.set(owner)
    try:
        yield
    finally:
        _framework_owner.reset(token)


def current_framework_owner() -> RuntimeExecutionOwner | None:
    return _framework_owner.get()


@contextmanager
def graph_event_scope(sink: Any | None) -> Iterator[None]:
    """Follow the existing model stream context pattern for live event delivery."""
    token = _event_sink.set(sink)
    try:
        yield
    finally:
        _event_sink.reset(token)


def current_graph_event_sink() -> Any | None:
    return _event_sink.get()

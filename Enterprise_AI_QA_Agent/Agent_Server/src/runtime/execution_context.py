"""Process-local execution resources, excluded from graph/checkpoint values."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_event_sink: ContextVar[Any | None] = ContextVar("graph_event_sink", default=None)


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

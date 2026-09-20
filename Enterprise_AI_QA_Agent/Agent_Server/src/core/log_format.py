"""Log formatter that pins every correlation field onto every log line."""

from __future__ import annotations

import logging
import sys

from src.core.request_context import CONTEXT_FIELDS, get_request_context

LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(request_id)s %(trace_id)s %(session_id)s "
    "%(turn_id)s %(task_type)s %(queue)s %(status)s] %(name)s: %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ContextFilter(logging.Filter):
    """Resolve correlation placeholders from the active RequestContext.

    Explicit ``extra`` values win only when the context has no value for the field,
    which keeps the ContextVar authoritative without hiding call-site overrides.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        context = get_request_context()
        for name in CONTEXT_FIELDS:
            value = getattr(context, name, None) if context is not None else None
            if value is None:
                value = record.__dict__.get(name)
            record.__dict__[name] = "" if value is None else str(value)
        record.__dict__.setdefault("status", "")
        return True


def configure_logging(level: int | str = logging.INFO) -> None:
    """Install the correlation formatter on the root logger's handlers.

    The filter is attached to handlers rather than the root logger because logger
    filters do not run for records propagated from child loggers.
    """
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stderr))
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    for handler in root.handlers:
        if not any(isinstance(existing, ContextFilter) for existing in handler.filters):
            handler.addFilter(ContextFilter())
        handler.setFormatter(formatter)
    root.setLevel(level)

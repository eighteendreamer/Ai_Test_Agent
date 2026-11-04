"""Log formatter that pins every correlation field onto every log line."""

from __future__ import annotations

import json
import logging
import sys

from src.core.request_context import CONTEXT_FIELDS, get_request_context

# 开发规范第四节要求的 12 个统一字段，按此顺序逐个渲染；缺任一字段就无法回答
# “哪个 Worker 领取、用了哪个资源、跑到哪一步”。
CORRELATION_FIELDS: tuple[str, ...] = (
    "request_id",
    "trace_id",
    "session_id",
    "turn_id",
    "run_id",
    "run_item_id",
    "attempt_id",
    "worker_id",
    "resource_id",
    "task_type",
    "queue",
    "status",
)

LOG_FORMAT = (
    "%(asctime)s %(levelname)s ["
    + " ".join(f"%({name})s" for name in CORRELATION_FIELDS)
    + "] %(name)s: %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DIAGNOSTIC_FIELDS = (
    "task_id", "monitor_operation", "operation_seconds", "monitor_seconds", "error_type",
    "resource_lease_seconds", "since_resource_renew_seconds", "last_interrupt_poll_seconds",
    "runner_id", "assignment_id", "recovery_source", "recovery_reason",
    "automatic_retry_count", "automatic_retry_limit",
    "artifact_id", "task_status", "execution_id",
    "delivery_message_id", "delivery_stream", "heartbeat_operation", "heartbeat_failure_reason",
    "reclaim_idle_ms", "heartbeat_seconds", "touch_seconds", "successful_touches", "since_last_touch_seconds",
    "framework_release_revision", "framework_release_sha256",
    "binding_id", "owner_epoch", "authority_rejections", "authority_observed_at",
    "session_lease_expires_at", "owner_lease_expires_at", "owner_execution_state", "expected_owner_states",
    "authority_lock_seconds", "authority_check_seconds", "recovery_phase", "retry_count", "not_before_ms",
    "activity_event_count", "activity_boundary_index", "activity_event_id", "activity_event_sequence",
    "activity_status", "activity_observed_at", "activity_execution_returned", "automatic_replay",
    "case_lease_expires_at", "case_lease_remaining_seconds", "case_lease_started_at", "case_heartbeat_at",
    "case_item_status", "case_expected_states",
)


class ContextFormatter(logging.Formatter):
    """Render selected scalar diagnostics, never arbitrary LogRecord extras."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        diagnostics = {}
        for name in DIAGNOSTIC_FIELDS:
            if name in record.__dict__:
                value = record.__dict__[name]
                diagnostics[name] = value if type(value) in (str, int, float, bool, type(None)) else "<unsupported>"
        if diagnostics:
            rendered += " diagnostics=" + json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":"))
        return rendered


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
    formatter = ContextFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    for handler in root.handlers:
        if not any(isinstance(existing, ContextFilter) for existing in handler.filters):
            handler.addFilter(ContextFilter())
        handler.setFormatter(formatter)
    root.setLevel(level)

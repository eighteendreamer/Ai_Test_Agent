"""Opt-in evidence for spawned test Workers; never execution/recovery policy."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from time import monotonic


logger = logging.getLogger(__name__)


def agent_worker_process(settings, prefix, entered=None):
    """Apply the same evidence scope to the original, unbound Agent Worker."""
    with framework_worker_diagnostics() as started:
        from tests.test_agent_task_dispatch import _run_agent_worker

        if started is not None:
            logger.info("framework_test_worker_fixtures_loaded", extra={
                "worker_id": str(os.getpid()), "operation_seconds": monotonic() - started,
            })
        _run_agent_worker(settings, prefix, entered)


def framework_worker_process(settings, prefix, binding, pause_at, recovery_mode=None):
    """Spawn through a light module, then import the real v2 Worker fixtures."""
    with framework_worker_diagnostics() as started:
        # spawn unpickles/imports the target module before invoking the target.
        # Keep the heavy fixture import inside the diagnostic scope.
        from tests.test_framework_task_control import _run_framework_worker

        if started is not None:
            logger.info("framework_test_worker_fixtures_loaded", extra={
                "worker_id": str(os.getpid()), "operation_seconds": monotonic() - started,
            })
        _run_framework_worker(settings, prefix, binding, pause_at, recovery_mode)


@contextmanager
def framework_worker_diagnostics():
    from src.core.log_format import configure_logging

    enabled = os.environ.get("QA_FRAMEWORK_WORKER_DIAGNOSTICS") == "1"
    configure_logging("INFO" if enabled else "WARNING")
    if not enabled:
        yield None
        return

    started, worker_id = monotonic(), str(os.getpid())
    error_type = None
    logger.info("framework_test_worker_entered", extra={"worker_id": worker_id})
    try:
        # No native watchdog: it crashed this CPython build during imports.
        # Keep diagnostics to synchronous phase events (implementation record §107).
        yield started
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        logger.info("framework_test_worker_exited", extra={"worker_id": worker_id,
            "operation_seconds": monotonic() - started, "error_type": error_type})

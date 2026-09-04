"""D3 Worker Pool: worker lifecycle management extracted from CoordinatorRuntimeService.

WorkerPool handles:
- Worker creation (child session spawning)
- Worker dispatch (task assignment)
- Worker monitoring (completion/failure detection)
- Worker timeout (guard against hung workers)
- Failure guard (circuit breaker after consecutive failures)

This is Phase 1 of the CoordinatorRuntimeService decomposition.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class WorkerDispatchSpec:
    """Specification for dispatching a worker."""

    task_id: str
    description: str
    prompt: str
    agent_key: str
    model_key: str | None = None
    skill_keys: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerStatus:
    """Runtime status of a dispatched worker."""

    task_id: str
    child_session_id: str
    agent_key: str
    status: str = "dispatched"  # dispatched | running | completed | failed | timeout | cancelled
    dispatched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class FailureGuard:
    """Circuit breaker: blocks dispatch after N consecutive worker failures."""

    def __init__(self, max_consecutive_failures: int = 3) -> None:
        self._max_consecutive_failures = max_consecutive_failures
        self._consecutive_failures = 0

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def is_blocked(self) -> bool:
        return self._consecutive_failures >= self._max_consecutive_failures

    def reset(self) -> None:
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures


class WorkerPool:
    """Manages worker lifecycle: dispatch, monitor, timeout, cancel.

    Extracted from CoordinatorRuntimeService to provide a clean separation
    between worker infrastructure and orchestration strategy.
    """

    def __init__(
        self,
        max_concurrent_workers: int = 10,
        worker_timeout_seconds: float = 600.0,
        max_consecutive_failures: int = 3,
    ) -> None:
        self._max_concurrent_workers = max_concurrent_workers
        self._worker_timeout_seconds = worker_timeout_seconds
        self._failure_guard = FailureGuard(max_consecutive_failures)
        self._active_workers: dict[str, WorkerStatus] = {}
        self._worker_tasks: dict[str, asyncio.Task] = {}
        self._parent_locks: dict[str, asyncio.Lock] = {}

    def _get_parent_lock(self, parent_session_id: str) -> asyncio.Lock:
        if parent_session_id not in self._parent_locks:
            self._parent_locks[parent_session_id] = asyncio.Lock()
        return self._parent_locks[parent_session_id]

    @property
    def failure_guard(self) -> FailureGuard:
        return self._failure_guard

    @property
    def active_worker_count(self) -> int:
        return len(self._active_workers)

    def can_dispatch(self) -> bool:
        """Check if a new worker can be dispatched."""
        return (
            self.active_worker_count < self._max_concurrent_workers
            and not self._failure_guard.is_blocked()
        )

    def register_worker(self, task_id: str, child_session_id: str, agent_key: str) -> WorkerStatus:
        """Register a newly dispatched worker."""
        status = WorkerStatus(
            task_id=task_id,
            child_session_id=child_session_id,
            agent_key=agent_key,
        )
        self._active_workers[task_id] = status
        return status

    def complete_worker(self, task_id: str, result: dict[str, Any]) -> WorkerStatus | None:
        """Mark a worker as completed with its result."""
        worker = self._active_workers.get(task_id)
        if worker is None:
            return None
        worker.status = "completed"
        worker.completed_at = datetime.now(timezone.utc)
        worker.result = result
        self._failure_guard.record_success()
        return worker

    def fail_worker(self, task_id: str, error: str) -> WorkerStatus | None:
        """Mark a worker as failed."""
        worker = self._active_workers.get(task_id)
        if worker is None:
            return None
        worker.status = "failed"
        worker.completed_at = datetime.now(timezone.utc)
        worker.error = error
        self._failure_guard.record_failure()
        return worker

    def cancel_worker(self, task_id: str, reason: str = "") -> WorkerStatus | None:
        """Cancel a running worker."""
        worker = self._active_workers.get(task_id)
        if worker is None:
            return None
        worker.status = "cancelled"
        worker.completed_at = datetime.now(timezone.utc)
        worker.error = reason
        task = self._worker_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()
        return worker

    def get_worker(self, task_id: str) -> WorkerStatus | None:
        """Get the status of a specific worker."""
        return self._active_workers.get(task_id)

    def get_active_workers(self) -> list[WorkerStatus]:
        """Get all active (non-terminal) workers."""
        return [w for w in self._active_workers.values() if w.status in ("dispatched", "running")]

    def get_completed_workers(self) -> list[WorkerStatus]:
        """Get all completed workers."""
        return [w for w in self._active_workers.values() if w.status == "completed"]

    def cleanup_completed(self) -> None:
        """Remove terminal workers from the active set."""
        terminal_ids = [
            task_id
            for task_id, w in self._active_workers.items()
            if w.status in ("completed", "failed", "cancelled", "timeout")
        ]
        for task_id in terminal_ids:
            del self._active_workers[task_id]
            self._worker_tasks.pop(task_id, None)

    def reset_failure_guard(self) -> None:
        """Reset the failure guard circuit breaker."""
        self._failure_guard.reset()

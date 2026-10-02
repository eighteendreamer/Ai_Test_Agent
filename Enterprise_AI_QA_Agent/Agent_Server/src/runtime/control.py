from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


class RuntimeControlRegistry:
    def __init__(self) -> None:
        self._interrupt_reasons: dict[str, str] = {}
        self._replay_requests: dict[str, int] = defaultdict(int)
        self._interruptible_tasks: dict[str, asyncio.Task[Any]] = {}
        self._execution_owners: dict[str, object] = {}

    @contextmanager
    def execution_scope(self, session_id: str) -> Iterator[None]:
        # No upstream counterpart (checked: RuntimeService, AgentLoop, task registration):
        # cooperative graphs and cancellable adapters need the same invocation lifetime.
        if session_id in self._execution_owners:
            logger.error("runtime_control_execution_already_active", extra={"session_id": session_id})
            raise RuntimeError(f"Session '{session_id}' already has an active runtime execution.")
        owner = object()
        self._execution_owners[session_id] = owner
        self.clear_interrupt(session_id)
        logger.debug("runtime_control_execution_started", extra={"session_id": session_id})
        try:
            yield
        finally:
            if self._execution_owners.get(session_id) is owner:
                self._execution_owners.pop(session_id)
                self.clear_interrupt(session_id)
                logger.debug("runtime_control_execution_released", extra={"session_id": session_id})

    def request_interrupt(self, session_id: str, reason: str = "") -> None:
        if session_id not in self._execution_owners and not self.has_interruptible_task(session_id):
            logger.info("runtime_interrupt_ignored_no_active_execution", extra={"session_id": session_id})
            return
        self._interrupt_reasons[session_id] = reason.strip()
        task = self._interruptible_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()

    def clear_interrupt(self, session_id: str) -> None:
        self._interrupt_reasons.pop(session_id, None)

    def get_interrupt_reason(self, session_id: str) -> str:
        return self._interrupt_reasons.get(session_id, "")

    def is_interrupt_requested(self, session_id: str) -> bool:
        return session_id in self._interrupt_reasons

    def register_interruptible_task(
        self,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        existing = self._interruptible_tasks.get(session_id)
        if existing is not None and not existing.done() and existing is not task:
            raise RuntimeError(
                f"Session '{session_id}' already has an active interruptible runtime task."
            )
        self._interruptible_tasks[session_id] = task
        if self.is_interrupt_requested(session_id) and not task.done():
            task.cancel()

    def unregister_interruptible_task(
        self,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        if self._interruptible_tasks.get(session_id) is task:
            self._interruptible_tasks.pop(session_id, None)
            if session_id not in self._execution_owners:
                self.clear_interrupt(session_id)

    def has_interruptible_task(self, session_id: str) -> bool:
        task = self._interruptible_tasks.get(session_id)
        return task is not None and not task.done()

    def mark_replay_requested(self, session_id: str) -> int:
        self._replay_requests[session_id] += 1
        return self._replay_requests[session_id]

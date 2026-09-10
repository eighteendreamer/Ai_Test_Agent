from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class RuntimeControlRegistry:
    def __init__(self) -> None:
        self._interrupt_reasons: dict[str, str] = {}
        self._replay_requests: dict[str, int] = defaultdict(int)
        self._interruptible_tasks: dict[str, asyncio.Task[Any]] = {}

    def request_interrupt(self, session_id: str, reason: str = "") -> None:
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

    def has_interruptible_task(self, session_id: str) -> bool:
        task = self._interruptible_tasks.get(session_id)
        return task is not None and not task.done()

    def mark_replay_requested(self, session_id: str) -> int:
        self._replay_requests[session_id] += 1
        return self._replay_requests[session_id]

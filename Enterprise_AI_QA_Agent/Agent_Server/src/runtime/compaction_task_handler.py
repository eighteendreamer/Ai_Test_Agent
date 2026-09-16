"""Redis task handler for durable Session hot-memory compaction."""

from __future__ import annotations

from typing import Any

from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.compaction_worker import CompactionWorker
from src.runtime.task_deferred import TaskDeferred


class CompactionTaskHandler:
    def __init__(self, worker: CompactionWorker) -> None:
        self._worker = worker

    async def __call__(self, task: QueuedTask) -> None:
        payload = task.payload
        session_id = str(payload.get("session_id") or "").strip()
        task_id = str(payload.get("task_id") or task.message_id)
        if not session_id:
            raise ValueError(f"Compaction task {task_id} is missing session_id")
        turn_id = str(payload.get("turn_id") or f"compaction:{session_id}")
        trace_id = str(payload.get("trace_id") or f"trace:{task_id}")
        completed = await self._worker.compact_session(
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            context_bundle=_task_context(payload),
        )
        if completed:
            return
        state_reader = getattr(self._worker._hot, "get_compaction_state", None)
        state = await state_reader(session_id) if callable(state_reader) else None
        if isinstance(state, dict) and state.get("status") == "completed":
            # An at-least-once duplicate after another worker committed is safe
            # to ACK; the durable cursor is the authority.
            return
        raise TaskDeferred("Session hot memory compaction is not complete")


def _task_context(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("payload")
    if not isinstance(context, dict):
        return {}
    return {
        key: context[key]
        for key in ("project_id", "environment", "mode_key")
        if context.get(key) is not None
    }

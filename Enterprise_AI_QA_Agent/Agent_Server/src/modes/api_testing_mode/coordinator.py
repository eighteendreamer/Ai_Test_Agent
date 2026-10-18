"""Coordinator: dispatch ready tasks to the executor with concurrency control.

Concurrency rules:
- Read-only tasks can run in parallel (up to max_workers).
- Write tasks run serially.
- Tasks sharing the same resource lock are serialized.
- Auth tasks run first and alone.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from src.modes.api_testing_mode.campaign_state import ApiTestTask, ExecutionPolicy
from src.modes.api_testing_mode.contracts import (
    EXECUTION_MODE_AUTH,
    EXECUTION_MODE_READ,
    EXECUTION_MODE_WRITE,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_RUNNING,
)
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.modes.api_testing_mode.input_binding import (
    apply_input_bindings,
    inject_binding_value,
    unresolved_path_parameters,
)
from src.modes.api_testing_mode.auth_propagation import propagate_auth_ref
from src.modes.api_testing_mode.auth_refresh import (
    AuthRefreshContractError,
    checkpoint as auth_refresh_checkpoint,
    mark_refresh_completed,
    mark_refresh_started,
    mark_replay_finished,
    mark_replay_started,
    prepare_auth_refresh_tasks,
    record_401_pending,
    refresh_is_eligible,
    refresh_targets,
)


# Type alias for the executor callback.
TaskExecutorFn = Callable[[ApiTestTask], Awaitable[ApiTestTask]]
CheckpointCallback = Callable[[str, ApiTestTask, list[ApiTestTask]], None]
logger = logging.getLogger("uvicorn.error.api_testing_mode.coordinator")


class ApiTestCoordinator:
    """Orchestrate task execution respecting concurrency constraints."""

    def __init__(
        self,
        *,
        pool: ApiTaskPool,
        policy: ExecutionPolicy,
        executor_fn: TaskExecutorFn,
        checkpoint_callback: CheckpointCallback | None = None,
    ) -> None:
        self._pool = pool
        self._policy = policy
        self._executor_fn = executor_fn
        self._checkpoint_callback = checkpoint_callback
        self._active_resource_locks: set[str] = set()
        self._write_running: bool = False
        prepare_auth_refresh_tasks(pool.all_tasks)
        self._auth_refresh_targets = refresh_targets(pool.all_tasks)
        self._pending_auth_refresh: list[str] = [
            task.task_id for task in pool.all_tasks
            if auth_refresh_checkpoint(task).get("state") in {
                "pending", "refresh_running", "refresh_completed", "replay_running"
            }
        ]
        # Rebuild from the campaign checkpoint, as the Worker coordinator does.
        # A completed producer must not be sent again just to recover its output.
        self._task_outputs: dict[str, Any] = {
            task.task_id: task.response_body for task in pool.all_tasks if task.status == TASK_COMPLETED
        }
        if self._task_outputs:
            logger.info("api_completed_task_outputs_restored", extra={
                "task_count": len(self._task_outputs),
                "task_ids": list(self._task_outputs),
            })

    async def run_all(self) -> list[ApiTestTask]:
        """Execute all tasks in the pool respecting dependency and concurrency rules.

        Returns the final task list with statuses and results populated.
        """
        while not self._pool.is_complete:
            # A persisted 401 recovery checkpoint owns the target task until
            # refresh/replay is settled. Recover it before the normal
            # dependency pass can expose the target as an ordinary task.
            if self._pending_auth_refresh:
                await self._recover_pending_auth_refresh()
                continue
            self._pool.resolve_blocked()
            batch = self._select_batch()
            if not batch:
                if self._pool.has_running:
                    await asyncio.sleep(0.05)
                    continue
                if self._pending_auth_refresh:
                    await self._recover_pending_auth_refresh()
                    continue
                self._skip_unused_refresh_tasks()
                break

            if len(batch) == 1:
                await self._execute_one(batch[0])
            else:
                await asyncio.gather(*(self._execute_one(task) for task in batch))

            self._pool.resolve_blocked()
            if self._pending_auth_refresh:
                await self._recover_pending_auth_refresh()

        # Retry failed tasks if policy allows.
        if self._policy.max_retries > 0:
            await self._retry_failed()

        return self._pool.all_tasks

    async def _retry_failed(self) -> None:
        """Retry failed tasks up to max_retries (only for retryable failures)."""
        max_retries = self._policy.max_retries
        for _ in range(max_retries):
            retryable = [
                task for task in self._pool.failed_tasks()
                if task.attempts <= max_retries and self._is_retryable(task)
            ]
            if not retryable:
                break
            for task in retryable:
                task.status = "ready"
                task.last_error = ""
            # Re-run the pool.
            while not self._pool.is_complete:
                self._pool.resolve_blocked()
                batch = self._select_batch()
                if not batch:
                    if self._pool.has_running:
                        await asyncio.sleep(0.05)
                        continue
                    break
                if len(batch) == 1:
                    await self._execute_one(batch[0])
                else:
                    await asyncio.gather(*(self._execute_one(task) for task in batch))
                self._pool.resolve_blocked()
                if self._pending_auth_refresh:
                    await self._recover_pending_auth_refresh()

    def _is_retryable(self, task: ApiTestTask) -> bool:
        """Determine if a failed task should be retried."""
        if (task.last_error or "").startswith("Required input binding(s) unresolved:"):
            return False
        if task.request_outcome == "unknown":
            return False
        error = (task.last_error or "").lower()
        # Retry on server errors and timeouts.
        if task.response_status and task.response_status >= 500:
            return True
        if "timeout" in error or "timed out" in error:
            return True
        if "connection" in error:
            return True
        return False

    # ------------------------------------------------------------------
    # Batch selection
    # ------------------------------------------------------------------

    def _select_batch(self) -> list[ApiTestTask]:
        ready = self._pool.ready_tasks()
        if not ready:
            return []

        # Auth tasks always run alone.
        auth_tasks = [t for t in ready if t.execution_mode == EXECUTION_MODE_AUTH]
        if auth_tasks:
            return auth_tasks[:1]

        # If a write task is running, wait.
        if self._write_running:
            return []

        # Collect eligible tasks.
        batch: list[ApiTestTask] = []
        max_workers = max(1, self._policy.max_workers)

        for task in ready:
            if len(batch) >= max_workers:
                break

            if task.execution_mode == EXECUTION_MODE_WRITE:
                if self._policy.write_serial and batch:
                    continue
                if self._has_resource_conflict(task, batch):
                    continue
                batch.append(task)
                break  # Only one write at a time.

            if task.execution_mode == EXECUTION_MODE_READ:
                if self._has_resource_conflict(task, batch):
                    continue
                batch.append(task)

        return batch

    def _has_resource_conflict(self, task: ApiTestTask, batch: list[ApiTestTask]) -> bool:
        if not task.resource_locks:
            return False
        task_locks = set(task.resource_locks)
        if task_locks & self._active_resource_locks:
            return True
        for other in batch:
            if set(other.resource_locks) & task_locks:
                return True
        return False

    # ------------------------------------------------------------------
    # Execution wrapper
    # ------------------------------------------------------------------

    async def _execute_one(self, task: ApiTestTask) -> None:
        # Bind before claiming the task as running. An unresolved placeholder
        # is a planning gap, not an HTTP result and must not hit the target.
        binding_gaps = self._apply_input_bindings(task)
        if binding_gaps:
            self._pool.mark_failed(task.task_id, "Required input binding(s) unresolved: " + "; ".join(binding_gaps))
            self._pool.record_reconciliation(task)
            self._emit_checkpoint("task_failed", task)
            return
        unresolved = unresolved_path_parameters(task)
        if unresolved:
            self._pool.mark_failed(
                task.task_id,
                "Required path parameter(s) unresolved: " + ", ".join(unresolved)
                + "; provide project data or a producer test case before execution.",
            )
            self._emit_checkpoint("task_failed", task)
            return
        self._pool.mark_running(task.task_id)
        self._emit_checkpoint("task_running", task)
        for lock in task.resource_locks:
            self._active_resource_locks.add(lock)
        if task.execution_mode == EXECUTION_MODE_WRITE:
            self._write_running = True

        try:
            result = await self._executor_fn(task)
            if result.status == TASK_COMPLETED:
                self._pool.mark_completed(task.task_id)
                self._pool.record_reconciliation(task)
                self._emit_checkpoint("task_completed", task)
                # Store output for downstream input bindings.
                self._task_outputs[task.task_id] = result.response_body
                # If auth task completed, propagate credential to dependent tasks.
                if result.execution_mode == EXECUTION_MODE_AUTH and result.auth_ref:
                    self._propagate_auth_ref(result.auth_ref, source_task_id=task.task_id)
            else:
                if self._queue_401_recovery(task):
                    self._emit_checkpoint("task_auth_refresh_pending", task)
                else:
                    self._pool.mark_failed(task.task_id, result.last_error or "execution_failed")
                    self._pool.record_reconciliation(task)
                    self._emit_checkpoint("task_failed", task)
        except Exception as exc:
            self._pool.mark_failed(task.task_id, str(exc))
            self._pool.record_reconciliation(task)
            self._emit_checkpoint("task_failed", task)
        finally:
            for lock in task.resource_locks:
                self._active_resource_locks.discard(lock)
            if task.execution_mode == EXECUTION_MODE_WRITE:
                self._write_running = False

    def _emit_checkpoint(self, event_type: str, task: ApiTestTask) -> None:
        if self._checkpoint_callback is None:
            return
        try:
            self._checkpoint_callback(event_type, task, self._pool.all_tasks)
        except Exception:
            return

    def _propagate_auth_ref(self, auth_ref: str, *, source_task_id: str) -> None:
        """Propagate the latest auth session to tasks depending on this auth step.

        A refresh task must replace a credential previously propagated by an
        earlier login. Tasks without this auth dependency retain an explicitly
        configured credential instead of being overwritten by an unrelated
        authentication flow.
        """
        propagate_auth_ref(self._pool.all_tasks, auth_ref, source_task_id=source_task_id)

    def _queue_401_recovery(self, task: ApiTestTask) -> bool:
        if task.task_id not in self._auth_refresh_targets or not refresh_is_eligible(task):
            return False
        record_401_pending(task)
        task.status = "pending"
        task.last_error = ""
        if task.task_id not in self._pending_auth_refresh:
            self._pending_auth_refresh.append(task.task_id)
        logger.info("api_auth_refresh_pending", extra={"task_id": task.task_id,
            "refresh_task_id": task.auth_refresh_task_id, "original_status": 401})
        return True

    async def _recover_pending_auth_refresh(self) -> None:
        pending = list(self._pending_auth_refresh)
        self._pending_auth_refresh.clear()
        for target_id in pending:
            target = self._pool.get(target_id)
            refresh_id = target.auth_refresh_task_id if target else ""
            refresh = self._pool.get(refresh_id) if refresh_id else None
            if target is None or refresh is None:
                continue
            state = auth_refresh_checkpoint(target)
            if state.get("state") in {"replay_completed", "replay_failed"}:
                continue
            if state.get("state") == "replay_running":
                if target.status == TASK_COMPLETED:
                    mark_replay_finished(target)
                    self._emit_checkpoint("task_auth_refresh_replayed", target)
                    continue
                self._pool.mark_failed(target.task_id, "auth_refresh_recovery_unsettled")
                state["state"] = "replay_unsettled"
                target.execution_checkpoint["auth_refresh"] = state
                self._emit_checkpoint("task_failed", target)
                continue

            # A completed refresh is durable evidence. Reuse it instead of
            # sending the refresh request again after a process restart.
            if refresh.status == TASK_COMPLETED:
                if not refresh.auth_ref:
                    self._pool.mark_failed(target.task_id, "auth_refresh_credential_missing")
                    state["state"] = "refresh_failed"
                    target.execution_checkpoint["auth_refresh"] = state
                    self._emit_checkpoint("task_failed", target)
                    continue
                target.auth_ref = refresh.auth_ref
                if state.get("state") != "refresh_completed":
                    mark_refresh_completed(target, refresh)
                    self._emit_checkpoint("task_auth_refresh_completed", target)
            elif state.get("state") == "pending" and refresh.status in {"deferred", "ready"}:
                if any((self._pool.get(dep) is None or self._pool.get(dep).status != "completed")
                       for dep in refresh.depends_on):
                    self._pool.mark_failed(target.task_id, "auth_refresh_prerequisite_unavailable")
                    state["state"] = "refresh_prerequisite_failed"
                    target.execution_checkpoint["auth_refresh"] = state
                    self._emit_checkpoint("task_failed", target)
                    continue
                self._pool.mark_ready(refresh.task_id)
                mark_refresh_started(target)
                await self._execute_one(refresh)
                if refresh.status == TASK_COMPLETED and refresh.auth_ref:
                    target.auth_ref = refresh.auth_ref
                    mark_refresh_completed(target, refresh)
                    self._emit_checkpoint("task_auth_refresh_completed", target)
                else:
                    self._pool.mark_failed(target.task_id, "auth_refresh_failed")
                    state = auth_refresh_checkpoint(target)
                    state["state"] = "refresh_failed"
                    target.execution_checkpoint["auth_refresh"] = state
                    self._emit_checkpoint("task_failed", target)
                    continue
            elif refresh.status == TASK_FAILED:
                self._pool.mark_failed(target.task_id, "auth_refresh_failed")
                state["state"] = "refresh_failed"
                target.execution_checkpoint["auth_refresh"] = state
                self._emit_checkpoint("task_failed", target)
                continue
            else:
                # A refresh already marked running but lacking a settled
                # result has unknown outcome and cannot be resent safely.
                self._pool.mark_failed(target.task_id, "auth_refresh_recovery_unsettled")
                state["state"] = "refresh_unsettled"
                target.execution_checkpoint["auth_refresh"] = state
                self._emit_checkpoint("task_failed", target)
                continue

            self._pool.mark_ready(target.task_id)
            mark_replay_started(target)
            self._emit_checkpoint("task_auth_refresh_replay_started", target)
            await self._execute_one(target)
            mark_replay_finished(target)
            if auth_refresh_checkpoint(target).get("state") == "replay_failed":
                self._pool.mark_failed(target.task_id, target.last_error or "401 replay failed")
            self._emit_checkpoint("task_auth_refresh_replayed", target)

    def _skip_unused_refresh_tasks(self) -> None:
        for task in self._pool.all_tasks:
            if task.status == "deferred":
                self._pool.mark_skipped(task.task_id, "Auth refresh was not needed.")
                self._emit_checkpoint("task_skipped", task)

    def _apply_input_bindings(self, task: ApiTestTask) -> list[str]:
        return apply_input_bindings(task, self._task_outputs)

    def _inject_binding_value(self, task: ApiTestTask, target_path: str, value: Any) -> None:
        # Retained for existing direct callers; execution uses the atomic binder.
        inject_binding_value(task, target_path, value)

__all__ = ["ApiTestCoordinator", "TaskExecutorFn", "CheckpointCallback"]

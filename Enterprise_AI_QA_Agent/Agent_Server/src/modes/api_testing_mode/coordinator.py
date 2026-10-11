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
from src.modes.api_testing_mode.input_binding import bind_path_parameters, substitute_path_parameter


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
        self._task_outputs: dict[str, Any] = {}  # task_id -> response_body

    async def run_all(self) -> list[ApiTestTask]:
        """Execute all tasks in the pool respecting dependency and concurrency rules.

        Returns the final task list with statuses and results populated.
        """
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

    def _is_retryable(self, task: ApiTestTask) -> bool:
        """Determine if a failed task should be retried."""
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
        self._pool.mark_running(task.task_id)
        self._emit_checkpoint("task_running", task)
        for lock in task.resource_locks:
            self._active_resource_locks.add(lock)
        if task.execution_mode == EXECUTION_MODE_WRITE:
            self._write_running = True

        # Apply input bindings: resolve path parameters from completed task outputs.
        self._apply_input_bindings(task)

        try:
            result = await self._executor_fn(task)
            if result.status == TASK_COMPLETED:
                self._pool.mark_completed(task.task_id)
                self._emit_checkpoint("task_completed", task)
                # Store output for downstream input bindings.
                self._task_outputs[task.task_id] = result.response_body
                # If auth task completed, propagate credential to dependent tasks.
                if result.execution_mode == EXECUTION_MODE_AUTH and result.auth_ref:
                    self._propagate_auth_ref(result.auth_ref, source_task_id=task.task_id)
            else:
                self._pool.mark_failed(task.task_id, result.last_error or "execution_failed")
                self._emit_checkpoint("task_failed", task)
        except Exception as exc:
            self._pool.mark_failed(task.task_id, str(exc))
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
        for task in self._pool.all_tasks:
            if task.status in {TASK_COMPLETED, TASK_FAILED, "skipped"}:
                continue
            is_future_auth = task.execution_mode == EXECUTION_MODE_AUTH
            depends_on_source = source_task_id in task.depends_on
            if is_future_auth or depends_on_source:
                task.auth_ref = auth_ref
                logger.info(
                    "api_auth_ref_propagated",
                    extra={
                        "source_task_id": source_task_id,
                        "target_task_id": task.task_id,
                        "dependency_match": depends_on_source,
                    },
                )

    def _apply_input_bindings(self, task: ApiTestTask) -> None:
        """Resolve input bindings: substitute path parameters and request body from prior task outputs."""
        if not task.input_bindings and not task.depends_on:
            return

        # Explicit input bindings.
        for binding in task.input_bindings:
            if not binding.source_task_id:
                continue
            source_output = self._task_outputs.get(binding.source_task_id)
            if source_output is None:
                continue
            value = self._extract_path_value(source_output, binding.source_path)
            if value is None and binding.literal_value is not None:
                value = binding.literal_value
            if value is None:
                continue
            self._inject_binding_value(task, binding.target_path, value)

        # Explicit field mappings take precedence. Infer only placeholders
        # that remain after those mappings have been applied.
        bind_path_parameters(task, self._task_outputs)

    def _extract_path_value(self, obj, path: str):
        """Extract a value from a nested dict using dot-path notation."""
        if not path:
            return obj
        parts = path.replace("[", ".").replace("]", "").split(".")
        current = obj
        for part in parts:
            if not part:
                continue
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return None
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (IndexError, ValueError):
                    return None
            else:
                return None
        return current

    def _inject_binding_value(self, task: ApiTestTask, target_path: str, value) -> None:
        """Inject a value into the task's URL path or request body."""
        if not target_path:
            return
        if target_path.startswith("url.path.") or target_path.startswith("path."):
            param_name = target_path.split(".")[-1]
            substitute_path_parameter(task, param_name, value)
        elif target_path.startswith("body.") or target_path.startswith("request_body."):
            parts = target_path.split(".", 1)
            field_path = parts[1] if len(parts) > 1 else ""
            if field_path:
                if task.request_body is None:
                    task.request_body = {}
                self._set_nested(task.request_body, field_path, value)
        elif target_path.startswith("query."):
            param_name = target_path.split(".", 1)[-1]
            if not task.request_query:
                task.request_query = {}
            task.request_query[param_name] = value

    def _set_nested(self, obj: dict, path: str, value) -> None:
        parts = path.split(".")
        current = obj
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value


__all__ = ["ApiTestCoordinator", "TaskExecutorFn", "CheckpointCallback"]

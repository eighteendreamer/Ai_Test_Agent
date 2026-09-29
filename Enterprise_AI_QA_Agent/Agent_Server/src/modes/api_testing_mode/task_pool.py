"""Task pool: lifecycle management for API test tasks.

Maintains the state machine for each task and provides queries for the
coordinator to pick the next batch of ready tasks.
"""
from __future__ import annotations

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.contracts import (
    TASK_BLOCKED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_PENDING,
    TASK_READY,
    TASK_RUNNING,
    TASK_SKIPPED,
)


class ApiTaskPool:
    """In-memory task pool for one campaign."""

    def __init__(self, tasks: list[ApiTestTask] | None = None) -> None:
        self._tasks: dict[str, ApiTestTask] = {}
        for task in tasks or []:
            self._tasks[task.task_id] = task

    @property
    def all_tasks(self) -> list[ApiTestTask]:
        return list(self._tasks.values())

    @property
    def is_complete(self) -> bool:
        return all(
            task.status in {TASK_COMPLETED, TASK_FAILED, TASK_SKIPPED}
            for task in self._tasks.values()
        )

    @property
    def has_running(self) -> bool:
        return any(task.status == TASK_RUNNING for task in self._tasks.values())

    def ready_tasks(self) -> list[ApiTestTask]:
        return [task for task in self._tasks.values() if task.status == TASK_READY]

    def blocked_tasks(self) -> list[ApiTestTask]:
        return [task for task in self._tasks.values() if task.status == TASK_BLOCKED]

    def running_tasks(self) -> list[ApiTestTask]:
        return [task for task in self._tasks.values() if task.status == TASK_RUNNING]

    def completed_tasks(self) -> list[ApiTestTask]:
        return [task for task in self._tasks.values() if task.status == TASK_COMPLETED]

    def failed_tasks(self) -> list[ApiTestTask]:
        return [task for task in self._tasks.values() if task.status == TASK_FAILED]

    def get(self, task_id: str) -> ApiTestTask | None:
        return self._tasks.get(task_id)

    def mark_running(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.status = TASK_RUNNING

    def mark_completed(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.status = TASK_COMPLETED
            self._reconcile_dependencies()

    def mark_failed(self, task_id: str, error: str = "") -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.status = TASK_FAILED
            task.last_error = error
            self._reconcile_dependencies()

    def mark_skipped(self, task_id: str, reason: str = "") -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.status = TASK_SKIPPED
            task.last_error = reason
            task.dependency_skip_task_id = ""
            self._reconcile_dependencies()

    def resolve_blocked(self) -> int:
        """Promote blocked tasks to ready if all their dependencies are satisfied."""
        return self._reconcile_dependencies()

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {
            TASK_PENDING: 0,
            TASK_BLOCKED: 0,
            TASK_READY: 0,
            TASK_RUNNING: 0,
            TASK_COMPLETED: 0,
            TASK_FAILED: 0,
            TASK_SKIPPED: 0,
        }
        for task in self._tasks.values():
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reconcile_dependencies(self) -> int:
        promoted = 0
        changed = True
        while changed:
            changed = False
            for task in self._tasks.values():
                if task.status not in {TASK_BLOCKED, TASK_PENDING, TASK_SKIPPED}:
                    continue
                if task.status == TASK_SKIPPED and not self._dependency_skip_source(task):
                    continue
                dependencies = [self._tasks[dep_id] for dep_id in task.depends_on if dep_id in self._tasks]
                failed = next(
                    (dep for dep in dependencies if dep.status in {TASK_FAILED, TASK_SKIPPED}),
                    None,
                )
                if task.status == TASK_PENDING and failed is None:
                    continue
                if failed is not None:
                    status = TASK_SKIPPED
                    skip_source = failed.task_id
                    error = f"Skipped because dependency {failed.task_id} failed."
                elif all(dep.status == TASK_COMPLETED for dep in dependencies):
                    status = TASK_READY
                    skip_source = ""
                    error = ""
                else:
                    status = TASK_BLOCKED
                    skip_source = ""
                    error = ""
                if (task.status, task.dependency_skip_task_id, task.last_error) == (status, skip_source, error):
                    continue
                if status == TASK_READY:
                    promoted += 1
                task.status = status
                task.dependency_skip_task_id = skip_source
                task.last_error = error
                changed = True
        return promoted

    @staticmethod
    def _dependency_skip_source(task: ApiTestTask) -> str:
        if task.dependency_skip_task_id:
            return task.dependency_skip_task_id
        # Campaigns saved before this field existed still carry the generated reason.
        for dep_id in task.depends_on:
            if task.last_error == f"Skipped because dependency {dep_id} failed.":
                return dep_id
        return ""


__all__ = ["ApiTaskPool"]

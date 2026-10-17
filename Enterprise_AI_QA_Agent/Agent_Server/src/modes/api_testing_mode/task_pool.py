"""Task pool: lifecycle management for API test tasks.

Maintains the state machine for each task and provides queries for the
coordinator to pick the next batch of ready tasks.
"""
from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
import logging

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

logger = logging.getLogger("uvicorn.error.api_testing_mode.task_pool")


class ApiTaskPool:
    """In-memory task pool for one campaign."""

    def __init__(self, tasks: list[ApiTestTask] | None = None) -> None:
        self._tasks: dict[str, ApiTestTask] = {}
        for task in tasks or []:
            if task.task_id in self._tasks:
                self._reject_graph("duplicate_task", task.task_id)
            self._tasks[task.task_id] = task
        self._validate_dependencies()

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
        self._reconcile_dependencies()
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

    def _reject_graph(self, reason: str, task_id: str = "") -> None:
        logger.warning("api_task_dependency_graph_rejected", extra={
            "reason": reason, "task_id": task_id, "task_count": len(self._tasks),
            "business_execution_started": False,
        })
        raise ValueError(f"API task dependency graph invalid: {reason} ({task_id})")

    def _validate_dependencies(self) -> None:
        # Match validate_run_dependencies: deterministic identities/edges only;
        # business dependencies remain the planner's responsibility.
        graph = {}
        for identity, task in self._tasks.items():
            if not identity or task.task_id != identity:
                self._reject_graph("task_identity_changed", identity)
            if len(set(task.depends_on)) != len(task.depends_on):
                self._reject_graph("duplicate_dependency", identity)
            if any(parent not in self._tasks or parent == identity for parent in task.depends_on):
                self._reject_graph("missing_or_self_dependency", identity)
            if task.reconciliation_for:
                target = self._tasks.get(task.reconciliation_for)
                if target is None or task.reconciliation_for == identity:
                    self._reject_graph("missing_or_self_reconciliation", identity)
                if task.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                    self._reject_graph("reconciliation_must_be_read_only", identity)
            graph[identity] = task.depends_on
        try:
            tuple(TopologicalSorter(graph).static_order())
        except CycleError:
            self._reject_graph("dependency_cycle")

    def _reconcile_dependencies(self) -> int:
        self._validate_dependencies()
        promoted = 0
        changed = True
        while changed:
            changed = False
            for task in self._tasks.values():
                if task.status not in {TASK_BLOCKED, TASK_PENDING, TASK_READY, TASK_SKIPPED}:
                    continue
                if task.status == TASK_SKIPPED and not self._dependency_skip_source(task):
                    continue
                dependencies = [self._tasks[dep_id] for dep_id in task.depends_on]
                if task.reconciliation_for:
                    target = self._tasks[task.reconciliation_for]
                    if target.request_outcome == "unknown" and target.status == TASK_FAILED:
                        if any(dep.status in {TASK_FAILED, TASK_SKIPPED} for dep in dependencies):
                            status, skip_source, error = TASK_SKIPPED, next(
                                dep.task_id for dep in dependencies if dep.status in {TASK_FAILED, TASK_SKIPPED}
                            ), "Reconciliation prerequisite failed."
                        elif all(dep.status == TASK_COMPLETED for dep in dependencies):
                            status, skip_source, error = TASK_READY, "", ""
                        else:
                            status, skip_source, error = TASK_BLOCKED, "", ""
                    else:
                        status, skip_source, error = TASK_BLOCKED, "", ""
                        if target.status in {TASK_COMPLETED, TASK_SKIPPED} or (
                            target.status == TASK_FAILED and target.request_outcome != "unknown"
                        ):
                            status, skip_source, error = TASK_SKIPPED, target.task_id, (
                                f"Skipped because reconciliation target {target.task_id} is not unknown."
                            )
                    if (task.status, task.dependency_skip_task_id, task.last_error) == (status, skip_source, error):
                        continue
                    if status == TASK_READY:
                        promoted += 1
                    logger.info("api_reconciliation_task_status_changed", extra={
                        "task_id": task.task_id, "target_task_id": target.task_id,
                        "previous_status": task.status, "status": status,
                    })
                    task.status, task.dependency_skip_task_id, task.last_error = status, skip_source, error
                    changed = True
                    continue
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
                logger.info("api_task_dependency_status_changed", extra={
                    "task_id": task.task_id, "previous_status": task.status,
                    "status": status, "dependency_task_ids": task.depends_on,
                    "dependency_skip_task_id": skip_source,
                })
                task.status = status
                task.dependency_skip_task_id = skip_source
                task.last_error = error
                changed = True
        return promoted

    def record_reconciliation(self, task: ApiTestTask) -> None:
        """Record an explicit read-only observation without resolving the write."""
        if not task.reconciliation_for:
            return
        target = self._tasks.get(task.reconciliation_for)
        if target is None:
            return
        if task.status == TASK_COMPLETED and task.reconciliation_observation in {"effect_present", "effect_absent"}:
            target.reconciliation_status = task.reconciliation_observation
        else:
            target.reconciliation_status = "inconclusive"
        logger.info("api_reconciliation_observation_recorded", extra={
            "task_id": task.task_id, "target_task_id": target.task_id,
            "target_request_outcome": target.request_outcome,
            "observation": task.reconciliation_observation or "inconclusive",
            "target_remains_unknown": target.request_outcome == "unknown",
        })

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

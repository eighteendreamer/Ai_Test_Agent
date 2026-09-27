from __future__ import annotations

from typing import Any


def matches_security_worker_assignment(
    arguments: dict[str, Any],
    context_bundle: dict[str, Any],
) -> bool:
    if context_bundle.get("dispatch_role") != "security_testing_worker":
        return True
    assigned = context_bundle.get("security_runtime_arguments")
    if not isinstance(assigned, dict):
        return False
    task = assigned.get("task")
    if not isinstance(task, dict):
        return False
    task_id = str(context_bundle.get("security_task_id") or "").strip()
    return bool(task_id) and str(task.get("task_id") or "").strip() == task_id and arguments == assigned

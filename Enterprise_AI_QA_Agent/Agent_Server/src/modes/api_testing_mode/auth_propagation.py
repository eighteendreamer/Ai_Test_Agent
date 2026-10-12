"""Share the verified source-based credential propagation between coordinators."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.contracts import EXECUTION_MODE_AUTH, TASK_COMPLETED, TASK_FAILED, TASK_SKIPPED

logger = logging.getLogger("uvicorn.error.api_testing_mode.coordinator")


def propagate_auth_ref(tasks: Iterable[ApiTestTask], auth_ref: str, *, source_task_id: str) -> None:
    # Existing counterpart: ApiTestCoordinator's live-verified auth propagation.
    # Both coordinators must replace earlier credentials after an explicit refresh.
    for task in tasks:
        if task.status in {TASK_COMPLETED, TASK_FAILED, TASK_SKIPPED}:
            continue
        is_future_auth = task.execution_mode == EXECUTION_MODE_AUTH
        depends_on_source = source_task_id in task.depends_on
        if is_future_auth or depends_on_source:
            task.auth_ref = auth_ref
            logger.info("api_auth_ref_propagated", extra={
                "source_task_id": source_task_id, "target_task_id": task.task_id,
                "dependency_match": depends_on_source})

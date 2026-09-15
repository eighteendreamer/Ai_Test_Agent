"""Bridge durable TestRun execution to Redis without leasing while queued."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from redis.exceptions import RedisError
from src.core.request_context import get_request_context, new_id
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.task_deferred import TaskDeferred
from src.schemas.run_dispatch import RunDispatchAccepted, RunDispatchTask
from src.schemas.run_management import RunClaimRequest, RunItemExecuteRequest
if TYPE_CHECKING:
    from src.application.test_runs.run_service import TestRunService
    from src.application.test_runs.execution_service import TestRunExecutionService
LOGGER = logging.getLogger(__name__)

class RedisExecutionDispatcher:
    def __init__(self, *, queue: RedisTaskQueue, test_run_service: TestRunService, execution_service: TestRunExecutionService) -> None:
        self._queue, self._runs, self._execution = queue, test_run_service, execution_service

    async def dispatch(self, run_id: str) -> RunDispatchAccepted:
        run = await self._runs.get_record(run_id)
        if run.status in {"completed", "cancelled"}:
            raise ValueError("Cannot dispatch a completed or cancelled test run")
        context = get_request_context()
        task = RunDispatchTask(task_id=new_id("task"), request_id=context.request_id if context else new_id("req"), trace_id=context.trace_id if context else new_id("trace"), project_id=run.project_id, session_id=run.session_id, run_id=run.id)
        try:
            message_id = await self._queue.enqueue(task.model_dump(mode="json"))
        except RedisError as exc:
            raise RuntimeError("Redis task dispatch is unavailable; run remains queued") from exc
        LOGGER.info("test_run_dispatched", extra={"task_id": task.task_id, "request_id": task.request_id, "trace_id": task.trace_id, "project_id": run.project_id, "run_id": run.id, "session_id": run.session_id, "message_id": message_id})
        return RunDispatchAccepted(task_id=task.task_id, run_id=run.id, message_id=message_id)

    async def handle(self, task: QueuedTask, *, worker_id: str) -> None:
        command = RunDispatchTask.model_validate(task.payload)
        run = await self._runs.get_record(command.run_id)
        if run.project_id != command.project_id or run.session_id != command.session_id:
            raise ValueError("Dispatch identity does not match stored test run")
        while run.status not in {"completed", "cancelled"}:
            claimed = await self._runs.claim(run.id, RunClaimRequest(worker_id=worker_id, limit=1))
            if not claimed.claims:
                run = await self._runs.get_record(run.id)
                if run.stats.claimed or run.stats.running or run.stats.queued:
                    raise TaskDeferred("Run has outstanding work")
                return
            claim = claimed.claims[0]
            LOGGER.info("dispatch_item_execution_started", extra={"task_id": command.task_id, "request_id": command.request_id, "trace_id": command.trace_id, "project_id": command.project_id, "run_id": run.id, "run_item_id": claim.item.id, "attempt_id": claim.attempt.id, "worker_id": worker_id})
            await self._execution.execute_item(claim.item.id, RunItemExecuteRequest(lease_token=claim.lease_token))
            run = await self._runs.get_record(run.id)

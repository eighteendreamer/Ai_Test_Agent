"""Run only read-only planning, with independent PG and transport ownership."""
from __future__ import annotations

import asyncio
import logging

from src.application.test_cases.planning_workflow import CasePlanningWorkflow
from src.core.request_context import bind_request_context, get_request_context
from src.runtime.task_deferred import TaskDeferred
from src.schemas.case_planning_task import CasePlanningTaskCommand, TERMINAL_PLANNING_TASK_STATUSES

logger = logging.getLogger(__name__)


class PlanningTaskHandler:
    def __init__(self, planner, store, *, worker_id: str):
        self._planner, self._store, self._worker_id = planner, store, worker_id

    async def __call__(self, task):
        command = CasePlanningTaskCommand.model_validate(task.payload)
        context = get_request_context()
        worker_id = (context.worker_id if context else None) or self._worker_id
        claim = await self._store.claim(command.project_id, command.task_id, worker_id=worker_id)
        if claim is None:
            current = await self._store.get(command.project_id, command.task_id)
            if current is None:
                raise ValueError("规划投递没有对应的项目持久任务")
            if current.status in TERMINAL_PLANNING_TASK_STATUSES:
                logger.info("case_planning_terminal_redelivery", extra={"project_id": command.project_id,
                    "task_id": command.task_id, "status": current.status})
                return
            raise TaskDeferred("规划任务仍由其他有效 attempt 执行")
        stop = asyncio.Event()

        async def heartbeat():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._store.lease_seconds / 3)
                except asyncio.TimeoutError:
                    pass
                if not stop.is_set():
                    await self._store.heartbeat(claim)

        async def execute():
            with bind_request_context(project_id=command.project_id, trace_id=command.task_id,
                attempt_id=claim.attempt_id, worker_id=worker_id, task_type="case_planning"):
                async def persist(checkpoint, *, message):
                    await self._store.save_checkpoint(claim, checkpoint, message=message)
                checkpoint = await CasePlanningWorkflow(self._planner).run(claim.seed, claim.checkpoint,
                    deadline_at=claim.task.deadline_at, persist=persist, attempt_id=claim.attempt_id,
                    pin_read_versions=True, remaining_seconds=claim.remaining_seconds)
                await self._store.finish(claim, checkpoint)

        execution, renewal = asyncio.create_task(execute()), asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({execution, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            await execution
        except asyncio.CancelledError:
            logger.warning("case_planning_worker_interrupted", extra={"project_id": command.project_id,
                "task_id": command.task_id, "attempt_id": claim.attempt_id})
            raise
        except Exception as exc:
            logger.error("case_planning_worker_failed", extra={"project_id": command.project_id,
                "task_id": command.task_id, "attempt_id": claim.attempt_id, "error_type": type(exc).__name__})
            raise
        finally:
            stop.set()
            execution.cancel()
            await asyncio.gather(execution, renewal, return_exceptions=True)

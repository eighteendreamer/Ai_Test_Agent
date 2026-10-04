"""Shared leased Worker lifecycle, extracted from DocumentParseTaskHandler."""
from __future__ import annotations

import asyncio
import logging

from src.core.request_context import bind_request_context, get_request_context
from src.runtime.task_deferred import TaskDeferred

logger = logging.getLogger(__name__)


class DocumentOperationTaskHandler:
    def __init__(self, service, store, *, worker_id):
        self._service, self._store, self._worker_id = service, store, worker_id

    async def __call__(self, task):
        command = self._command_type.model_validate(task.payload)
        context = get_request_context()
        worker_id = (context.worker_id if context else None) or self._worker_id
        claim = await self._store.claim(command.project_id, command.task_id, worker_id=worker_id)
        if claim is None:
            current = await self._store.get(command.project_id, command.task_id)
            if current is None:
                raise ValueError(f"{self._label}投递没有对应的项目持久任务")
            if current.status in {"completed", "failed"}:
                logger.info(f"{self._log_prefix}_terminal_redelivery", extra={"task_id": command.task_id, "status": current.status})
                return
            raise TaskDeferred(f"{self._label}任务由其他有效 attempt 执行")
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
                attempt_id=claim.attempt_id, worker_id=worker_id, task_type=self._task_type):
                await self._service.execute(claim)
        execution, renewal = asyncio.create_task(execute()), asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({execution, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            await execution
        except asyncio.CancelledError:
            logger.warning(f"{self._log_prefix}_worker_interrupted", extra={"task_id": command.task_id, "attempt_id": claim.attempt_id})
            raise
        except Exception as exc:
            logger.error(f"{self._log_prefix}_worker_failed", extra={"task_id": command.task_id,
                "attempt_id": claim.attempt_id, "error_type": type(exc).__name__})
            raise
        finally:
            stop.set()
            execution.cancel()
            await asyncio.gather(execution, renewal, return_exceptions=True)

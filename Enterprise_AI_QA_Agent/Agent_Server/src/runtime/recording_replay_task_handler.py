"""Lease fenced delivery for durable recording replay tasks."""
from __future__ import annotations

import asyncio
import logging

from src.core.request_context import bind_request_context, get_request_context
from src.runtime.task_deferred import TaskDeferred
from src.schemas.recording import RecordingReplayCommand

logger = logging.getLogger(__name__)


class RecordingReplayTaskHandler:
    def __init__(self, service, store, *, worker_id: str) -> None:
        self._service = service
        self._store = store
        self._worker_id = worker_id

    async def __call__(self, task) -> None:
        command = RecordingReplayCommand.model_validate(task.payload)
        context = get_request_context()
        worker_id = (context.worker_id if context else None) or self._worker_id
        claim = await self._store.claim(command.project_id, command.replay_id, worker_id=worker_id)
        if claim is None:
            current = await self._store.get_by_id(command.project_id, command.replay_id)
            if current is None:
                raise ValueError("录制回放投递没有对应的持久任务")
            if current.status in {"completed", "failed"}:
                logger.info("recording_replay_terminal_redelivery", extra={
                    "replay_id": command.replay_id, "status": current.status})
                return
            raise TaskDeferred("录制回放任务由其他有效 attempt 执行")

        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._store.lease_seconds / 3)
                except asyncio.TimeoutError:
                    pass
                if not stop.is_set():
                    await self._store.heartbeat(claim)

        async def execute() -> None:
            with bind_request_context(project_id=command.project_id, trace_id=command.replay_id,
                                     attempt_id=claim.attempt_id, worker_id=worker_id,
                                     task_type="recording_replay"):
                await self._service.execute_claim(claim)

        execution = asyncio.create_task(execute())
        renewal = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({execution, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            await execution
        except asyncio.CancelledError:
            logger.warning("recording_replay_worker_interrupted", extra={
                "replay_id": command.replay_id, "attempt_id": claim.attempt_id})
            raise
        except Exception as exc:
            logger.error("recording_replay_worker_failed", extra={
                "replay_id": command.replay_id, "attempt_id": claim.attempt_id,
                "error_type": type(exc).__name__})
            raise
        finally:
            stop.set()
            execution.cancel()
            await asyncio.gather(execution, renewal, return_exceptions=True)

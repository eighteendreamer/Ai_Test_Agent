"""Real PostgreSQL/Redis proof for the durable recording replay Worker."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

from src.application.recorder.replay_executor import ReplayReport, ReplayStepResult
from src.application.recorder.replay_service import RECORDING_REPLAY_STREAM, RecordingReplayService
from src.infrastructure.recording_store import PostgresRecordingStore
from src.infrastructure.recording_replay_store import PostgresRecordingReplayStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.recording_replay_task_handler import RecordingReplayTaskHandler
from src.schemas.recording import RecorderEvent, RecordingReplayCommand, RecordingReplayRequest, RecordingSession, RecordingStatus
from test_execution_worker_cli import isolated_runtime


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_RECORDING_REPLAY_WORKER") != "1",
    reason="requires isolated PostgreSQL and an existing Redis",
)]


class Projects:
    async def require_active(self, project_id):
        if project_id not in {"project-replay-worker", "project-replay-checkpoint"}:
            raise KeyError("project not found")

    async def get(self, project_id):
        return await self.require_active(project_id)


class Executor:
    calls = 0

    async def execute(self, *, recording_id, entry_url, events):
        type(self).calls += 1
        return ReplayReport(recording_id=recording_id, entry_url=entry_url,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc))


class CrashAfterCheckpointExecutor:
    calls = 0

    async def execute_with_checkpoint(self, *, recording_id, entry_url, events,
                                      checkpoint, checkpoint_callback):
        type(self).calls += 1
        if type(self).calls == 1:
            await checkpoint_callback({
                "version": 1,
                "steps": {},
                "in_progress": {"seq": 0, "action": "click"},
            })
            raise asyncio.CancelledError()
        assert checkpoint and checkpoint["in_progress"] == {"seq": 0, "action": "click"}
        report = ReplayReport(recording_id=recording_id, entry_url=entry_url,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc))
        report.steps = [ReplayStepResult(seq=0, action="click", status="failed",
                                         error="recovery_unknown_side_effect")]
        report.recovery_required = {
            "status": "required", "seq": 0, "action": "click",
            "reason": "recovery_unknown_side_effect", "policy": "stop_without_replay",
        }
        return report


@pytest.mark.asyncio
async def test_real_postgres_outbox_redis_worker_replay_is_recoverable_and_terminal_idempotent(tmp_path):
    shared_redis = os.getenv("QA_REPLAY_WORKER_SHARED_REDIS_URL", "redis://127.0.0.1:6379/15")
    async with isolated_runtime(tmp_path, redis_url=shared_redis) as (settings, _processes, _queue, _objects, _owned):
        settings.orchestration.redis_task_outbox_streams = RECORDING_REPLAY_STREAM
        recording_store = PostgresRecordingStore(settings)
        await recording_store.initialize()
        recording_id = str(uuid4())
        await recording_store.create_session(RecordingSession(
            id=recording_id, project_id="project-replay-worker", entry_url="https://example.test",
            status=RecordingStatus.active, created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)))
        await recording_store.append_events(recording_id, [RecorderEvent(seq=0, type="page_scan")])
        await recording_store.update_status(recording_id, RecordingStatus.completed,
            ended_at=datetime.now(timezone.utc))

        replay_store = PostgresRecordingReplayStore(settings)
        await replay_store.initialize()
        outbox = PostgresTaskOutbox(settings)
        await outbox.initialize()
        Executor.calls = 0
        service = RecordingReplayService(recordings=recording_store, projects=Projects(), store=replay_store,
            executor_factory=Executor, dispatch_stream=RECORDING_REPLAY_STREAM)
        request = RecordingReplayRequest(request_id="worker-request", project_id="project-replay-worker")
        submitted = await service.run(recording_id, request)
        assert submitted.status == "running"

        queue = RedisTaskQueue(settings.database.redis_url, stream=RECORDING_REPLAY_STREAM,
            group=f"{settings.orchestration.redis_task_consumer_group}:recording_replay", socket_timeout_seconds=1)
        await queue.connect()
        try:
            assert await outbox.relay_once(queue, limit=1) == 1
            replay_store.lease_seconds = 0.05
            crashed_claim = await replay_store.claim("project-replay-worker",
                submitted.id, worker_id="crashed-before-execution")
            assert crashed_claim is not None
            await asyncio.sleep(0.12)
            worker = RedisTaskWorker(queue, consumer="replay-product-worker",
                handler=RecordingReplayTaskHandler(service, replay_store, worker_id="replay-product-worker"),
                block_ms=0, reclaim_idle_ms=100, task_timeout_seconds=30)
            assert await worker.run_once() == 1
            finished = (await replay_store.list(recording_id))[0]
            assert finished.status == "completed" and finished.report["summary"]["total"] == 0
            assert Executor.calls == 1

            await queue.enqueue(RecordingReplayCommand(task_id=submitted.id, replay_id=submitted.id,
                recording_id=recording_id, project_id="project-replay-worker").model_dump(mode="json"))
            assert await worker.run_once() == 1
            assert Executor.calls == 1
            assert (await replay_store.list(recording_id))[0].status == "completed"
        finally:
            await queue.close()


@pytest.mark.asyncio
async def test_real_postgres_checkpoint_stops_unknown_side_effect_after_attempt_loss(tmp_path):
    shared_redis = os.getenv("QA_REPLAY_WORKER_SHARED_REDIS_URL", "redis://127.0.0.1:6379/15")
    async with isolated_runtime(tmp_path, redis_url=shared_redis) as (settings, _processes, _queue, _objects, _owned):
        recording_store = PostgresRecordingStore(settings)
        await recording_store.initialize()
        recording_id = str(uuid4())
        await recording_store.create_session(RecordingSession(
            id=recording_id, project_id="project-replay-checkpoint", entry_url="https://example.test",
            status=RecordingStatus.active, created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)))
        await recording_store.append_events(recording_id, [RecorderEvent(
            seq=0, type="click", target={"tag": "BUTTON", "locators": {"id": "save"}})])
        await recording_store.update_status(recording_id, RecordingStatus.completed,
            ended_at=datetime.now(timezone.utc))

        replay_store = PostgresRecordingReplayStore(settings)
        await replay_store.initialize()
        outbox = PostgresTaskOutbox(settings)
        await outbox.initialize()
        CrashAfterCheckpointExecutor.calls = 0
        service = RecordingReplayService(
            recordings=recording_store,
            projects=Projects(),
            store=replay_store,
            executor_factory=CrashAfterCheckpointExecutor,
            dispatch_stream=RECORDING_REPLAY_STREAM,
        )
        submitted = await service.run(recording_id, RecordingReplayRequest(
            request_id="checkpoint-loss", project_id="project-replay-checkpoint"))
        replay_store.lease_seconds = 0.05
        first_claim = await replay_store.claim("project-replay-checkpoint", submitted.id,
            worker_id="crashed-worker")
        assert first_claim is not None
        with pytest.raises(asyncio.CancelledError):
            await service.execute_claim(first_claim)
        await asyncio.sleep(0.12)

        second_claim = await replay_store.claim("project-replay-checkpoint", submitted.id,
            worker_id="recovery-worker")
        assert second_claim is not None
        finished = await service.execute_claim(second_claim)
        assert finished.status == "completed"
        assert finished.checkpoint["in_progress"] == {"seq": 0, "action": "click"}
        assert finished.report["recovery_required"]["policy"] == "stop_without_replay"
        assert finished.report["steps"] == [{
            "seq": 0, "action": "click", "strategy": "", "status": "failed",
            "error": "recovery_unknown_side_effect", "elapsed_ms": 0,
        }]
        assert CrashAfterCheckpointExecutor.calls == 2

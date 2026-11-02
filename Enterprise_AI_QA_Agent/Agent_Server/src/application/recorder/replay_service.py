"""Project-scoped recording replay with durable reports and request idempotency."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.application.recorder.replay_executor import RecordingReplayExecutor
from src.schemas.recording import (
    RecordingReplayClaim,
    RecordingReplayRecord,
    RecordingReplayRequest,
    RecordingStatus,
)

RECORDING_REPLAY_STREAM = "qa:tasks:recording_replay"

logger = logging.getLogger(__name__)


class RecordingReplayService:
    def __init__(self, *, recordings, projects, store, executor_factory=None, dispatch_stream: str | None = None) -> None:
        self._recordings = recordings
        self._projects = projects
        self._store = store
        self._executor_factory = executor_factory or (lambda: RecordingReplayExecutor(headless=True))
        self._dispatch_stream = dispatch_stream

    async def run(self, recording_id: str, request: RecordingReplayRequest) -> RecordingReplayRecord:
        await self._projects.require_active(request.project_id)
        session = await self._recordings.get_session(recording_id)
        if session is None or session.project_id != request.project_id:
            raise KeyError("recording not found in project")
        if session.status != RecordingStatus.completed:
            raise ValueError("only completed recordings can be replayed")
        request_hash = hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        pending = RecordingReplayRecord(id=str(uuid4()), recording_id=recording_id,
            project_id=request.project_id, request_id=request.request_id, request_hash=request_hash,
            status="running", created_at=datetime.now(timezone.utc))
        if self._dispatch_stream and hasattr(self._store, "submit"):
            stored, created = await self._store.submit(pending, stream=self._dispatch_stream)
            logger.info("recording_replay_submitted", extra={"recording_id": recording_id,
                "project_id": request.project_id, "replay_id": stored.id,
                "dispatch_stream": self._dispatch_stream, "created_new": created})
            return stored
        stored, created = await self._store.create_or_get(pending)
        if not created:
            return stored
        events = await self._recordings.get_events(recording_id)
        try:
            report = await self._executor_factory().execute(recording_id=recording_id,
                entry_url=session.entry_url, events=events)
            result = await self._store.finish(stored.id, report=report.to_dict())
            logger.info("recording_replay_completed", extra={"recording_id": recording_id,
                "project_id": request.project_id, "replay_id": result.id,
                "failed_steps": result.report["summary"]["failed"] if result.report else None})
            return result
        except Exception as exc:
            logger.exception("recording_replay_failed", extra={"recording_id": recording_id,
                "project_id": request.project_id, "replay_id": stored.id})
            return await self._store.finish(stored.id, error=f"{type(exc).__name__}: {exc}")

    async def execute_claim(self, claim: RecordingReplayClaim) -> RecordingReplayRecord:
        """Execute a claimed replay and publish only through its lease fence."""
        record = claim.record
        session = await self._recordings.get_session(record.recording_id)
        if session is None or session.project_id != record.project_id:
            return await self._store.finish_claim(claim, error="recording not found in project")
        if session.status != RecordingStatus.completed:
            return await self._store.finish_claim(claim, error="only completed recordings can be replayed")
        events = await self._recordings.get_events(record.recording_id)
        try:
            executor = self._executor_factory()
            if hasattr(executor, "execute_with_checkpoint") and hasattr(self._store, "save_checkpoint"):
                async def save_checkpoint(checkpoint: dict) -> None:
                    await self._store.save_checkpoint(claim, checkpoint)

                report = await executor.execute_with_checkpoint(
                    recording_id=record.recording_id, entry_url=session.entry_url, events=events,
                    checkpoint=record.checkpoint, checkpoint_callback=save_checkpoint,
                )
            else:
                report = await executor.execute(recording_id=record.recording_id,
                    entry_url=session.entry_url, events=events)
            result = await self._store.finish_claim(claim, report=report.to_dict())
            logger.info("recording_replay_worker_completed", extra={"recording_id": record.recording_id,
                "project_id": record.project_id, "replay_id": result.id,
                "failed_steps": result.report["summary"]["failed"] if result.report else None})
            return result
        except Exception as exc:
            logger.exception("recording_replay_worker_failed", extra={"recording_id": record.recording_id,
                "project_id": record.project_id, "replay_id": record.id})
            return await self._store.finish_claim(claim, error=f"{type(exc).__name__}: {exc}")

    async def list(self, recording_id: str, project_id: str, limit: int = 20) -> list[RecordingReplayRecord]:
        session = await self._recordings.get_session(recording_id)
        if session is None or session.project_id != project_id:
            raise KeyError("recording not found in project")
        await self._projects.get(project_id)
        return await self._store.list(recording_id, limit)

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.application.recorder.replay_executor import ReplayReport, ReplayStepResult
from src.application.recorder.replay_service import RecordingReplayService
from src.runtime.recording_replay_task_handler import RecordingReplayTaskHandler
from src.schemas.recording import (
    RecordingReplayClaim,
    RecordingReplayRequest,
    RecordingSession,
    RecordingStatus,
)


class Projects:
    async def require_active(self, project_id):
        if project_id != "project-1":
            raise KeyError("project not found")

    async def get(self, project_id):
        return await self.require_active(project_id)


class Recordings:
    session = RecordingSession(id="recording-1", project_id="project-1", entry_url="https://example.test",
        status=RecordingStatus.completed, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))

    async def get_session(self, recording_id):
        return self.session if recording_id == self.session.id else None

    async def get_events(self, recording_id):
        return []


class Store:
    def __init__(self):
        self.by_request = {}

    async def create_or_get(self, record):
        key = (record.recording_id, record.request_id)
        original = self.by_request.get(key)
        if original:
            if original.request_hash != record.request_hash:
                raise ValueError("recording replay request identity reused with different content")
            return original, False
        self.by_request[key] = record
        return record, True

    async def finish(self, replay_id, *, report=None, error=None):
        record = next(value for value in self.by_request.values() if value.id == replay_id)
        record = record.model_copy(update={"status": "failed" if error else "completed",
            "report": report, "error": error, "finished_at": datetime.now(timezone.utc)})
        self.by_request[(record.recording_id, record.request_id)] = record
        return record

    async def list(self, recording_id, limit):
        return [value for value in self.by_request.values() if value.recording_id == recording_id][:limit]


class DispatchStore(Store):
    def __init__(self):
        super().__init__()
        self.lease_seconds = 30
        self.submissions = []
        self.claimed = None
        self.heartbeats = 0

    async def submit(self, record, *, stream):
        self.submissions.append((record, stream))
        return await self.create_or_get(record)

    async def get_by_id(self, project_id, replay_id):
        return next((value for value in self.by_request.values()
                     if value.project_id == project_id and value.id == replay_id), None)

    async def claim(self, project_id, replay_id, *, worker_id):
        record = await self.get_by_id(project_id, replay_id)
        if record is None or self.claimed is not None:
            return None
        self.claimed = RecordingReplayClaim(record=record, worker_id=worker_id,
            attempt_id="attempt-1", lease_token="lease-1", remaining_seconds=30)
        return self.claimed

    async def heartbeat(self, claim):
        self.heartbeats += 1

    async def finish_claim(self, claim, *, report=None, error=None):
        return await self.finish(claim.record.id, report=report, error=error)


class Executor:
    calls = 0

    async def execute(self, *, recording_id, entry_url, events):
        Executor.calls += 1
        report = ReplayReport(recording_id=recording_id, entry_url=entry_url,
            finished_at=datetime.now(timezone.utc))
        report.steps = [ReplayStepResult(seq=1, action="click", status="failed", error="element missing")]
        return report


@pytest.mark.asyncio
async def test_replay_is_project_scoped_persisted_and_idempotent():
    Executor.calls = 0
    store = Store()
    service = RecordingReplayService(recordings=Recordings(), projects=Projects(), store=store,
        executor_factory=Executor)
    request = RecordingReplayRequest(request_id="same-request", project_id="project-1")
    first = await service.run("recording-1", request)
    second = await service.run("recording-1", request)
    assert first == second and Executor.calls == 1
    assert first.status == "completed" and first.report["summary"]["failed"] == 1
    assert (await service.list("recording-1", "project-1"))[0] == first
    with pytest.raises(KeyError):
        await service.list("recording-1", "project-2")


@pytest.mark.asyncio
async def test_registered_dispatch_persists_outbox_intent_without_running_browser():
    Executor.calls = 0
    store = DispatchStore()
    service = RecordingReplayService(recordings=Recordings(), projects=Projects(), store=store,
        executor_factory=Executor, dispatch_stream="qa:tasks:recording_replay")
    result = await service.run("recording-1", RecordingReplayRequest(
        request_id="background-request", project_id="project-1"))
    assert result.status == "running"
    assert len(store.submissions) == 1
    assert store.submissions[0][1] == "qa:tasks:recording_replay"
    assert Executor.calls == 0
    duplicate = await service.run("recording-1", RecordingReplayRequest(
        request_id="background-request", project_id="project-1"))
    assert duplicate == result and len(store.submissions) == 2
    assert Executor.calls == 0


@pytest.mark.asyncio
async def test_replay_worker_claim_executes_once_and_terminal_delivery_does_not_repeat():
    Executor.calls = 0
    store = DispatchStore()
    service = RecordingReplayService(recordings=Recordings(), projects=Projects(), store=store,
        executor_factory=Executor, dispatch_stream="qa:tasks:recording_replay")
    submitted = await service.run("recording-1", RecordingReplayRequest(
        request_id="worker-request", project_id="project-1"))
    handler = RecordingReplayTaskHandler(service, store, worker_id="worker-1")
    task = SimpleNamespace(payload={"task_id": submitted.id, "replay_id": submitted.id,
        "recording_id": submitted.recording_id, "project_id": submitted.project_id})
    await handler(task)
    assert Executor.calls == 1
    assert (await store.get_by_id("project-1", submitted.id)).status == "completed"

    # A redelivered terminal command is acknowledged without opening another browser.
    await handler(task)
    assert Executor.calls == 1

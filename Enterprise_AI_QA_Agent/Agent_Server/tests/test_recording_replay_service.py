from datetime import datetime, timezone

import pytest

from src.application.recorder.replay_executor import ReplayReport, ReplayStepResult
from src.application.recorder.replay_service import RecordingReplayService
from src.schemas.recording import RecordingReplayRequest, RecordingSession, RecordingStatus


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

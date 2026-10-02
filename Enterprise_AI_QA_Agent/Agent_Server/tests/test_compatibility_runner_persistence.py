"""Controlled filesystem contracts, not evidence of a production disk outage."""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.routes.compatibility import router
from src.application.compatibility.runner_service import CompatibilityRunnerService
from src.core.config import OrchestrConfig
from src.modes.compatibility_testing_mode.contracts import (
    CompatibilityDispatchPlan,
    CompatibilityRunnerTask,
    RunnerSelector,
)
from src.schemas.compatibility_runner import (
    CompatibilityArtifactUploadRequest,
    CompatibilityRunnerRegistrationRequest,
    CompatibilityRunnerTaskReportRequest,
    CompatibilityTaskRequeueRequest,
)


def settings_for(root):
    return SimpleNamespace(
        data_dir=str(root / "data"),
        storage=SimpleNamespace(artifact_root_dir=str(root / "artifacts")),
        compatibility_runner_heartbeat_timeout_seconds=120,
    )


def test_runner_uses_nested_orchestration_settings_and_accepts_zero_retry_limit(tmp_path):
    settings = settings_for(tmp_path)
    settings.orchestration = OrchestrConfig(
        compatibility_runner_heartbeat_timeout_seconds=300,
        compatibility_runner_max_automatic_retries=0,
    )
    service = CompatibilityRunnerService(settings=settings)
    assert service._runner_heartbeat_timeout_seconds() == 300
    assert service._orchestration_settings.compatibility_runner_max_automatic_retries == 0
    with pytest.raises(ValidationError):
        OrchestrConfig(compatibility_runner_max_automatic_retries=-1)


def test_runner_report_cannot_override_server_recovery_budget(tmp_path):
    async def exercise():
        service = CompatibilityRunnerService(settings=settings_for(tmp_path))
        await service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"],
        ))
        untrusted_metadata = {
            "automatic_retry_count": 999, "automatic_retry_limit": 999,
            "recovery_reason": "forged", "recovery_required_at": "forged", "recovery_source": "forged",
        }
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="metadata-plan",
            tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local", metadata=untrusted_metadata,
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        assigned = (await service.poll_tasks("runner")).tasks[0]
        assert assigned.metadata["automatic_retry_count"] == 0
        assert assigned.metadata["recovery_reason"] is None
        reported = await service.report_task("runner", "task", CompatibilityRunnerTaskReportRequest(
            status="completed", result={"summary": "Normal dry-run completion."}, metadata=untrusted_metadata,
        ))
        assert reported.metadata["automatic_retry_count"] == 0
        assert reported.metadata["recovery_reason"] is None
    asyncio.run(exercise())


@pytest.mark.parametrize("content", [
    b"{", b"\xff", b"[]", b'{"tasks": {}}',
    b'{"tasks": [{"task_id": "missing-required-fields"}]}',
    b'{"schema_version": 2}',
])
def test_invalid_state_is_preserved_and_prevents_startup(tmp_path, content):
    state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(content)
    with pytest.raises(RuntimeError, match="Compatibility runner state"):
        CompatibilityRunnerService(settings=settings_for(tmp_path))
    assert state_path.read_bytes() == content


def test_duplicate_state_records_are_not_silently_collapsed(tmp_path):
    settings = settings_for(tmp_path)
    service = CompatibilityRunnerService(settings=settings)
    asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(runner_id="runner")))
    state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["runners"].append(state["runners"][0])
    original = json.dumps(state)
    state_path.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Compatibility runner state"):
        CompatibilityRunnerService(settings=settings)
    assert state_path.read_text(encoding="utf-8") == original


def test_atomic_replace_failure_does_not_advance_committed_memory(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    service = CompatibilityRunnerService(settings=settings)
    asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(runner_id="runner")))
    state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
    before = state_path.read_bytes()
    original_replace = type(state_path).replace

    def reject_replace(path, target):
        if path == state_path.with_suffix(".tmp"):
            raise PermissionError("Controlled atomic replace rejection")
        return original_replace(path, target)

    # Separately inject rejection after temporary content was written, to
    # verify the commit boundary without modifying the live catalog path.
    with monkeypatch.context() as patch:
        patch.setattr(type(state_path), "replace", reject_replace)
        with pytest.raises(RuntimeError, match="Compatibility runner state"):
            asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(runner_id="new-runner")))
    assert set(service._runners) == {"runner"}
    assert state_path.read_bytes() == before
    assert not state_path.with_suffix(".tmp").exists()
    asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(runner_id="new-runner")))
    assert set(CompatibilityRunnerService(settings=settings)._runners) == {"runner", "new-runner"}


def test_unserializable_metadata_does_not_leave_an_uncommitted_runner(tmp_path):
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    with pytest.raises(RuntimeError, match="Compatibility runner state"):
        asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", metadata={"controlled_non_json_value": object()},
        )))
    assert service._runners == {}
    assert not (tmp_path / "data" / "compatibility" / "runner_state.json").exists()
    asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(runner_id="runner")))
    assert set(service._runners) == {"runner"}


@pytest.mark.parametrize("operation", ["register", "poll", "report", "requeue", "upload"])
def test_failed_commit_rolls_back_memory_and_api_allows_safe_retry(tmp_path, caplog, operation):
    settings = settings_for(tmp_path)
    service = CompatibilityRunnerService(settings=settings)
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")

    async def enqueue():
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="persistence-plan",
            tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))

    with TestClient(app) as client:
        assert client.post("/api/v1/compatibility/runners/register", json={
            "runner_id": "runner", "capabilities": ["local_browser"],
        }).status_code == 200
        asyncio.run(enqueue())
        if operation in {"report", "requeue", "upload"}:
            assert client.post("/api/v1/compatibility/runners/runner/tasks/poll").status_code == 200
        if operation == "requeue":
            assert client.post("/api/v1/compatibility/runners/runner/tasks/task/report", json={
                "status": "completed", "result": {"summary": "Normal dry-run completion."},
            }).status_code == 200
        requests = {
            "register": ("/runners/register", {"runner_id": "new-runner"}),
            "poll": ("/runners/runner/tasks/poll", None),
            "report": ("/runners/runner/tasks/task/report", {
                "status": "completed", "result": {"summary": "Normal dry-run completion."},
            }),
            "requeue": ("/tasks/requeue", {"task_ids": ["task"], "statuses": ["completed"]}),
            "upload": ("/runners/runner/tasks/task/artifacts/upload", {
                "filename": "evidence.txt", "content_base64": base64.b64encode(b"safe fixture").decode(),
            }),
        }
        suffix, payload = requests[operation]
        state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
        before = state_path.read_bytes()
        # A directory at the temporary file path produces a real local OS
        # write failure. No service method or production file is replaced.
        blocked_path = state_path.with_suffix(".tmp")
        blocked_path.mkdir()
        failed = client.post(f"/api/v1/compatibility{suffix}", json=payload)
        assert failed.status_code == 503, failed.text
        assert failed.json()["detail"] == "Compatibility runner state is unavailable."
        assert state_path.read_bytes() == before
        assert [item.model_dump(mode="json") for item in service._runners.values()] == json.loads(before)["runners"]
        assert [item.model_dump(mode="json") for item in service._tasks.values()] == json.loads(before)["tasks"]
        assert [item.model_dump(mode="json") for item in service._artifacts.values()] == json.loads(before)["artifacts"]
        if operation == "upload":
            assert not [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
        assert "compatibility_runner_state_save_failed" in caplog.text
        blocked_path.rmdir()
        retried = client.post(f"/api/v1/compatibility{suffix}", json=payload)
        assert retried.status_code == 200, retried.text
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert [item.model_dump(mode="json") for item in service._tasks.values()] == saved["tasks"]
        assert [item.model_dump(mode="json") for item in service._runners.values()] == saved["runners"]
    restored = CompatibilityRunnerService(settings=settings)
    assert set(restored._tasks) == set(service._tasks)
    assert set(restored._runners) == set(service._runners)


@pytest.mark.parametrize("recovery", ["restart", "heartbeat"])
@pytest.mark.parametrize("limit", [0, 2])
def test_automatic_retry_budget_preserves_uncertain_attempt_for_review(tmp_path, recovery, limit):
    async def exercise():
        settings = settings_for(tmp_path)
        settings.compatibility_runner_max_automatic_retries = limit
        service = CompatibilityRunnerService(settings=settings)
        await service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"],
        ))
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="retry-budget-plan",
            tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        for attempt in range(limit + 1):
            assert (await service.poll_tasks("runner")).tasks[0].task_id == "task"
            artifact = await service.upload_task_artifact("runner", "task", CompatibilityArtifactUploadRequest(
                filename="evidence.txt", content_base64=base64.b64encode(b"safe evidence").decode(),
            ))
            await service.begin_mode_call_execution("runner", "task")
            await service.finish_mode_call_execution("runner", "task", status="partial", result_count=1)
            before = service._tasks["task"].model_dump(mode="json")
            if recovery == "restart":
                # Controlled process-recovery contract; no Runner is crashed.
                service = CompatibilityRunnerService(settings=settings)
            else:
                # Existing stale-heartbeat fixture, not an observed outage.
                runner = service._runners["runner"]
                service._runners["runner"] = runner.model_copy(update={"heartbeat_at": "2000-01-01T00:00:00Z"})
                await service.list_tasks()
            if attempt < limit:
                assert service._tasks["task"].status == "queued"
                await service.register_runner(CompatibilityRunnerRegistrationRequest(
                    runner_id="runner", capabilities=["local_browser"],
                ))
        paused = service._tasks["task"]
        assert paused.status == "recovery_required"
        assert paused.assigned_runner_id == "runner"
        assert paused.result == before["result"]
        assert [item.model_dump(mode="json") for item in paused.artifacts] == before["artifacts"]
        assert paused.artifacts[0].artifact_id == artifact.artifact_id
        assert paused.metadata["mode_calls_execution_status"] == "partial"
        assert paused.metadata["automatic_retry_count"] == limit
        assert paused.metadata["recovery_reason"] == "automatic_retry_budget_exhausted"
        assert paused.error == before["error"]
        assert (await service.summarize_tasks()).failed == 0
        report = await service.build_report()
        assert report.status == "recovery_required"
        assert report.pending_tasks == 1
        assert [task.status for task in report.recoverable_tasks] == ["recovery_required"]
        assert report.environments[0].status == "recovery_required"
        with pytest.raises(PermissionError, match="requires recovery review"):
            await service.begin_mode_call_execution("runner", "task")
        restored = CompatibilityRunnerService(settings=settings)
        assert restored._tasks["task"].model_dump(mode="json") == paused.model_dump(mode="json")
        await restored.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"],
        ))
        assert (await restored.poll_tasks("runner")).tasks == []
        await restored.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="unrelated-plan",
            tasks=[CompatibilityRunnerTask(
                task_id="unrelated-task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        assert [(task.task_id) for task in (await restored.poll_tasks("runner")).tasks] == ["unrelated-task"]
        await restored.report_task("runner", "unrelated-task", CompatibilityRunnerTaskReportRequest(
            status="completed", result={"summary": "Normal unrelated dry-run completion."},
        ))
        retry = await restored.requeue_tasks(CompatibilityTaskRequeueRequest(
            task_ids=["task"], statuses=["recovery_required"], reason="reviewed_fixture_no_external_side_effects",
        ))
        assert retry.task_ids == ["task"]
        renewed = (await restored.poll_tasks("runner")).tasks[0]
        assert renewed.metadata["automatic_retry_count"] == 0
        assert renewed.metadata["recovery_reason"] is None
        assert renewed.metadata["retry_history"][-1]["previous_artifacts"] == before["artifacts"]
    asyncio.run(exercise())

"""Controlled filesystem contracts, not evidence of a production disk outage."""

import asyncio
import base64
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from urllib import error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.routes.compatibility import router
from src.application.compatibility import runner_client as runner_client_module
from src.application.compatibility.runner_client import CompatibilityRunnerClient
from src.application.compatibility.runner_journal import RunnerJournal
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


@pytest.mark.parametrize("lost_response", ["report", "upload"])
def test_cli_keeps_completed_output_and_remaining_batch_after_lost_response(tmp_path, monkeypatch, lost_response):
    """Controlled lost ACK after a real commit; not a reported provider outage."""
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    executions, requests = [], []
    dropped = False
    with TestClient(app) as http:
        def transport(base_url, path, *, method="GET", payload=None):
            nonlocal dropped
            response = http.request(method, path, json=payload)
            if response.status_code >= 400:
                raise error.HTTPError(path, response.status_code, "API rejected request", {}, None)
            requests.append((path, payload))
            suffix = "/report" if lost_response == "report" else "/artifacts/upload"
            if path.endswith(suffix) and not dropped:
                dropped = True
                raise error.URLError("Controlled loss of a committed response")
            return response.json()

        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        client = CompatibilityRunnerClient(
            base_url="http://test", runner_id="runner", name="Recovery contract", capabilities=["local_browser"],
            devices=[], max_parallel=2, poll_interval=1, executor="dry-run", state_file=tmp_path / "client.sqlite3",
        )
        execute = client.execute_task

        def tracked_execute(task):
            executions.append(task["task_id"])
            return execute(task)

        monkeypatch.setattr(client, "execute_task", tracked_execute)
        client.register()
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="batch-plan", tasks=[CompatibilityRunnerTask(
                task_id=f"task-{index}", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            ) for index in (1, 2)],
        )))
        with pytest.raises(error.URLError):
            client.run_once()
        client.close()
        client = CompatibilityRunnerClient(
            base_url="http://test", runner_id="runner", name="Recovery contract", capabilities=["local_browser"],
            devices=[], max_parallel=2, poll_interval=1, executor="dry-run", state_file=tmp_path / "client.sqlite3",
        )
        monkeypatch.setattr(client, "execute_task", tracked_execute)
        client.run_once()
        assert executions == ["task-1", "task-2"]
        assert service._tasks["task-2"].status == "completed"
        reports = [body for path, body in requests if path.endswith("/report")]
        assert all(body["status"] == "completed" for body in reports)
        if lost_response == "report":
            assert reports[0] == reports[1]
            assert len(service._artifacts) == 2
        else:
            retained = client.journal.entries()
            assert len(retained) == 1
            assert retained[0]["phase"] == "review"
            assert retained[0]["payload"]["status"] == "completed"
            assert service._tasks["task-1"].status == "assigned"
            assert len(service._artifacts) == 2
        client.close()


def test_journal_lock_is_exclusive_across_processes_and_released_on_close(tmp_path):
    path = tmp_path / "runner.sqlite3"
    journal = RunnerJournal(path, base_url="http://test", runner_id="runner")
    code = """import sys
from src.application.compatibility.runner_journal import RunnerJournal
journal = RunnerJournal(sys.argv[1], base_url='http://test', runner_id='runner')
print('opened')
journal.close()
"""
    blocked = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=False, timeout=10)
    assert blocked.returncode != 0
    assert "database is locked" in blocked.stderr
    journal.close()
    opened = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=False, timeout=10)
    assert opened.returncode == 0, opened.stderr
    assert opened.stdout.strip() == "opened"


def test_journal_preserves_bad_file_and_rejects_cross_runner_reuse(tmp_path):
    path = tmp_path / "runner.sqlite3"
    journal = RunnerJournal(path, base_url="http://test", runner_id="runner")
    journal.close()
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="identity/version"):
        RunnerJournal(path, base_url="http://test", runner_id="other")
    assert path.read_bytes() == before
    path.write_bytes(b"controlled invalid SQLite fixture")
    with pytest.raises(sqlite3.DatabaseError):
        RunnerJournal(path, base_url="http://test", runner_id="runner")
    assert path.read_bytes() == b"controlled invalid SQLite fixture"


def test_journal_batch_is_atomic_and_unknown_attempt_blocks_new_assignment(tmp_path):
    journal = RunnerJournal(tmp_path / "runner.sqlite3", base_url="http://test", runner_id="runner")
    task = {"task_id": "task", "metadata": {"assignment_id": "first"}}
    with pytest.raises(ValueError, match="requires"):
        journal.enqueue([task, {"task_id": "missing-assignment"}])
    assert journal.entries() == []
    journal.enqueue([task])
    journal.update("first", "review", reason="unknown_execution")
    journal.enqueue([{**task, "metadata": {"assignment_id": "second"}}])
    assert [entry["phase"] for entry in journal.entries()] == ["review", "review"]
    assert journal.entries()[1]["reason"] == "unresolved_prior_attempt"
    journal.close()


def test_process_exit_retains_unknown_execution_without_replaying_it(tmp_path):
    """Phase recovery contract: the child writes phases, no external tool runs."""
    path = tmp_path / "runner.sqlite3"
    code = """import sys
from src.application.compatibility.runner_journal import RunnerJournal
journal = RunnerJournal(sys.argv[1], base_url='http://test', runner_id='runner')
journal.enqueue([{'task_id': 'task', 'metadata': {'assignment_id': 'first'}}])
journal.update('first', 'executing')
"""
    child = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True,
                           text=True, check=False, timeout=10)
    assert child.returncode == 0, child.stderr

    class RecoveryClient(CompatibilityRunnerClient):
        def heartbeat(self, status="online"):
            pass

        def poll(self):
            return []

        def execute_task(self, task):
            pytest.fail("Unknown execution was replayed")

    client = RecoveryClient(base_url="http://test", runner_id="runner", name="Recovery contract",
                            capabilities=[], devices=[], max_parallel=1, poll_interval=1,
                            executor="dry-run", state_file=path)
    try:
        assert client.run_once() == 0
        assert client.journal.entries()[0]["phase"] == "review"
        assert client.journal.entries()[0]["reason"] == "unknown_executing"
    finally:
        client.close()


def test_saved_queued_assignment_is_checked_before_execution_after_manual_retry(tmp_path, monkeypatch):
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as http:
        def transport(base_url, path, *, method="GET", payload=None):
            response = http.request(method, path, json=payload)
            response.raise_for_status()
            return response.json()

        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        client = CompatibilityRunnerClient(
            base_url="http://test", runner_id="runner", name="Normal retry contract", capabilities=["local_browser"],
            devices=[], max_parallel=1, poll_interval=1, executor="dry-run", state_file=tmp_path / "client.sqlite3",
        )
        try:
            client.register()
            asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
                plan_id="queued-plan", tasks=[CompatibilityRunnerTask(
                    task_id="task", environment_id="local",
                    runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
                )],
            )))
            client.journal.enqueue(client.poll())
            asyncio.run(service.requeue_tasks(CompatibilityTaskRequeueRequest(
                task_ids=["task"], statuses=["assigned"], reason="normal_manual_retry",
            )))
            current = asyncio.run(service.poll_tasks("runner")).tasks[0].model_dump(mode="json")
            assert client.run_once() == 0
            assert client.journal.entries()[0]["phase"] == "conflict"
            assert service._tasks["task"].model_dump(mode="json") == current
            assert service._artifacts == {}
        finally:
            client.close()


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


@pytest.mark.parametrize("operation", ["report", "upload", "mode_calls"])
def test_previous_assignment_cannot_write_to_same_runner_retry(tmp_path, operation):
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    # The task has no mode calls; runtime objects are unused success fixtures.
    app.state.tool_runtime_service = object()
    app.state.tool_registry = object()
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        prefix = "/api/v1/compatibility"
        assert client.post(f"{prefix}/runners/register", json={
            "runner_id": "runner", "capabilities": ["local_browser"],
            "metadata": {"assignment_fencing_version": 1},
        }).status_code == 200
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="assignment-plan",
            tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        )))
        first = client.post(f"{prefix}/runners/runner/tasks/poll").json()["tasks"][0]
        assert client.post(f"{prefix}/tasks/requeue", json={
            "task_ids": ["task"], "statuses": ["assigned"], "reason": "normal_manual_retry",
        }).status_code == 200
        second = client.post(f"{prefix}/runners/runner/tasks/poll").json()["tasks"][0]
        first_id = first["metadata"].get("assignment_id")
        second_id = second["metadata"].get("assignment_id")

        def submit(assignment_id):
            if operation == "report":
                return client.post(f"{prefix}/runners/runner/tasks/task/report", json={
                    "assignment_id": assignment_id, "status": "completed", "result": {"summary": "Dry-run result."},
                })
            if operation == "upload":
                return client.post(f"{prefix}/runners/runner/tasks/task/artifacts/upload", json={
                    "assignment_id": assignment_id, "filename": "evidence.txt",
                    "content_base64": base64.b64encode(b"safe evidence").decode(),
                })
            params = {"assignment_id": assignment_id} if assignment_id is not None else {}
            return client.post(f"{prefix}/runners/runner/tasks/task/mode-calls/execute", params=params)

        # Ordinary manual retry with a delayed first result; no method, clock,
        # filesystem or execution exception is replaced to produce failure.
        stale = submit(first_id)
        assert stale.status_code == 409, stale.text
        assert first_id and second_id and first_id != second_id
        assert service._tasks["task"].model_dump(mode="json") == second
        assert submit(None).status_code == 409
        assert submit(second_id).status_code == 200


def test_cli_transmits_assignment_identity_through_formal_api(tmp_path, monkeypatch):
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.state.tool_runtime_service = object()
    app.state.tool_registry = object()
    app.include_router(router, prefix="/api/v1")
    calls = []
    with TestClient(app) as http:
        def transport(base_url, path, *, method="GET", payload=None):
            calls.append((path, payload))
            response = http.request(method, path, json=payload)
            response.raise_for_status()
            return response.json()

        # Successful ASGI transport only; the CLI, API and service are real.
        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        client = CompatibilityRunnerClient(
            base_url="http://test", runner_id="runner", name="Protocol fixture", capabilities=["local_browser"],
            devices=[], max_parallel=1, poll_interval=1, executor="dry-run",
        )
        client.register()
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="cli-plan", tasks=[CompatibilityRunnerTask(
                task_id="cli-task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        )))
        assert client.run_once() == 1
        task = service._tasks["cli-task"]
        assert task.status == "completed"
        assert task.metadata["assignment_fencing_required"] is True
        assignment_id = task.metadata["assignment_id"]
        for suffix in ("/report", "/artifacts/upload"):
            transmitted = next(body for path, body in calls if path.endswith(suffix))
            assert transmitted["assignment_id"] == assignment_id
        # Empty approved mode-call list: verifies query transport with no tool
        # execution, browser, model or provider request.
        assert client.execute_mode_calls(task.model_dump(mode="json"))["status"] == "completed"
        assert calls[-1][0].endswith("?assignment_id=" + assignment_id)


def test_same_assignment_identical_report_is_idempotent_but_cannot_change_terminal_result(tmp_path):
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        prefix = "/api/v1/compatibility"
        assert client.post(f"{prefix}/runners/register", json={
            "runner_id": "runner", "capabilities": ["local_browser"],
            "metadata": {"assignment_fencing_version": 1},
        }).status_code == 200
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="report-receipt-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        )))
        task = client.post(f"{prefix}/runners/runner/tasks/poll").json()["tasks"][0]
        payload = {
            "assignment_id": task["metadata"]["assignment_id"], "status": "completed",
            "result": {"summary": "Normal dry-run result."},
            "artifacts": [{"type": "runner_log", "uri": "artifact://fixture/evidence", "label": "safe evidence"}],
        }
        path = f"{prefix}/runners/runner/tasks/task/report"
        first = client.post(path, json=payload)
        assert first.status_code == 200
        state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
        committed = state_path.read_bytes()
        # Two normal submissions; no dropped response or network failure is
        # fabricated. This is the result retransmission protocol contract.
        repeated = client.post(path, json=payload)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == first.json()
        assert state_path.read_bytes() == committed
        assert len(service._artifacts) == 1
        different = client.post(path, json={**payload, "result": {"summary": "Changed result."}})
        assert different.status_code == 409
        assert state_path.read_bytes() == committed
        # The receipt survives reload but never authorizes an old assignment.
        app.state.compatibility_runner_service = CompatibilityRunnerService(settings=settings_for(tmp_path))
        assert client.post(path, json=payload).json() == first.json()
        assert client.post(f"{prefix}/tasks/requeue", json={
            "task_ids": ["task"], "statuses": ["completed"],
        }).status_code == 200
        client.post(f"{prefix}/runners/runner/tasks/poll")
        assert client.post(path, json=payload).status_code == 409


def test_mode_finish_from_previous_assignment_does_not_modify_new_attempt(tmp_path):
    async def exercise():
        service = CompatibilityRunnerService(settings=settings_for(tmp_path))
        await service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"],
        ))
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="finish-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        original = (await service.poll_tasks("runner")).tasks[0]
        await service.begin_mode_call_execution("runner", "task")
        await service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"]))
        current = (await service.poll_tasks("runner")).tasks[0]
        with pytest.raises(PermissionError, match="assignment no longer matches"):
            await service.finish_mode_call_execution(
                "runner", "task", status="completed", result_count=1,
                assignment_id=original.metadata["assignment_id"],
            )
        assert service._tasks["task"].model_dump(mode="json") == current.model_dump(mode="json")
    asyncio.run(exercise())


def test_upload_checks_assignment_again_after_storage_await(tmp_path):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class DelayedStorage:
            enabled = True

            def __init__(self):
                self.deleted = []

            async def store_uploaded_bytes(self, **kwargs):
                started.set()
                await release.wait()
                return {"uri": "rustfs://fixture/old-artifact"}

            async def delete_object_uri(self, uri):
                self.deleted.append(uri)

        storage = DelayedStorage()
        service = CompatibilityRunnerService(settings=settings_for(tmp_path), artifact_storage_service=storage)
        await service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"],
        ))
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="upload-race-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        await service.poll_tasks("runner")
        # Controlled storage-await scheduling; no real RustFS fault is claimed.
        upload = asyncio.create_task(service.upload_task_artifact("runner", "task", CompatibilityArtifactUploadRequest(
            filename="evidence.txt", content_base64=base64.b64encode(b"safe evidence").decode(),
        )))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            await service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"]))
            current = (await service.poll_tasks("runner")).tasks[0]
        finally:
            release.set()
        with pytest.raises(PermissionError, match="assignment no longer matches"):
            await upload
        assert service._tasks["task"].model_dump(mode="json") == current.model_dump(mode="json")
        assert storage.deleted == ["rustfs://fixture/old-artifact"]
        assert service._artifacts == {}
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

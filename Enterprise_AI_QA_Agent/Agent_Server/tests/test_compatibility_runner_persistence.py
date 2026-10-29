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
from src.application.compatibility.runner_service import CompatibilityRunnerReportObserved, CompatibilityRunnerService
from src.core.config import OrchestrConfig
from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT
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


@pytest.mark.parametrize("lost_response", ["report", "upload", "legacy-upload"])
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
            body = response.json()
            if lost_response == "legacy-upload" and path.endswith("/register"):
                # Old server has no capability announcement; never infer safe replay.
                body["metadata"].pop("artifact_upload_idempotency_version", None)
            return body

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
        client.register()
        client.run_once()
        assert executions == ["task-1", "task-2"]
        assert service._tasks["task-2"].status == "completed"
        reports = [body for path, body in requests if path.endswith("/report")]
        assert all(body["status"] == "completed" for body in reports)
        if lost_response == "report":
            assert reports[0] == reports[1]
            assert len(service._artifacts) == 2
        elif lost_response == "upload":
            assert client.journal.entries() == []
            assert service._tasks["task-1"].status == "completed"
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


def test_identical_identified_upload_reuses_persisted_artifact_without_duplicate_storage(tmp_path):
    """Two normal API calls; no dropped response or execution fault."""
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as http:
        prefix = "/api/v1/compatibility"
        http.post(f"{prefix}/runners/register", json={"runner_id": "runner", "capabilities": ["local_browser"]})
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="upload-receipt-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        )))
        task = http.post(f"{prefix}/runners/runner/tasks/poll").json()["tasks"][0]
        payload = {"filename": "safe.txt", "content_base64": base64.b64encode(b"safe evidence").decode(),
                   "assignment_id": task["metadata"]["assignment_id"], "upload_id": "runner-log"}
        path = f"{prefix}/runners/runner/tasks/task/artifacts/upload"
        first = http.post(path, json=payload)
        second = http.post(path, json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert len(service._artifacts) == 1
        assert len(list((tmp_path / "artifacts" / "compatibility").rglob("artifact_*"))) == 1
        assert http.post(f"{prefix}/runners/runner/tasks/task/report", json={
            "assignment_id": payload["assignment_id"], "status": "completed",
        }).status_code == 200
        # Startup deliberately revokes unfinished assignments. A completed
        # assignment retains its receipt; do not change that recovery rule.
        app.state.compatibility_runner_service = CompatibilityRunnerService(settings=settings_for(tmp_path))
        assert http.post(path, json=payload).json() == first.json()
        assert http.post(path, json={**payload, "content_base64": base64.b64encode(b"changed").decode()}).status_code == 409
        assert http.post(path, json={**payload, "assignment_id": None}).status_code == 400
        assert http.post(f"{prefix}/tasks/requeue", json={"task_ids": ["task"], "statuses": ["completed"]}).status_code == 200
        http.post(f"{prefix}/runners/runner/tasks/poll")
        assert http.post(path, json=payload).status_code == 409


def test_concurrent_identical_uploads_share_one_receipt_and_compensate_only_loser(tmp_path):
    async def exercise():
        ready, release = asyncio.Event(), asyncio.Event()

        class Storage:
            enabled = True

            def __init__(self):
                self.stored, self.deleted = [], []

            async def store_uploaded_bytes(self, **kwargs):
                uri = "rustfs://fixture/" + kwargs["object_prefix"] + "/" + kwargs["filename"]
                self.stored.append(uri)
                if len(self.stored) == 2:
                    ready.set()
                await release.wait()
                return {"uri": uri}

            async def delete_object_uri(self, uri):
                self.deleted.append(uri)

        storage = Storage()
        service = CompatibilityRunnerService(settings=settings_for(tmp_path), artifact_storage_service=storage)
        await service.register_runner(CompatibilityRunnerRegistrationRequest(runner_id="runner", capabilities=["local_browser"]))
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(plan_id="race-plan", tasks=[CompatibilityRunnerTask(
            task_id="task", environment_id="local",
            runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
        )]))
        task = (await service.poll_tasks("runner")).tasks[0]
        payload = CompatibilityArtifactUploadRequest(
            filename="safe.txt", content_base64=base64.b64encode(b"safe evidence").decode(),
            assignment_id=task.metadata["assignment_id"], upload_id="runner-log",
        )
        calls = [asyncio.create_task(service.upload_task_artifact("runner", "task", payload)) for _ in range(2)]
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
        finally:
            release.set()
        first, second = await asyncio.gather(*calls)
        assert first == second
        assert len(service._artifacts) == 1
        assert len(set(storage.stored)) == 2
        assert len(storage.deleted) == 1
        assert first.uri not in storage.deleted
        assert storage.deleted[0] in storage.stored
    asyncio.run(exercise())


def test_old_server_cannot_echo_client_metadata_as_upload_capability(monkeypatch):
    def transport(base_url, path, *, method="GET", payload=None):
        return {"runner_id": "runner", "status": "online", "metadata": payload["metadata"]}

    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    client = CompatibilityRunnerClient(
        base_url="http://test", runner_id="runner", name="Old server", capabilities=[], devices=[],
        max_parallel=1, poll_interval=1, executor="dry-run", metadata={"artifact_upload_idempotency_version": 1},
    )

    try:
        client.register()
        assert client._idempotent_uploads is False
    finally:
        client.close()


def test_identified_poll_returns_same_batch_after_reregistration(tmp_path):
    """Ordinary repeated requests establish the new poll receipt contract."""
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as http:
        prefix = "/api/v1/compatibility"
        registration = {"runner_id": "runner", "capabilities": ["local_browser"], "max_parallel": 2}
        registered = http.post(f"{prefix}/runners/register", json=registration)
        assert registered.status_code == 200
        epoch = registered.json()["metadata"]["poll_epoch"]
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="poll-receipt-plan", tasks=[CompatibilityRunnerTask(
                task_id=f"task-{index}", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            ) for index in (1, 2)],
        )))
        path = f"{prefix}/runners/runner/tasks/poll?limit=2&request_id=first-request&sequence=1&epoch={epoch}"
        first = http.post(path)
        assert first.status_code == 200
        assert len(first.json()["tasks"]) == 2
        assert http.post(f"{prefix}/runners/register", json=registration).status_code == 200
        repeated = http.post(path)
        assert repeated.status_code == 200
        assert repeated.json() == first.json()
        assert len(service._tasks) == 2
        committed = service._state_path.read_bytes()
        assert http.post(path).json() == first.json()
        assert service._state_path.read_bytes() == committed
        assert http.post(path.replace("limit=2", "limit=1")).status_code == 409
        assert http.post(path.replace("first-request", "different-request")).status_code == 409
        assert http.post(path.replace("&sequence=1", "")).status_code == 400
        assert http.post(f"{prefix}/runners/runner/heartbeat", json={"metadata": {
            "poll_receipt": None, "poll_next_sequence": 999, "poll_epoch": "forged",
            "poll_request_idempotency_version": 0,
        }}).status_code == 200
        assert http.post(path).json() == first.json()
        for task in first.json()["tasks"]:
            assert http.post(f"{prefix}/runners/runner/tasks/{task['task_id']}/report", json={
                "assignment_id": task["metadata"]["assignment_id"], "status": "completed",
            }).status_code == 200
        next_path = path.replace("first-request", "second-request").replace("sequence=1", "sequence=2")
        assert http.post(next_path).json()["tasks"] == []
        assert http.post(next_path).json()["tasks"] == []
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(plan_id="next-plan", tasks=[CompatibilityRunnerTask(
            task_id="task-3", environment_id="local",
            runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
        )])))
        assert http.post(path).status_code == 409
        assert service._tasks["task-3"].status == "queued"
        # Normal service re-open preserves receipts; no clock or file is corrupted.
        app.state.compatibility_runner_service = CompatibilityRunnerService(settings=settings_for(tmp_path))
        registered = http.post(f"{prefix}/runners/register", json=registration).json()
        assert registered["metadata"]["poll_epoch"] == epoch
        assert registered["metadata"]["poll_next_sequence"] == 3
        assert http.post(next_path).json()["tasks"] == []
        third_path = path.replace("first-request", "third-request").replace("sequence=1", "sequence=3")
        assert [task["task_id"] for task in http.post(third_path).json()["tasks"]] == ["task-3"]


def test_cli_recovers_committed_poll_after_lost_response_without_reexecution(tmp_path, monkeypatch):
    """Controlled loss after formal poll commit; no product executor fails."""
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    dropped = False
    polls = []
    with TestClient(app) as http:
        def transport(base_url, path, *, method="GET", payload=None):
            nonlocal dropped
            response = http.request(method, path, json=payload)
            if response.status_code >= 400:
                raise error.HTTPError(path, response.status_code, "API rejected request", {}, None)
            if "/tasks/poll?" in path:
                polls.append(path)
            if "/tasks/poll?" in path and not dropped:
                dropped = True
                raise error.URLError("Controlled loss of committed poll response")
            return response.json()

        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        options = {"base_url": "http://test", "runner_id": "runner", "name": "Poll recovery",
                   "capabilities": ["local_browser"], "devices": [], "max_parallel": 2,
                   "poll_interval": 1, "executor": "dry-run", "state_file": tmp_path / "client.sqlite3"}
        client = CompatibilityRunnerClient(**options)
        client.register()
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="lost-poll-plan", tasks=[CompatibilityRunnerTask(
                task_id=f"task-{index}", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            ) for index in (1, 2)],
        )))
        with pytest.raises(error.URLError):
            client.run_once()
        assert all(task.status == "assigned" for task in service._tasks.values())
        client.close()
        client = CompatibilityRunnerClient(**options)
        try:
            client.register()
            assert client.run_once() == 2
            assert all(task.status == "completed" for task in service._tasks.values())
            assert len(service._artifacts) == 2
            assert client.journal.entries() == []
            assert client.journal.pending_poll() is None
            assert polls[0] == polls[1]
        finally:
            client.close()


def test_pending_poll_is_not_acknowledged_before_batch_transaction_commits(tmp_path):
    """Controlled invalid protocol batch verifies the transaction boundary."""
    path = tmp_path / "client.sqlite3"
    journal = RunnerJournal(path, base_url="http://test", runner_id="runner")
    pending = journal.begin_poll(limit=2, sequence=1, epoch="epoch")
    task = {"task_id": "task", "metadata": {"assignment_id": "assignment"}}
    try:
        with pytest.raises(ValueError, match="requires"):
            journal.complete_poll(pending["request_id"], [task, {"task_id": "missing-identity"}])
        assert journal.entries() == []
        assert journal.pending_poll() == pending
    finally:
        journal.close()
    journal = RunnerJournal(path, base_url="http://test", runner_id="runner")
    try:
        assert journal.pending_poll() == pending
        journal.complete_poll(pending["request_id"], [task])
        assert len(journal.entries()) == 1
        assert journal.pending_poll() is None
    finally:
        journal.close()


def test_recreated_runner_rejects_poll_from_previous_registration_epoch(tmp_path):
    service = CompatibilityRunnerService(settings=settings_for(tmp_path))
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as http:
        prefix = "/api/v1/compatibility"
        first = http.post(f"{prefix}/runners/register", json={"runner_id": "runner"}).json()
        epoch = first["metadata"]["poll_epoch"]
        path = f"{prefix}/runners/runner/tasks/poll?limit=1&request_id=old&sequence=1&epoch={epoch}"
        assert http.post(path).status_code == 200
        assert http.post(f"{prefix}/runners/runner/heartbeat", json={"status": "offline"}).status_code == 200
        deleted = http.post(f"{prefix}/runners/cleanup", json={"runner_ids": ["runner"], "older_than_seconds": 0})
        assert deleted.json()["runner_ids"] == ["runner"]
        recreated = http.post(f"{prefix}/runners/register", json={"runner_id": "runner"}).json()
        assert recreated["metadata"]["poll_epoch"] != epoch
        assert http.post(path).status_code == 409


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
        bridge_results = []
        execute_task = client.execute_task

        def execute_with_empty_bridge(task):
            # Exercise query transport while the original assignment is live;
            # a completed task must not receive new mode execution authority.
            bridge_results.append(client.execute_mode_calls(task))
            return execute_task(task)

        monkeypatch.setattr(client, "execute_task", execute_with_empty_bridge)
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
        assert bridge_results[0]["status"] == "completed"
        bridge_path = next(path for path, _ in calls if "/mode-calls/execute" in path)
        assert bridge_path.endswith("?assignment_id=" + assignment_id)
        terminal = http.post(bridge_path)
        assert terminal.status_code == 409


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
        receipt = different.json()["observation"]
        assert receipt["authoritative"] is False and receipt["execution_authority"] is False
        after = json.loads(state_path.read_bytes())
        original = json.loads(committed)
        assert after["tasks"] == original["tasks"] and after["runners"] == original["runners"]
        assert after["artifacts"][:-1] == original["artifacts"]
        assert after["artifacts"][-1]["artifact_id"] == receipt["artifact_id"]
        assert after["artifacts"][-1]["type"] == "runner_late_report"
        assert service._tasks["task"].model_dump(mode="json") == first.json()
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


@pytest.mark.parametrize("interruption", ["manual", "heartbeat"])
def test_upload_checks_assignment_again_after_storage_await(tmp_path, interruption):
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
            if interruption == "manual":
                await service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"]))
                current = (await service.poll_tasks("runner")).tasks[0]
            else:
                runner = service._runners["runner"]
                service._runners["runner"] = runner.model_copy(update={"heartbeat_at": "2000-01-01T00:00:00Z"})
                current = (await service.list_tasks())[0]
                assert current.status == "recovery_required"
        finally:
            release.set()
        reason = "assignment no longer matches" if interruption == "manual" else "requires recovery review"
        with pytest.raises(PermissionError, match=reason):
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


@pytest.mark.parametrize("recovery", ["restart", "heartbeat", "missing-runner"])
@pytest.mark.parametrize("limit", [0, 3])
def test_unknown_execution_cannot_be_replayed_by_another_runner(tmp_path, monkeypatch, caplog, recovery, limit):
    """Real local side effect with controlled result loss, not a domain Runner."""
    settings = settings_for(tmp_path)
    settings.compatibility_runner_max_automatic_retries = limit
    service = CompatibilityRunnerService(settings=settings)
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    effects = tmp_path / "effects.txt"

    def make_client(runner_id, max_parallel=1):
        return CompatibilityRunnerClient(
            base_url="http://test", runner_id=runner_id, name="Unknown execution contract",
            capabilities=["local_browser"], devices=[], max_parallel=max_parallel,
            poll_interval=1, executor="dry-run", state_file=tmp_path / f"{runner_id}.sqlite3",
        )

    def record_effect(task):
        with effects.open("a", encoding="utf-8") as stream:
            stream.write(task["task_id"] + "\n")

    with TestClient(app) as http:
        def transport(base_url, path, *, method="GET", payload=None):
            response = http.request(method, path, json=payload)
            if response.status_code >= 400:
                raise error.HTTPError(path, response.status_code, "API rejected request", {}, None)
            return response.json()

        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        client = make_client("original")
        try:
            client.register()
            asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
                plan_id="unknown-outcome-plan", tasks=[CompatibilityRunnerTask(
                    task_id="unknown-task", environment_id="local",
                    runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
                )],
            )))

            def interrupted_execute(task):
                record_effect(task)
                raise error.URLError("Controlled result loss after a committed local effect")

            monkeypatch.setattr(client, "execute_task", interrupted_execute)
            with pytest.raises(error.URLError):
                client.run_once()
            original_entries = client.journal.entries()
            assert len(original_entries) == 1
            assert original_entries[0]["phase"] == "executing"
            before = service._tasks["unknown-task"].model_dump(mode="json")
            # External client execution has no server-side bridge-start marker.
            # Its absence must not be interpreted as proof that nothing ran.
            assert before["metadata"].get("mode_calls_execution_status") is None
        finally:
            client.close()

        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="unrelated-plan", tasks=[CompatibilityRunnerTask(
                task_id="unrelated-task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        )))
        if recovery == "restart":
            service = CompatibilityRunnerService(settings=settings)
            app.state.compatibility_runner_service = service
        elif recovery == "heartbeat":
            runner = service._runners["original"]
            service._runners["original"] = runner.model_copy(update={"heartbeat_at": "2000-01-01T00:00:00Z"})
        else:
            # Missing catalog owner is not proof of an unexecuted assignment.
            service._runners.pop("original")

        replacement = make_client("replacement", max_parallel=2)
        execute = replacement.execute_task

        def tracked_execute(task):
            if task["task_id"] == "unknown-task":
                record_effect(task)
            return execute(task)

        monkeypatch.setattr(replacement, "execute_task", tracked_execute)
        try:
            replacement.register()
            processed = replacement.run_once()
        finally:
            replacement.close()
        assert effects.read_text(encoding="utf-8").splitlines() == ["unknown-task"], "Unknown effect was replayed"
        assert processed == 1
        assert service._tasks["unrelated-task"].status == "completed"
        held = service._tasks["unknown-task"]
        assert held.status == "recovery_required"
        assert held.assigned_runner_id == "original"
        assert held.metadata["assignment_id"] == before["metadata"]["assignment_id"]
        assert held.metadata["automatic_retry_count"] == before["metadata"]["automatic_retry_count"]
        assert held.metadata.get("retry_history") == before["metadata"].get("retry_history")
        assert held.metadata["recovery_reason"] == "assignment_outcome_unknown"
        assert held.metadata["recovery_source"] == ("restart" if recovery == "restart" else "heartbeat")
        assert held.result == before["result"]
        assert held.error == before["error"]
        assert [item.model_dump(mode="json") for item in held.artifacts] == before["artifacts"]
        assert "compatibility_task_recovery_required_staged" in caplog.text
        record = next(item for item in caplog.records if item.getMessage() == "compatibility_task_recovery_required_staged")
        ContextFilter().filter(record)
        rendered = ContextFormatter(LOG_FORMAT).format(record)
        assert json.loads(rendered.split(" diagnostics=", 1)[1]) == {
            "task_id": "unknown-task", "runner_id": "original",
            "assignment_id": before["metadata"]["assignment_id"],
            "recovery_source": held.metadata["recovery_source"], "recovery_reason": "assignment_outcome_unknown",
            "automatic_retry_count": 0, "automatic_retry_limit": limit,
        }
        saved = held.model_dump(mode="json")
        restored = CompatibilityRunnerService(settings=settings)
        app.state.compatibility_runner_service = restored
        assert restored._tasks["unknown-task"].model_dump(mode="json") == saved
        original = make_client("original")
        try:
            assert original.journal.entries() == original_entries
            evidence = original.reconcile_journal()
            assert len(evidence) == 1
            assert evidence[0].status == "unknown"
            assert evidence[0].reason_code == "server_recovery_required"
            assert evidence[0].execution_authority is False
            assert original.journal.entries() == original_entries
        finally:
            original.close()


@pytest.mark.parametrize("recovery", ["restart", "heartbeat"])
@pytest.mark.parametrize("limit", [0, 2])
@pytest.mark.parametrize("previous_automatic_retries", [None, 0, 2])
def test_interrupted_assignment_preserves_evidence_independent_of_retry_budget(
    tmp_path, recovery, limit, previous_automatic_retries,
):
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
        await service.poll_tasks("runner")
        await service.report_task("runner", "task", CompatibilityRunnerTaskReportRequest(
            status="completed", result={"summary": "Normal controlled completion before manual retry."},
        ))
        await service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["completed"]))
        assert (await service.poll_tasks("runner")).tasks[0].task_id == "task"
        artifact = await service.upload_task_artifact("runner", "task", CompatibilityArtifactUploadRequest(
            filename="evidence.txt", content_base64=base64.b64encode(b"safe evidence").decode(),
        ))
        mode_execution = await service.begin_mode_call_execution("runner", "task")
        await service.finish_mode_call_execution("runner", "task", status="partial", result_count=1,
            execution_id=mode_execution.metadata["mode_calls_execution_id"])
        # Legacy persisted counters are facts, not fresh execution authority.
        task = service._tasks["task"]
        metadata = {**task.metadata, "automatic_retry_count": previous_automatic_retries}
        if previous_automatic_retries is None:
            metadata.pop("automatic_retry_count")
        service._tasks["task"] = task.model_copy(update={"metadata": metadata})
        service._save_state()
        before = service._tasks["task"].model_dump(mode="json")
        if recovery == "restart":
            # Controlled service reconstruction; no domain Runner is crashed.
            service = CompatibilityRunnerService(settings=settings)
        else:
            # Existing stale-heartbeat fixture, not an observed outage.
            runner = service._runners["runner"]
            service._runners["runner"] = runner.model_copy(update={"heartbeat_at": "2000-01-01T00:00:00Z"})
            await service.list_tasks()
        paused = service._tasks["task"]
        assert paused.status == "recovery_required"
        assert paused.assigned_runner_id == "runner"
        assert paused.result == before["result"]
        assert [item.model_dump(mode="json") for item in paused.artifacts] == before["artifacts"]
        assert paused.artifacts[0].artifact_id == artifact.artifact_id
        assert paused.metadata["mode_calls_execution_status"] == "partial"
        assert paused.mode_calls == before["mode_calls"]
        assert paused.metadata["assignment_id"] == before["metadata"]["assignment_id"]
        assert paused.metadata.get("automatic_retry_count") == previous_automatic_retries
        assert ("automatic_retry_count" in paused.metadata) == ("automatic_retry_count" in before["metadata"])
        assert paused.metadata["retry_count"] == before["metadata"]["retry_count"]
        assert paused.metadata["retry_history"] == before["metadata"]["retry_history"]
        assert paused.metadata["automatic_retry_limit"] == limit
        assert paused.metadata["recovery_reason"] == "assignment_outcome_unknown"
        assert paused.metadata["recovery_source"] == recovery
        assert paused.error == before["error"]
        assert (await service.summarize_tasks()).failed == 0
        report = await service.build_report()
        assert report.status == "recovery_required"
        assert report.pending_tasks == 1
        assert [task.status for task in report.recoverable_tasks] == ["recovery_required"]
        assert "outcome is unknown" in report.recoverable_tasks[0].summary
        assert "budget exhausted" not in report.recoverable_tasks[0].summary
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
        default_retry = await restored.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"]))
        assert default_retry.task_ids == []
        assert default_retry.skipped_reasons == {"task": "status_recovery_required_not_requeueable"}
        assert restored._tasks["task"].model_dump(mode="json") == paused.model_dump(mode="json")
        retry = await restored.requeue_tasks(CompatibilityTaskRequeueRequest(
            task_ids=["task"], statuses=["recovery_required"], reason="reviewed_fixture_no_external_side_effects",
        ))
        assert retry.task_ids == ["task"]
        renewed = (await restored.poll_tasks("runner")).tasks[0]
        assert renewed.metadata["automatic_retry_count"] == 0
        assert renewed.metadata["recovery_reason"] is None
        assert renewed.metadata["assignment_id"] != before["metadata"]["assignment_id"]
        assert renewed.metadata["retry_history"][-1]["previous_assignment_id"] == before["metadata"]["assignment_id"]
        assert renewed.metadata["retry_history"][-1]["previous_artifacts"] == before["artifacts"]
    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["report", "upload", "begin", "finish"])
def test_held_assignment_identity_does_not_authorize_runner_writes(tmp_path, operation):
    async def exercise():
        settings = settings_for(tmp_path)
        service = CompatibilityRunnerService(settings=settings)
        await service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"], metadata={"assignment_fencing_version": 1},
        ))
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="late-write-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        assignment = (await service.poll_tasks("runner")).tasks[0].metadata["assignment_id"]
        await service.begin_mode_call_execution("runner", "task", assignment_id=assignment)
        restored = CompatibilityRunnerService(settings=settings)
        before = restored._tasks["task"].model_dump(mode="json")
        state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
        committed = state_path.read_bytes()
        with pytest.raises(PermissionError, match="requires recovery review") as rejected:
            if operation == "report":
                await restored.report_task("runner", "task", CompatibilityRunnerTaskReportRequest(
                    status="completed", assignment_id=assignment, result={"summary": "Late controlled result."},
                ))
            elif operation == "upload":
                await restored.upload_task_artifact("runner", "task", CompatibilityArtifactUploadRequest(
                    assignment_id=assignment, filename="late.txt", content_base64=base64.b64encode(b"late").decode(),
                ))
            elif operation == "begin":
                await restored.begin_mode_call_execution("runner", "task", assignment_id=assignment)
            else:
                await restored.finish_mode_call_execution(
                    "runner", "task", status="completed", result_count=1, assignment_id=assignment,
                )
        assert restored._tasks["task"].model_dump(mode="json") == before
        if operation == "report":
            assert isinstance(rejected.value, CompatibilityRunnerReportObserved)
            receipt = rejected.value.observation
            assert receipt.authoritative is False and receipt.execution_authority is False
            original, after = json.loads(committed), json.loads(state_path.read_bytes())
            assert after["tasks"] == original["tasks"] and after["runners"] == original["runners"]
            assert len(after["artifacts"]) == 1 and set(restored._artifacts) == {receipt.artifact_id}
            assert after["artifacts"][0]["metadata"]["runner_report_observation"] == receipt.model_dump(mode="json")
            assert after["artifacts"][0]["type"] == "runner_late_report"
        else:
            assert state_path.read_bytes() == committed
            assert restored._artifacts == {}
        assert not [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
    asyncio.run(exercise())


def test_legacy_budget_review_keeps_its_reason_and_never_reacquires_execution_authority(tmp_path):
    async def exercise():
        settings = settings_for(tmp_path)
        service = CompatibilityRunnerService(settings=settings)
        await service.register_runner(CompatibilityRunnerRegistrationRequest(
            runner_id="runner", capabilities=["local_browser"],
        ))
        await service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="legacy-review-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local",
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        ))
        await service.poll_tasks("runner")
        service = CompatibilityRunnerService(settings=settings)
        held = service._tasks["task"]
        # Persist the original writer's review reason as a legacy-file fixture.
        service._tasks["task"] = held.model_copy(update={"metadata": {
            **held.metadata, "automatic_retry_count": 3, "recovery_reason": "automatic_retry_budget_exhausted",
        }})
        service._save_state()
        before = service._tasks["task"].model_dump(mode="json")
        state_path = tmp_path / "data" / "compatibility" / "runner_state.json"
        committed = state_path.read_bytes()
        restored = CompatibilityRunnerService(settings=settings)
        report = await restored.build_report()
        assert report.status == "recovery_required"
        assert "Automatic recovery budget exhausted" in report.recoverable_tasks[0].summary
        assert (await restored.poll_tasks("runner")).tasks == []
        assert restored._tasks["task"].model_dump(mode="json") == before
        assert state_path.read_bytes() == committed
    asyncio.run(exercise())

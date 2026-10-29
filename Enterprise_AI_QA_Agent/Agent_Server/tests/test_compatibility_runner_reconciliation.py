from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from hashlib import sha256
import json
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib import error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.compatibility import router
from src.application.compatibility import runner_client as runner_client_module
from src.application.compatibility.runner_client import CompatibilityRunnerClient
from src.application.compatibility.runner_journal import reconcile_runner_journal_entry
from src.application.compatibility.runner_service import CompatibilityRunnerService
from src.modes.compatibility_testing_mode.contracts import (
    CompatibilityDispatchPlan,
    CompatibilityRunnerTask,
    RunnerSelector,
)
from src.schemas.compatibility_runner import (
    CompatibilityQueuedTask,
    CompatibilityArtifactUploadRequest,
    CompatibilityRunnerJournalReconciliation,
    CompatibilityRunnerRegistrationRequest,
    CompatibilityRunnerTaskReportRequest,
    CompatibilityTaskRequeueRequest,
)


def local_entry(*, phase="report_ready", assignment="assignment-1", report_status="completed"):
    task = CompatibilityQueuedTask(
        task_id="task-1", dispatch_id="dispatch-1", plan_id="plan-1", environment_id="local",
        status="assigned", assigned_runner_id="runner-1", case_ids=["case-1"],
        created_at="2026-10-29T00:00:00Z", updated_at="2026-10-29T00:00:00Z",
        metadata={"assignment_id": assignment},
    ).model_dump(mode="json")
    payload = CompatibilityRunnerTaskReportRequest(
        assignment_id=assignment, status=report_status, result={"summary": "controlled result"},
    ).model_dump(mode="json")
    return {"assignment": assignment, "phase": phase, "reason": None, "task": task, "payload": payload}


def server_task(entry, *, status="completed"):
    task = deepcopy(entry["task"])
    task["status"] = status
    payload = entry["payload"]
    task["metadata"]["report_receipt_sha256"] = sha256(json.dumps(
        {**payload, "status": status}, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    task["result"] = {**payload["result"], "artifacts": [], "error": payload["error"]}
    return task


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_exact_report_receipt_verifies_any_terminal_not_only_test_success(status):
    entry = local_entry(report_status=status)
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=server_task(entry, status=status))
    assert isinstance(result, CompatibilityRunnerJournalReconciliation)
    assert result.status == "completed"
    assert result.reason_code == "report_verified"
    assert result.server_status == status
    assert result.execution_authority is False


def test_terminal_status_without_report_receipt_is_unknown_not_proof():
    entry = local_entry()
    task = server_task(entry)
    task["metadata"].pop("report_receipt_sha256")
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=task)
    assert result.status == "unknown"
    assert result.reason_code == "server_receipt_missing"


@pytest.mark.parametrize("changed", ["receipt", "result", "assignment", "runner", "definition"])
def test_changed_evidence_cannot_be_verified_as_original_report(changed):
    entry = local_entry()
    task = server_task(entry)
    if changed == "receipt":
        task["metadata"]["report_receipt_sha256"] = "0" * 64
    elif changed == "result":
        task["result"]["summary"] = "changed persisted result"
    elif changed == "assignment":
        task["metadata"]["assignment_id"] = "replacement-assignment"
    elif changed == "runner":
        task["assigned_runner_id"] = "replacement-runner"
    else:
        task["case_ids"] = ["different-case"]
    before = deepcopy((entry, task))
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=task)
    assert result.status == "drifted"
    assert result.execution_authority is False
    assert (entry, task) == before


@pytest.mark.parametrize("phase", ["executing", "review", "conflict"])
def test_execution_without_saved_report_stays_unknown_even_with_server_terminal(phase):
    entry = local_entry(phase=phase)
    task = server_task(entry)
    entry["payload"] = None
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=task)
    assert result.status == "unknown"
    assert result.reason_code == "journal_report_missing"


@pytest.mark.parametrize("status", ["assigned", "queued", "recovery_required"])
def test_non_terminal_observation_never_grants_execution(status):
    entry = local_entry(phase="executing")
    task = server_task(entry, status=status)
    entry["payload"] = None
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=task)
    assert result.status == "unknown"
    assert result.execution_authority is False


def test_missing_server_task_is_unknown_without_retry_authority():
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=local_entry(phase="review"), server_task=None)
    assert result.status == "unknown"
    assert result.reason_code == "server_task_missing"
    assert result.execution_authority is False


def test_local_task_runner_identity_must_match_the_journal_owner():
    entry = local_entry()
    task = server_task(entry)
    entry["task"]["assigned_runner_id"] = "different-local-runner"
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=task)
    assert result.status == "drifted"
    assert result.reason_code == "journal_identity_drift"


@pytest.fixture
def connected(tmp_path, monkeypatch):
    """Real JSON/SQLite and ASGI API; no external executor or provider."""
    settings = SimpleNamespace(data_dir=str(tmp_path / "data"),
        storage=SimpleNamespace(artifact_root_dir=str(tmp_path / "artifacts")),
        compatibility_runner_heartbeat_timeout_seconds=120)
    service = CompatibilityRunnerService(settings=settings)
    asyncio.run(service.register_runner(CompatibilityRunnerRegistrationRequest(
        runner_id="runner-1", capabilities=["local_browser"], metadata={"assignment_fencing_version": 1},
    )))
    asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
        plan_id="plan-1", tasks=[CompatibilityRunnerTask(task_id="task-1", environment_id="local",
            runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]))],
    )))
    task = asyncio.run(service.poll_tasks("runner-1")).tasks[0].model_dump(mode="json")
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    calls = []
    with TestClient(app) as http:
        def transport(_base_url, path, *, method="GET", payload=None):
            calls.append((method, path))
            response = http.request(method, path, json=payload)
            if response.status_code >= 400:
                raise error.HTTPError(path, response.status_code, "controlled API response", {}, None)
            return response.json()
        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        client = CompatibilityRunnerClient(base_url="http://test", runner_id="runner-1", name="reconciliation",
            capabilities=[], devices=[], max_parallel=1, poll_interval=1, executor="dry-run",
            state_file=tmp_path / "runner.sqlite3")
        def forbidden(*args, **kwargs):
            pytest.fail("Reconciliation called a state-changing Runner port")
        for name in ("execute_task", "register", "heartbeat", "poll", "report", "upload_artifact"):
            monkeypatch.setattr(client, name, forbidden)
        client.journal.enqueue([task])
        try:
            yield SimpleNamespace(client=client, service=service, task=task, http=http, calls=calls,
                settings=settings, journal_path=tmp_path / "runner.sqlite3")
        finally:
            client.close()


def test_read_only_query_does_not_reap_expired_assignment_or_modify_journal(connected):
    ctx = connected
    runner = ctx.service._runners["runner-1"]
    ctx.service._runners["runner-1"] = runner.model_copy(update={"heartbeat_at": "2000-01-01T00:00:00Z"})
    ctx.service._save_state()
    ctx.client.journal.update(ctx.task["metadata"]["assignment_id"], "executing")
    before = (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes(), ctx.client.journal.entries())
    result = ctx.client.reconcile_journal()
    assert [item.status for item in result] == ["unknown"]
    assert ctx.calls == [("GET", "/api/v1/compatibility/tasks/task-1/snapshot")]
    assert (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes(), ctx.client.journal.entries()) == before
    assert ctx.service._tasks["task-1"].status == "assigned"


def test_snapshot_endpoint_is_read_only_and_returns_detached_data(connected):
    ctx = connected
    before = ctx.service._state_path.read_bytes()
    response = ctx.http.get("/api/v1/compatibility/tasks/task-1/snapshot")
    assert response.status_code == 200, response.text
    assert response.json() == ctx.task
    snapshot = asyncio.run(ctx.service.read_task_snapshot("task-1"))
    snapshot.metadata["assignment_id"] = "not-authoritative"
    assert ctx.service._tasks["task-1"].model_dump(mode="json") == ctx.task
    assert ctx.service._state_path.read_bytes() == before
    assert ctx.http.get("/api/v1/compatibility/tasks/absent/snapshot").status_code == 404


def test_committed_report_with_lost_ack_is_verified_without_retransmission(connected, monkeypatch):
    ctx = connected
    assignment = ctx.task["metadata"]["assignment_id"]
    payload = CompatibilityRunnerTaskReportRequest(assignment_id=assignment,
        status="success", result={"summary": "controlled dry-run result"})
    ctx.client.journal.update(assignment, "report_ready", payload=payload.model_dump(mode="json"))
    transport = runner_client_module._json_request
    def lose_committed_ack(base, path, **kwargs):
        value = transport(base, path, **kwargs)
        if path.endswith("/report"):
            raise error.URLError("controlled lost ACK after server commit")
        return value
    monkeypatch.setattr(runner_client_module, "_json_request", lose_committed_ack)
    with pytest.raises(error.URLError, match="lost ACK"):
        runner_client_module._json_request("http://test", "/api/v1/compatibility/runners/runner-1/tasks/task-1/report",
            method="POST", payload=payload.model_dump(mode="json"))
    ctx.calls.clear()
    before = (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes())
    result = ctx.client.reconcile_journal()
    assert [item.reason_code for item in result] == ["report_verified"]
    assert result[0].server_status == "completed"
    assert (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes()) == before
    assert len(ctx.client.journal.entries()) == 1  # Diagnostic read is not ACK.
    assert ctx.calls == [("GET", "/api/v1/compatibility/tasks/task-1/snapshot")]


def test_report_with_uploaded_and_inline_artifacts_uses_existing_projection(connected, caplog):
    ctx = connected
    assignment = ctx.task["metadata"]["assignment_id"]
    artifact = asyncio.run(ctx.service.upload_task_artifact("runner-1", "task-1", CompatibilityArtifactUploadRequest(
        assignment_id=assignment, upload_id="runner-log-v1", filename="evidence.txt",
        content_base64=base64.b64encode(b"controlled evidence").decode(),
    )))
    payload = CompatibilityRunnerTaskReportRequest(assignment_id=assignment, status="failure",
        result={"summary": "controlled failed assertion"}, error="controlled error code",
        artifacts=[{"type": "evidence", "uri": "artifact://controlled/inline"}],
        metadata={"executor": "fixture", "uploaded_artifact_ids": [artifact.artifact_id],
                  "report_receipt_sha256": "untrusted value must not win"})
    ctx.client.journal.update(assignment, "report_ready", payload=payload.model_dump(mode="json"))
    asyncio.run(ctx.service.report_task("runner-1", "task-1", payload))
    before = (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes())
    result = ctx.client.reconcile_journal()[0]
    assert result.reason_code == "report_verified"
    assert result.server_status == "failed"  # Receipt verification is not test success.
    assert len(ctx.service._tasks["task-1"].artifacts) == 2
    assert (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes()) == before
    assert "controlled error code" not in caplog.text


def test_empty_journal_uses_no_server_request(connected):
    ctx = connected
    ctx.client.journal.acknowledge(ctx.task["metadata"]["assignment_id"])
    assert ctx.client.reconcile_journal() == []
    assert ctx.calls == []


@pytest.mark.parametrize("field", ["assignment_id", "report_receipt_sha256"])
def test_malformed_receipt_fields_are_drift_not_invalid_evidence_models(field):
    entry = local_entry()
    task = server_task(entry)
    task["metadata"][field] = {"not": "an identity"}
    result = reconcile_runner_journal_entry(runner_id="runner-1", entry=entry, server_task=task)
    assert result.status == "drifted"
    # frozen model_copy must not bypass the evidence DTO's strict contract.
    CompatibilityRunnerJournalReconciliation.model_validate_json(result.model_dump_json())


@pytest.mark.parametrize("snapshot", [None, [], {"result": {"private": "not-for-logs"}}])
def test_malformed_success_response_is_reported_without_private_body_or_state_change(connected, monkeypatch, caplog, snapshot):
    ctx = connected
    monkeypatch.setattr(runner_client_module, "_json_request", lambda *args, **kwargs: snapshot)
    before = (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes())
    with pytest.raises(RuntimeError, match="data is invalid") as exc:
        ctx.client.reconcile_journal()
    assert "not-for-logs" not in str(exc.value) + caplog.text
    assert (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes()) == before


@pytest.mark.parametrize("code", [404, 503])
def test_snapshot_missing_is_unknown_but_server_failure_propagates(connected, monkeypatch, code):
    ctx = connected
    calls = []
    def unavailable(_base, path, **kwargs):
        calls.append(path)
        raise error.HTTPError(path, code, "controlled response", {}, None)
    monkeypatch.setattr(runner_client_module, "_json_request", unavailable)
    before = ctx.journal_path.read_bytes()
    if code == 404:
        assert ctx.client.reconcile_journal()[0].reason_code == "server_task_missing"
    else:
        with pytest.raises(error.HTTPError) as exc:
            ctx.client.reconcile_journal()
        assert exc.value.code == 503
    assert calls == ["/api/v1/compatibility/tasks/task-1/snapshot"]
    assert ctx.journal_path.read_bytes() == before


def test_same_task_reassigned_to_another_runner_is_drift_without_state_change(connected):
    ctx = connected
    asyncio.run(ctx.service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task-1"], statuses=["assigned"])))
    asyncio.run(ctx.service.register_runner(CompatibilityRunnerRegistrationRequest(
        runner_id="runner-2", capabilities=["local_browser"], metadata={"assignment_fencing_version": 1},
    )))
    asyncio.run(ctx.service.poll_tasks("runner-2"))
    before = (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes())
    result = ctx.client.reconcile_journal()
    assert result[0].reason_code == "server_assignment_drift"
    assert result[0].server_runner_id == "runner-2"
    assert (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes()) == before


def test_killed_journal_writer_is_queried_without_execution_or_phase_rewrite(connected, tmp_path):
    """Real child kill after SQLite commit; no domain Runner is invoked."""
    ctx = connected
    ctx.client.close()  # Release this fixture's local journal lock for the child.
    marker = tmp_path / "child-ready"
    code = """import sys, time
from pathlib import Path
from src.application.compatibility.runner_journal import RunnerJournal
journal = RunnerJournal(sys.argv[1], base_url='http://test', runner_id='runner-1')
journal.update(sys.argv[2], 'executing')
Path(sys.argv[3]).write_text('committed', encoding='utf-8')
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-u", "-c", code, str(ctx.journal_path),
        ctx.task["metadata"]["assignment_id"], str(marker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "Journal writer did not reach the committed fault point"
        assert process.poll() is None
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode != 0, (stdout, stderr)
        assert "phase=executing" in stdout, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    recovered = CompatibilityRunnerClient(base_url="http://test", runner_id="runner-1", name="recovery",
        capabilities=[], devices=[], max_parallel=1, poll_interval=1, executor="dry-run", state_file=ctx.journal_path)
    try:
        before = (ctx.journal_path.read_bytes(), ctx.service._state_path.read_bytes())
        result = recovered.reconcile_journal()
        assert result[0].status == "unknown"
        assert result[0].journal_phase == "executing"
        assert recovered.journal.entries()[0]["phase"] == "executing"
        assert (ctx.journal_path.read_bytes(), ctx.service._state_path.read_bytes()) == before
        assert ctx.calls == [("GET", "/api/v1/compatibility/tasks/task-1/snapshot")]
    finally:
        recovered.close()


def test_reconcile_cli_does_not_register_heartbeat_poll_or_execute(connected, monkeypatch, capsys):
    ctx = connected
    ctx.client.close()
    monkeypatch.setattr(sys, "argv", ["runner-client", "--base-url", "http://test", "--runner-id", "runner-1",
        "--state-file", str(ctx.journal_path), "--reconcile-only"])
    before = (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes())
    runner_client_module.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unknown"
    assert result["execution_authority"] is False
    assert ctx.calls == [("GET", "/api/v1/compatibility/tasks/task-1/snapshot")]
    assert (ctx.service._state_path.read_bytes(), ctx.journal_path.read_bytes()) == before


def test_reconcile_cli_requires_existing_journal_instead_of_creating_empty_evidence(tmp_path, monkeypatch, capsys):
    path = tmp_path / "does-not-exist" / "runner.sqlite3"
    monkeypatch.setattr(sys, "argv", ["runner-client", "--state-file", str(path), "--reconcile-only"])
    with pytest.raises(SystemExit) as exc:
        runner_client_module.main()
    assert exc.value.code == 2
    assert "existing" in capsys.readouterr().err
    assert not path.parent.exists()

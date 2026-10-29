"""Committed mode results after response loss; local ASGI/JSON/SQLite, not E3."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from http.client import IncompleteRead
import io
import json
import sqlite3
import sys
from urllib import error

import pytest

from src.application.compatibility import runner_client as runner_client_module
from src.application.compatibility.runner_client import CompatibilityRunnerClient
from src.application.compatibility.runner_journal import RunnerJournal
from src.application.compatibility.runner_service import CompatibilityRunnerService
from src.schemas.compatibility_runner import (
    CompatibilityModeExecutionEvidence,
    CompatibilityRunnerTaskReportRequest, CompatibilityTaskRequeueRequest,
    runner_mode_evidence_content, runner_mode_observation_artifact_id,
)
from src.schemas.tool_runtime import ToolExecutionRecord
from tests.test_compatibility_mode_execution_authority import connected, ControlledRuntime, EXECUTE, PREFIX, state


QUERY = f"{PREFIX}/runners/original/tasks/task/mode-calls/execution"
MARKER = "mode_calls_execution_v1"
RESULT_ID = "mode_calls_execution_result_artifact_id"


def client_for(path, *, executor="server-mode-calls"):
    return CompatibilityRunnerClient(base_url="http://test", runner_id="original", name="Mode recovery",
        capabilities=["local_browser"], devices=[], max_parallel=1, poll_interval=1,
        executor=executor, state_file=path)


class Transport:
    def __init__(self, h, *, drop_execute=False, drop_report=False):
        self.h, self.drop_execute, self.drop_report = h, drop_execute, drop_report
        self.requests, self.responses = [], []
        self.before_execute = None
        self.after_query = None

    def __call__(self, base_url, path, *, method="GET", payload=None):
        self.requests.append((method, path, deepcopy(payload)))
        if path.split("?")[0] == EXECUTE and self.before_execute:
            self.before_execute()
        response = self.h.http.request(method, path, json=payload)
        self.responses.append((path, response))
        if response.status_code >= 400:
            raise error.HTTPError(path, response.status_code, "Controlled API rejection", {}, io.BytesIO(response.content))
        if path.split("?")[0] == EXECUTE and self.drop_execute:
            self.drop_execute = False
            raise error.URLError("Controlled loss of committed mode response")
        if path.endswith("/report") and self.drop_report:
            self.drop_report = False
            raise error.URLError("Controlled loss of committed report response")
        if path.split("?")[0] == QUERY and self.after_query:
            self.after_query()
        return response.json()


@pytest.mark.parametrize("exception_prefix", [False, True])
def test_cli_recovers_original_committed_response_without_reexecuting_tools(connected, tmp_path, monkeypatch, exception_prefix):
    h = connected
    runtime = ControlledRuntime(h)
    if exception_prefix:
        execute = runtime.execute

        async def fail_second(tool, call, context):
            if call.arguments["step"] == 1:
                raise RuntimeError("private-mode-recovery-error")
            return await execute(tool, call, context)

        runtime.execute = fail_second
    h.app.state.tool_runtime_service = runtime
    transport = Transport(h, drop_execute=not exception_prefix)
    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    path = tmp_path / "runner.sqlite3"
    client = client_for(path)
    try:
        client.register()
        client.journal.enqueue([h.first])
        with pytest.raises(error.HTTPError if exception_prefix else error.URLError):
            client.run_once()
        assert client.journal.entries()[0]["phase"] == "executing"
    finally:
        client.close()
    # Restart configuration cannot decide what the original request executed.
    recovered = client_for(path, executor="dry-run")
    try:
        recovered.register()
        before = len(transport.requests)
        assert recovered.run_once() == 1, "A known saved mode result was left unknown instead of delivered"
        assert recovered.journal.entries() == []
        resumed = transport.requests[before:]
        assert any(method == "GET" and url.split("?")[0] == QUERY for method, url, _ in resumed)
        assert not any(url.split("?")[0] == EXECUTE or "/tasks?" in url for _, url, _ in resumed)
        assert h.effects.read_text(encoding="utf-8").splitlines() == (["0"] if exception_prefix else ["0", "1"])
        task = h.service._tasks["task"]
        assert task.status == ("failed" if exception_prefix else "completed")
        assert task.metadata["executor"] == "server_mode_calls"
        evidence = next(item for item in state(h)["artifacts"] if item["type"] == "runner_mode_execution")
        response = h.http.get(evidence["uri"]).json()["response"]
        assert task.result["mode_call_results"] == response["mode_call_results"]
        assert task.metadata[RESULT_ID] == evidence["artifact_id"]
    finally:
        recovered.close()


@pytest.mark.parametrize("phase", ["not_started", "running", "completed"])
def test_query_is_read_only_and_only_returns_bound_committed_result(connected, phase):
    h = connected
    h.app.state.tool_runtime_service = ControlledRuntime(h)
    params = {"assignment_id": h.first["metadata"]["assignment_id"]}
    if phase == "running":
        asyncio.run(h.service.begin_mode_call_execution("original", "task", **params))
    elif phase == "completed":
        original = h.http.post(EXECUTE, params=params)
        assert original.status_code == 200
    before = h.state_path.read_bytes()
    response = h.http.get(QUERY, params=params)
    assert response.status_code == 200, response.text
    assert h.state_path.read_bytes() == before
    result = response.json()
    assert result["execution_authority"] is False
    assert result["scope"] == "compatibility_runner_mode_execution_result_only"
    if phase == "completed":
        assert result["status"] == "available"
        assert result["evidence"]["response"] == original.json()
        assert result["observation"]["artifact_id"] == h.service._tasks["task"].metadata[RESULT_ID]
    else:
        assert result["status"] == "unknown" and result["evidence"] is None


@pytest.mark.parametrize("waiting", ["waiting_resource", "waiting_children"])
def test_waiting_mode_result_is_not_aggregated_as_completed(connected, waiting):
    h = connected

    class WaitingRuntime:
        async def execute(self, tool, call, context):
            return ToolExecutionRecord(call_id=call.id, tool_key=tool.key, tool_name=tool.name,
                status=waiting, summary="Controlled pending mode result")

    h.app.state.tool_runtime_service = WaitingRuntime()
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200
    assert response.json()["status"] == "partial" and response.json()["ok"] is False
    assert "2 partial" in response.json()["summary"]


def saved_mode(h):
    runtime = ControlledRuntime(h)
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200, response.text
    return runtime


def lost_mode(h, path, monkeypatch):
    runtime = ControlledRuntime(h)
    h.app.state.tool_runtime_service = runtime
    transport = Transport(h, drop_execute=True)
    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    client = client_for(path)
    try:
        client.register()
        client.journal.enqueue([h.first])
        with pytest.raises(error.URLError):
            client.run_once()
        assert client.journal.entries()[0]["reason"] == MARKER
    finally:
        client.close()
    return runtime, transport


def test_real_cli_marks_original_operation_before_sending_and_delivers_only_once(connected, tmp_path, monkeypatch, capsys):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime = ControlledRuntime(h)
    h.app.state.tool_runtime_service = runtime
    journal = RunnerJournal(path, base_url="http://test", runner_id="original")
    journal.enqueue([h.first])
    journal.close()
    transport = Transport(h, drop_execute=True)

    def read_committed_marker():
        db = sqlite3.connect(path)
        try:
            row = db.execute("SELECT task, phase, reason FROM entries").fetchone()
            assert json.loads(row[0]) == h.first
            assert row[1:] == ("executing", MARKER), "HTTP started before its identity was committed"
        finally:
            db.close()

    transport.before_execute = read_committed_marker
    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    monkeypatch.setattr(sys, "argv", ["runner-client", "--base-url", "http://test", "--runner-id", "original",
        "--capabilities", "local_browser", "--state-file", str(path), "--executor", "server-mode-calls", "--once"])
    with pytest.raises(error.URLError):
        runner_client_module.main()
    runner_client_module.main()
    runner_client_module.main()
    assert len(runtime.calls) == 2
    assert sum(url.split("?")[0] == EXECUTE for _, url, _ in transport.requests) == 1
    assert sum(url.endswith("/report") for _, url, _ in transport.requests) == 1
    assert "private-mode-return-body" not in capsys.readouterr().out


def test_marker_save_failure_prevents_http_and_preserves_original_journal(connected, tmp_path, monkeypatch):
    h = connected
    transport = Transport(h)
    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    client = client_for(tmp_path / "runner.sqlite3")
    try:
        client.register()
        client.journal.enqueue([h.first])
        with client.journal._db:
            client.journal._db.execute(f"""CREATE TRIGGER block_marker BEFORE UPDATE OF reason ON entries
                WHEN NEW.reason = '{MARKER}' BEGIN SELECT RAISE(FAIL, 'controlled marker save failure'); END""")
        with pytest.raises(RuntimeError, match="could not be marked"):
            client.run_once()
        entry = client.journal.entries()[0]
        assert entry["phase"] == "executing" and entry["reason"] is None and entry["payload"] is None
        assert not any(url.split("?")[0] == EXECUTE or url.endswith("/report") for _, url, _ in transport.requests)
        assert not h.effects.exists()
    finally:
        client.close()


@pytest.mark.parametrize("drift", [
    "definition", "execution_id", "execution_runner", "reference", "pointer", "content", "receipt",
    "count", "count_bool", "finished_at", "status",
])
def test_query_rejects_drift_without_mutating_or_exposing_result(connected, drift, caplog):
    h = connected
    saved_mode(h)
    task = h.service._tasks["task"]
    artifact = h.service._artifacts[task.metadata[RESULT_ID]]
    if drift == "definition":
        task.mode_calls[0]["arguments"]["step"] = False  # Not interchangeable with JSON 0.
    elif drift == "execution_id":
        task.metadata["mode_calls_execution_id"] = "different-execution"
    elif drift == "execution_runner":
        task.metadata["mode_calls_execution_runner_id"] = "different-runner"
    elif drift == "reference":
        task.metadata["mode_calls_execution_reference"]["definition_sha256"] = "a" * 64
    elif drift == "pointer":
        task.metadata[RESULT_ID] = "missing-bound-artifact"
    elif drift == "content":
        artifact.metadata["__content_text"] = artifact.metadata["__content_text"].replace("private-mode-return-body", "private-drifted-result")
    elif drift == "receipt":
        artifact.metadata["runner_mode_execution_observation"]["authoritative"] = True
    elif drift in {"count", "count_bool"}:
        task.metadata["mode_calls_execution_result_count"] = True if drift == "count_bool" else 3
    elif drift == "finished_at":
        task.metadata["mode_calls_execution_completed_at"] = "2000-01-01T00:00:00Z"
    else:
        task.metadata["mode_calls_execution_status"] = "failed"
    h.service._save_state()
    before = h.state_path.read_bytes()
    response = h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Compatibility runner state is unavailable."}
    assert h.state_path.read_bytes() == before
    assert "private-mode" not in response.text + caplog.text


@pytest.mark.parametrize("condition,reason", [
    ("legacy", "execution_result_unbound"), ("stale", "runner_lease_expired"),
    ("runner_missing", "runner_unavailable"), ("restart", "task_not_assigned"),
    ("terminal", "task_not_assigned"), ("assignment", "assignment_superseded"),
])
def test_query_never_promotes_unknown_expired_or_other_assignment_result(connected, condition, reason):
    h = connected
    saved_mode(h)
    task = h.service._tasks["task"]
    if condition == "legacy":
        task.metadata.pop(RESULT_ID)
    elif condition == "stale":
        h.service._runners["original"].heartbeat_at = "2000-01-01T00:00:00Z"
    elif condition == "runner_missing":
        h.service._runners.pop("original")
    elif condition == "terminal":
        asyncio.run(h.service.report_task("original", "task", CompatibilityRunnerTaskReportRequest(
            status="completed", assignment_id=h.first["metadata"]["assignment_id"], result={"summary": "Already reported"})))
    elif condition == "assignment":
        task.metadata["assignment_id"] = "new-assignment"
    h.service._save_state()
    if condition == "restart":
        h.service = CompatibilityRunnerService(settings=h.settings)
        h.app.state.compatibility_runner_service = h.service
    before = h.state_path.read_bytes()
    response = h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200
    assert response.json()["status"] == ("unknown" if condition == "legacy" else "conflict")
    assert response.json()["reason_code"] == reason and response.json()["evidence"] is None
    assert h.state_path.read_bytes() == before
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0", "1"]


def test_query_cannot_use_late_observation_and_new_assignment_cannot_borrow_old_binding(connected):
    h = connected
    runtime = ControlledRuntime(h, position="manual")
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 409
    task = h.service._tasks["task"]
    assert task.metadata.get(RESULT_ID) is None
    original = h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]}).json()
    assert original["status"] == "conflict" and original["evidence"] is None
    current = h.http.get(QUERY, params={"assignment_id": task.metadata["assignment_id"]}).json()
    assert current["status"] == "unknown" and current["evidence"] is None
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0"]


@pytest.mark.parametrize("failure", ["network", "503", "scope", "identity", "authority_zero", "version_true", "content", "partial_as_completed"])
def test_query_errors_preserve_executing_journal_and_never_fallback(connected, tmp_path, monkeypatch, failure, caplog):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime, transport = lost_mode(h, path, monkeypatch)

    def broken(base_url, url, **kwargs):
        raw = transport(base_url, url, **kwargs)
        if url.split("?")[0] != QUERY:
            return raw
        if failure == "network":
            raise error.URLError("Controlled query connection loss")
        if failure == "503":
            raise error.HTTPError(url, 503, "Controlled query unavailable", {}, None)
        if failure == "scope":
            raw["scope"] = "other_scope"
        elif failure == "identity":
            raw["assignment_id"] = "other-assignment"
        elif failure == "authority_zero":
            raw["execution_authority"] = 0
        elif failure == "version_true":
            raw["schema_version"] = True
        elif failure == "content":
            raw["evidence"]["response"]["mode_call_results"][0]["output"]["value"] = "private-corrupted-result"
        else:
            raw["evidence"]["response"]["mode_call_results"][0]["status"] = "waiting_children"
            rebind_query_receipt(raw)
        return raw

    client = client_for(path)
    try:
        client.register()
        before, entries = path.read_bytes(), client.journal.entries()
        at = len(transport.requests)
        monkeypatch.setattr(runner_client_module, "_json_request", broken)
        with pytest.raises((error.URLError, RuntimeError)):
            client.run_once()
        assert path.read_bytes() == before and client.journal.entries() == entries
        assert [(method, url.split("?")[0]) for method, url, _ in transport.requests[at:]] == [
            ("POST", f"{PREFIX}/runners/original/heartbeat"), ("GET", QUERY)]
        assert len(runtime.calls) == 2 and "private-" not in caplog.text
    finally:
        client.close()


def rebind_query_receipt(raw):
    """Self-consistent hashes alone cannot prove valid bridge aggregation."""
    from hashlib import sha256
    evidence = CompatibilityModeExecutionEvidence.model_validate(raw["evidence"])
    digest = sha256(runner_mode_evidence_content(evidence).encode("utf-8")).hexdigest()
    raw["observation"]["content_sha256"] = digest
    raw["observation"]["artifact_id"] = runner_mode_observation_artifact_id(evidence.reference, digest)


@pytest.mark.parametrize("condition", ["old_server", "unknown", "old_journal"])
def test_missing_recovery_proof_goes_to_review_without_execution(connected, tmp_path, monkeypatch, condition):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime, transport = lost_mode(h, path, monkeypatch)
    client = client_for(path)
    try:
        client.register()
        if condition == "old_journal":
            client.journal.update(h.first["metadata"]["assignment_id"], "executing")
        elif condition == "unknown":
            h.service._tasks["task"].metadata.pop(RESULT_ID)
            h.service._save_state()
        else:
            def old_server(base_url, url, **kwargs):
                if url.split("?")[0] == QUERY:
                    raise error.HTTPError(url, 404, "Not found", {}, None)
                return transport(base_url, url, **kwargs)
            monkeypatch.setattr(runner_client_module, "_json_request", old_server)
        at = len(transport.requests)
        assert client.run_once() == 0
        assert client.journal.entries()[0]["phase"] == "review"
        assert not any(url.split("?")[0] == EXECUTE or "/tasks?" in url or url.endswith("/report")
            for _, url, _ in transport.requests[at:])
        assert len(runtime.calls) == 2
    finally:
        client.close()


def test_sqlite_failure_after_verified_query_only_requeries_saved_result(connected, tmp_path, monkeypatch):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime, transport = lost_mode(h, path, monkeypatch)
    client = client_for(path)
    try:
        client.register()
        with client.journal._db:
            client.journal._db.execute("""CREATE TRIGGER block_executed BEFORE UPDATE OF phase ON entries
                WHEN NEW.phase = 'executed' BEGIN SELECT RAISE(FAIL, 'controlled result save failure'); END""")
        before = client.journal.entries()
        with pytest.raises(sqlite3.DatabaseError, match="controlled result save failure"):
            client.run_once()
        assert client.journal.entries() == before
        assert not any(url.endswith(("/report", "/artifacts/upload")) for _, url, _ in transport.requests)
        with client.journal._db:
            client.journal._db.execute("DROP TRIGGER block_executed")
    finally:
        client.close()
    client = client_for(path)
    try:
        client.register()
        assert client.run_once() == 1
        assert len(runtime.calls) == 2 and client.journal.entries() == []
        assert sum(url.split("?")[0] == QUERY for _, url, _ in transport.requests) == 2
    finally:
        client.close()


def test_recovered_report_ack_loss_replays_identical_report_without_requery(connected, tmp_path, monkeypatch):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime, transport = lost_mode(h, path, monkeypatch)
    transport.drop_report = True
    client = client_for(path)
    try:
        client.register()
        with pytest.raises(error.URLError):
            client.run_once()
        assert client.journal.entries()[0]["phase"] == "report_ready"
    finally:
        client.close()
    before, at = state(h), len(transport.requests)
    client = client_for(path)
    try:
        client.register()
        assert client.run_once() == 1 and client.journal.entries() == []
        assert state(h)["tasks"] == before["tasks"] and state(h)["artifacts"] == before["artifacts"]
        assert not any(url.split("?")[0] in {EXECUTE, QUERY} or url.endswith("/artifacts/upload")
            for _, url, _ in transport.requests[at:])
        reports = [body for _, url, body in transport.requests if url.endswith("/report")]
        assert len(reports) == 2 and reports[0] == reports[1]
        assert len(runtime.calls) == 2
    finally:
        client.close()


def test_assignment_change_after_query_still_blocks_delivery(connected, tmp_path, monkeypatch):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime, transport = lost_mode(h, path, monkeypatch)

    def change_assignment():
        asyncio.run(h.service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"])))
        asyncio.run(h.service.poll_tasks("original"))

    transport.after_query = change_assignment
    client = client_for(path)
    try:
        client.register()
        assert client.run_once() == 0
        entry = client.journal.entries()[0]
        assert entry["phase"] == "conflict" and entry["payload"]["assignment_id"] == h.first["metadata"]["assignment_id"]
        assert h.service._tasks["task"].result == {} and len(runtime.calls) == 2
        assert not any(url.endswith("/report") for _, url, _ in transport.requests)
    finally:
        client.close()


def test_report_reconciliation_remains_read_only_even_with_available_mode_result(connected, tmp_path, monkeypatch):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime, transport = lost_mode(h, path, monkeypatch)
    client = client_for(path)
    try:
        before, server, at = path.read_bytes(), h.state_path.read_bytes(), len(transport.requests)
        results = client.reconcile_journal()
        assert results[0].status == "unknown"
        assert path.read_bytes() == before and h.state_path.read_bytes() == server
        assert [(method, url) for method, url, _ in transport.requests[at:]] == [("GET", f"{PREFIX}/tasks/task/snapshot")]
        assert len(runtime.calls) == 2
    finally:
        client.close()


def test_query_contract_is_additive_requires_assignment_and_needs_no_runtime(connected):
    h = connected
    saved_mode(h)
    del h.app.state.tool_runtime_service
    del h.app.state.tool_registry
    before = h.state_path.read_bytes()
    assert h.http.get(QUERY).status_code == 422
    assert h.http.get(QUERY, params={"assignment_id": ""}).status_code == 422
    assert h.http.get(QUERY.replace("/tasks/task/", "/tasks/missing/"), params={"assignment_id": "original"}).status_code == 404
    response = h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200 and response.json()["status"] == "available"
    snapshot = asyncio.run(h.service.query_mode_call_execution("original", "task", assignment_id=h.first["metadata"]["assignment_id"]))
    snapshot.evidence.response["mode_call_results"].clear()
    assert h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]}).json() == response.json()
    assert h.state_path.read_bytes() == before


@pytest.mark.parametrize("malformed", ["json", "incomplete", "count", "waiting_as_completed"])
def test_invalid_live_response_keeps_original_marker_for_read_recovery(connected, tmp_path, monkeypatch, malformed):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime = ControlledRuntime(h)
    h.app.state.tool_runtime_service = runtime
    transport = Transport(h)

    def malformed_response(base_url, url, **kwargs):
        raw = transport(base_url, url, **kwargs)
        if url.split("?")[0] == EXECUTE:
            if malformed == "json":
                raise json.JSONDecodeError("controlled malformed response", "private-response", 0)
            if malformed == "incomplete":
                raise IncompleteRead(b"private-truncated-response", 100)
            if malformed == "count":
                raw["mode_call_count"] = 999
            else:
                raw["mode_call_results"][0]["status"] = "waiting_resource"
        return raw

    monkeypatch.setattr(runner_client_module, "_json_request", malformed_response)
    client = client_for(path)
    try:
        client.register()
        client.journal.enqueue([h.first])
        with pytest.raises(RuntimeError, match="Mode execution response is invalid"):
            client.run_once()
        entry = client.journal.entries()[0]
        assert entry["phase"] == "executing" and entry["reason"] == MARKER and entry["payload"] is None
        assert not any(url.endswith(("/report", "/artifacts/upload")) for _, url, _ in transport.requests)
        monkeypatch.setattr(runner_client_module, "_json_request", transport)
        assert client.run_once() == 1 and client.journal.entries() == []
        assert len(runtime.calls) == 2
    finally:
        client.close()


def test_finish_binding_and_result_are_atomic_on_json_save_failure(connected):
    h = connected
    runtime = ControlledRuntime(h)
    execute = runtime.execute

    async def block_finish(tool, call, context):
        result = await execute(tool, call, context)
        if call.arguments["step"] == 1:
            h.state_path.with_suffix(".tmp").mkdir()
        return result

    runtime.execute = block_finish
    h.app.state.tool_runtime_service = runtime
    try:
        response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
        assert response.status_code == 503
        persisted = state(h)
        task = persisted["tasks"][0]
        assert task["metadata"].get(RESULT_ID) is None and task["metadata"]["mode_calls_execution_status"] == "running"
        assert persisted["artifacts"] == []
        assert h.service._tasks["task"].model_dump(mode="json") == task
        assert h.service._artifacts == {}
        before = h.state_path.read_bytes()
        query = h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]})
        assert query.json()["status"] == "unknown" and query.json()["reason_code"] == "execution_running"
        assert h.state_path.read_bytes() == before
        assert h.effects.read_text(encoding="utf-8").splitlines() == ["0", "1"]
    finally:
        h.state_path.with_suffix(".tmp").rmdir()


def test_result_binding_is_server_owned_archived_and_not_reused_for_next_execution(connected):
    h = connected
    saved_mode(h)
    original = h.service._tasks["task"]
    bound = original.metadata[RESULT_ID]
    reported = asyncio.run(h.service.report_task("original", "task", CompatibilityRunnerTaskReportRequest(
        status="completed", assignment_id=h.first["metadata"]["assignment_id"], metadata={RESULT_ID: "forged"})))
    assert reported.metadata[RESULT_ID] == bound
    asyncio.run(h.service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["completed"])))
    queued = h.service._tasks["task"]
    assert queued.metadata[RESULT_ID] is None
    assert queued.metadata["retry_history"][-1]["previous_mode_call_execution"][RESULT_ID] == bound
    new = asyncio.run(h.service.poll_tasks("original")).tasks[0]
    response = h.http.post(EXECUTE, params={"assignment_id": new.metadata["assignment_id"]})
    assert response.status_code == 200
    current = h.service._tasks["task"]
    assert current.metadata[RESULT_ID] != bound
    current.metadata[RESULT_ID] = bound
    h.service._save_state()
    before = h.state_path.read_bytes()
    assert h.http.get(QUERY, params={"assignment_id": new.metadata["assignment_id"]}).status_code == 503
    old = h.http.get(QUERY, params={"assignment_id": h.first["metadata"]["assignment_id"]}).json()
    assert old["status"] == "conflict" and old["evidence"] is None
    assert h.state_path.read_bytes() == before


def test_custom_sdk_bridge_consumer_does_not_claim_builtin_report_recovery(connected, tmp_path, monkeypatch):
    h, path = connected, tmp_path / "runner.sqlite3"
    runtime = ControlledRuntime(h)
    h.app.state.tool_runtime_service = runtime
    transport = Transport(h, drop_execute=True)
    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    client = client_for(path, executor="dry-run")
    try:
        client.register()
        client.journal.enqueue([h.first])
        project = client.execute_task

        def custom_projection(task):
            client.execute_mode_calls(task)
            return project(task)

        monkeypatch.setattr(client, "execute_task", custom_projection)
        with pytest.raises(error.URLError):
            client.run_once()
        assert client.journal.entries()[0]["reason"] != MARKER
        assert client.run_once() == 0
        assert client.journal.entries()[0]["phase"] == "review"
        assert not any(url.split("?")[0] == QUERY or url.endswith("/report") for _, url, _ in transport.requests)
        assert len(runtime.calls) == 2
    finally:
        client.close()

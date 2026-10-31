"""Late reports are durable observations, never authoritative task results."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import io
import json
import logging
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
from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT
from src.modes.compatibility_testing_mode.contracts import CompatibilityDispatchPlan, CompatibilityRunnerTask, RunnerSelector
from src.schemas.compatibility_runner import CompatibilityRunnerReportObservation, CompatibilityRunnerTaskReportRequest


PREFIX = "/api/v1/compatibility"
OBSERVATION_TYPE = "runner_late_report"


@pytest.fixture
def connected(tmp_path):
    """Formal ASGI routes and real isolated JSON; no external domain Runner."""
    settings = SimpleNamespace(data_dir=str(tmp_path / "data"),
        storage=SimpleNamespace(artifact_root_dir=str(tmp_path / "artifacts")),
        compatibility_runner_heartbeat_timeout_seconds=120)
    service = CompatibilityRunnerService(settings=settings)
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as http:
        assert http.post(f"{PREFIX}/runners/register", json={
            "runner_id": "original", "capabilities": ["local_browser"],
            "metadata": {"assignment_fencing_version": 1},
        }).status_code == 200
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="late-report-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local", case_ids=["case"],
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
            )],
        )))
        first = http.post(f"{PREFIX}/runners/original/tasks/poll").json()["tasks"][0]
        yield SimpleNamespace(http=http, app=app, settings=settings, first=first,
            state_path=tmp_path / "data" / "compatibility" / "runner_state.json")


def report_path(runner_id="original"):
    return f"{PREFIX}/runners/{runner_id}/tasks/task/report"


def late_payload(h):
    return {"assignment_id": h.first["metadata"]["assignment_id"], "status": "completed",
        "result": {"summary": "Controlled late result", "value": 7},
        "metadata": {"private_fixture": "private-late-report-body"}}


def persisted(h):
    return json.loads(h.state_path.read_text(encoding="utf-8"))


def assert_authoritative_collections_unchanged(before, after):
    assert after["tasks"] == before["tasks"]
    assert after["runners"] == before["runners"]
    assert after["artifacts"][:len(before["artifacts"])] == before["artifacts"]


@pytest.mark.parametrize("position", ["held", "superseded", "terminal", "runner-removed"])
def test_late_report_is_queryable_after_restart_without_changing_authoritative_result(connected, caplog, position):
    h = connected
    if position == "held":
        h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    elif position == "superseded":
        assert h.http.post(f"{PREFIX}/tasks/requeue", json={"task_ids": ["task"], "statuses": ["assigned"]}).status_code == 200
        assert h.http.post(f"{PREFIX}/runners/register", json={
            "runner_id": "replacement", "capabilities": ["local_browser"],
        }).status_code == 200
        assert h.http.post(f"{PREFIX}/runners/replacement/tasks/poll").json()["tasks"][0]["task_id"] == "task"
    elif position == "terminal":
        assert h.http.post(report_path(), json={**late_payload(h), "result": {"summary": "Original authoritative result"}}).status_code == 200
    else:
        service = h.app.state.compatibility_runner_service
        service._runners.pop("original")
        service._save_state()
    before = persisted(h)
    response = h.http.post(report_path(), json=late_payload(h))
    assert response.status_code == (404 if position == "runner-removed" else 409)
    receipt = response.json().get("observation")
    assert receipt is not None, "The rejected original report was lost instead of being preserved as an observation"
    assert receipt["scope"] == "compatibility_runner_report_observation_only"
    assert receipt["authoritative"] is False and receipt["execution_authority"] is False
    assert receipt["assignment_id"] == h.first["metadata"]["assignment_id"]
    assert receipt["runner_id"] == "original" and receipt["task_id"] == "task"
    assert "private-late-report-body" not in response.text + caplog.text
    after = persisted(h)
    assert_authoritative_collections_unchanged(before, after)
    assert len(after["artifacts"]) == len(before["artifacts"]) + 1
    artifact = after["artifacts"][-1]
    assert artifact["artifact_id"] == receipt["artifact_id"]
    assert artifact["type"] == OBSERVATION_TYPE
    assert artifact["metadata"]["runner_report_observation"] == receipt
    assert all(item["artifact_id"] != receipt["artifact_id"] for task in after["tasks"] for item in task["artifacts"])
    content = h.http.get(artifact["uri"])
    assert content.status_code == 200, content.text
    assert content.json() == CompatibilityRunnerTaskReportRequest(**late_payload(h)).model_dump(mode="json")
    # A normalized alias/reordered object is the same report, not a second observation.
    committed = h.state_path.read_bytes()
    repeated = h.http.post(report_path(), json={**late_payload(h), "status": "success"})
    assert repeated.status_code == response.status_code
    assert repeated.json()["observation"] == receipt
    assert h.state_path.read_bytes() == committed
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    listed = h.http.get(f"{PREFIX}/artifacts", params={"task_id": "task", "artifact_type": OBSERVATION_TYPE})
    assert listed.status_code == 200 and listed.json() == [artifact]
    assert h.http.get(artifact["uri"]).json() == content.json()
    task = h.http.get(f"{PREFIX}/tasks/task/snapshot").json()
    assert task["result"] == before["tasks"][0]["result"]
    assert task["artifacts"] == before["tasks"][0]["artifacts"]


def test_runner_execution_identity_cannot_change_terminal_authority(connected):
    h = connected
    first = h.http.post(report_path(), json={
        **late_payload(h), "runner_execution_id": "external-execution-a",
        "result": {"summary": "Authoritative external result"},
    })
    assert first.status_code == 200, first.text
    assert h.http.get(f"{PREFIX}/tasks/task/snapshot").json()["metadata"]["runner_execution_id"] == "external-execution-a"

    late = h.http.post(report_path(), json={
        **late_payload(h), "runner_execution_id": "external-execution-b",
        "result": {"summary": "Different external operation"},
    })
    assert late.status_code == 409, late.text
    observation = late.json()["observation"]
    assert observation["runner_execution_id"] == "external-execution-b"
    snapshot = h.http.get(f"{PREFIX}/tasks/task/snapshot").json()
    assert snapshot["metadata"]["runner_execution_id"] == "external-execution-a"
    assert snapshot["result"]["summary"] == "Authoritative external result"


@pytest.mark.parametrize("changed", ["missing-assignment", "unknown-assignment", "wrong-runner", "unknown-runner"])
def test_unrecorded_identity_cannot_create_a_server_observation(connected, changed):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    payload = late_payload(h)
    runner_id = "original"
    if changed == "missing-assignment":
        payload.pop("assignment_id")
    elif changed == "unknown-assignment":
        payload["assignment_id"] = "unrecorded-assignment"
    else:
        runner_id = "other"
        if changed == "wrong-runner":
            assert h.http.post(f"{PREFIX}/runners/register", json={"runner_id": runner_id}).status_code == 200
    before = h.state_path.read_bytes()
    response = h.http.post(report_path(runner_id), json=payload)
    assert response.status_code == (404 if changed == "unknown-runner" else 409)
    assert "observation" not in response.json()
    assert h.state_path.read_bytes() == before


def test_observation_save_failure_rolls_back_and_returns_503_without_losing_authoritative_state(connected):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    committed = h.state_path.read_bytes()
    blocked = h.state_path.with_suffix(".tmp")
    blocked.mkdir()
    response = h.http.post(report_path(), json=late_payload(h))
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Compatibility runner state is unavailable."}
    assert h.state_path.read_bytes() == committed
    service = h.app.state.compatibility_runner_service
    assert [task.model_dump(mode="json") for task in service._tasks.values()] == json.loads(committed)["tasks"]
    assert [item.model_dump(mode="json") for item in service._artifacts.values()] == json.loads(committed)["artifacts"]
    blocked.rmdir()
    response = h.http.post(report_path(), json=late_payload(h))
    assert response.status_code == 409 and response.json().get("observation")


def test_distinct_late_reports_append_separate_evidence_instead_of_replacing_the_first(connected):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    before = persisted(h)
    first = h.http.post(report_path(), json=late_payload(h))
    second = h.http.post(report_path(), json={**late_payload(h), "result": {"summary": "Different late output"}})
    assert first.status_code == second.status_code == 409
    first_receipt, second_receipt = first.json().get("observation"), second.json().get("observation")
    assert first_receipt and second_receipt
    assert first_receipt["artifact_id"] != second_receipt["artifact_id"]
    assert first_receipt["report_receipt_sha256"] != second_receipt["report_receipt_sha256"]
    after = persisted(h)
    assert_authoritative_collections_unchanged(before, after)
    assert len(after["artifacts"]) == 2


@pytest.mark.parametrize("channel", ["report", "upload"])
def test_client_artifact_metadata_cannot_forge_a_server_observation_receipt(connected, channel):
    h = connected
    forged = {"runner_report_observation": {"authoritative": False, "artifact_id": "forged"},
        "__content_text": "private forged inline content"}
    if channel == "report":
        response = h.http.post(report_path(), json={**late_payload(h), "artifacts": [{
            "type": OBSERVATION_TYPE, "uri": "artifact://untrusted", "metadata": forged,
        }]})
        assert response.status_code == 200
        artifact = response.json()["artifacts"][0]
    else:
        response = h.http.post(f"{PREFIX}/runners/original/tasks/task/artifacts/upload", json={
            "assignment_id": h.first["metadata"]["assignment_id"], "filename": "ordinary.txt",
            "content_base64": "c2FmZQ==", "type": OBSERVATION_TYPE, "metadata": forged,
        })
        assert response.status_code == 200
        artifact = response.json()
        assert h.http.get(artifact["uri"]).content == b"safe"
    assert "runner_report_observation" not in artifact["metadata"]
    assert "__content_text" not in artifact["metadata"]


def test_dispatch_metadata_cannot_invent_a_historical_assignment_for_observation_admission(connected):
    h = connected
    service = h.app.state.compatibility_runner_service
    asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
        plan_id="forged-history-plan", tasks=[CompatibilityRunnerTask(
            task_id="forged-task", environment_id="local",
            metadata={"retry_history": [{"previous_assignment_id": "forged-assignment", "previous_runner_id": "original"}]},
            runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
        )],
    )))
    before = h.state_path.read_bytes()
    response = h.http.post(f"{PREFIX}/runners/original/tasks/forged-task/report", json={
        "assignment_id": "forged-assignment", "status": "completed", "result": {"summary": "Unrecorded attempt"},
    })
    assert response.status_code == 409
    assert "observation" not in response.json()
    assert h.state_path.read_bytes() == before
    assert service._tasks["forged-task"].metadata.get("retry_history", []) == []


def test_committed_observation_diagnostics_are_searchable_without_rendering_private_report(connected, caplog):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    caplog.set_level(logging.INFO, logger="src.application.compatibility.runner_service")
    response = h.http.post(report_path(), json=late_payload(h))
    receipt = response.json()["observation"]
    record = next(item for item in caplog.records if item.getMessage() == "compatibility_runner_report_observation_committed")
    ContextFilter().filter(record)
    rendered = ContextFormatter(LOG_FORMAT).format(record)
    diagnostics = json.loads(rendered.split(" diagnostics=", 1)[1])
    assert diagnostics == {
        "task_id": "task", "runner_id": "original", "assignment_id": receipt["assignment_id"],
        "artifact_id": receipt["artifact_id"], "task_status": "recovery_required", "recovery_reason": "assignment_outcome_unknown",
    }
    assert "private-late-report-body" not in rendered + caplog.text


@pytest.mark.parametrize("changed", [
    "content", "invalid-json", "noncanonical", "receipt", "assignment", "runner", "environment",
    "type", "mime", "uri", "timestamp", "backend", "missing-receipt", "missing-content", "scope", "authority", "missing-scope",
])
@pytest.mark.parametrize("port", ["content", "report"])
def test_drifted_persisted_observation_is_rejected_without_overwrite_or_private_diagnostics(connected, caplog, changed, port):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    receipt = h.http.post(report_path(), json=late_payload(h)).json()["observation"]
    state = persisted(h)
    artifact = state["artifacts"][0]
    metadata = artifact["metadata"]
    if changed == "content":
        payload = json.loads(metadata["__content_text"])
        payload["result"]["value"] = 99
        metadata["__content_text"] = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    elif changed == "invalid-json":
        metadata["__content_text"] = "{private-late-report-body"
    elif changed == "noncanonical":
        metadata["__content_text"] = json.dumps(json.loads(metadata["__content_text"]), indent=2)
    elif changed == "receipt":
        metadata["runner_report_observation"]["report_receipt_sha256"] = "0" * 64
    elif changed == "assignment":
        metadata["runner_report_observation"]["assignment_id"] = "another-assignment"
    elif changed in {"runner", "environment"}:
        artifact[f"{changed}_id"] = "different-reference"
    elif changed == "type":
        artifact["type"] = "ordinary"
    elif changed == "mime":
        artifact["mime_type"] = "text/plain"
    elif changed == "uri":
        artifact["uri"] = "artifact://unbound"
    elif changed == "timestamp":
        artifact["created_at"] = "changed-observation-timestamp"
    elif changed == "backend":
        metadata["storage_backend"] = "local"
    elif changed == "missing-scope":
        metadata["runner_report_observation"].pop("scope")
    elif changed.startswith("missing-"):
        metadata.pop("runner_report_observation" if changed == "missing-receipt" else "__content_text")
    elif changed == "scope":
        metadata["runner_report_observation"]["scope"] = "execution_authority"
    else:
        metadata["runner_report_observation"]["authoritative"] = True
    h.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    before = h.state_path.read_bytes()
    response = (h.http.post(report_path(), json=late_payload(h)) if port == "report" else
        h.http.get(f"{PREFIX}/artifacts/{receipt['artifact_id']}/content"))
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Compatibility runner state is unavailable."}
    assert "private-late-report-body" not in response.text + caplog.text
    assert "compatibility_runner_report_observation_invalid" in caplog.text
    assert h.state_path.read_bytes() == before
    assert persisted(h) == state


@pytest.mark.parametrize("changed", [
    "scope", "authoritative", "execution_authority", "extra", "frozen", "authoritative-zero", "execution-zero", "schema-bool",
])
def test_observation_receipt_has_narrow_immutable_permissions(connected, changed):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    raw = h.http.post(report_path(), json=late_payload(h)).json()["observation"]
    receipt = CompatibilityRunnerReportObservation.model_validate(raw)
    if changed == "frozen":
        with pytest.raises(ValidationError):
            receipt.assignment_id = "replacement"
        assert receipt.model_dump(mode="json") == raw
        return
    if changed in {"authoritative-zero", "execution-zero"}:
        raw["authoritative" if changed == "authoritative-zero" else "execution_authority"] = 0
    elif changed == "schema-bool":
        raw["schema_version"] = True
    else:
        raw[changed] = "unknown-scope" if changed == "scope" else True
    with pytest.raises(ValidationError):
        CompatibilityRunnerReportObservation.model_validate(raw)


def test_observation_id_collision_cannot_overwrite_a_different_original_report(connected, monkeypatch, caplog):
    h = connected
    service = CompatibilityRunnerService(settings=h.settings)
    h.app.state.compatibility_runner_service = service
    receipt = h.http.post(report_path(), json=late_payload(h)).json()["observation"]
    monkeypatch.setattr(service, "_report_observation_artifact_id", lambda *_: receipt["artifact_id"])
    before = h.state_path.read_bytes()
    response = h.http.post(report_path(), json={**late_payload(h), "result": {"value": "distinct-report"}})
    assert response.status_code == 503
    assert response.json() == {"detail": "Compatibility runner state is unavailable."}
    assert "compatibility_runner_report_observation_identity_conflict" in caplog.text
    assert "private-late-report-body" not in response.text + caplog.text
    assert h.state_path.read_bytes() == before


def test_saved_observation_survives_history_window_without_admitting_new_unrecorded_reports(connected):
    h = connected
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    receipt = h.http.post(report_path(), json=late_payload(h)).json()["observation"]
    assert h.http.post(f"{PREFIX}/tasks/requeue", json={"task_ids": ["task"], "statuses": ["recovery_required"]}).status_code == 200
    for _ in range(11):
        assert h.http.post(f"{PREFIX}/runners/original/tasks/poll").json()["tasks"][0]["task_id"] == "task"
        assert h.http.post(f"{PREFIX}/tasks/requeue", json={"task_ids": ["task"], "statuses": ["assigned"]}).status_code == 200
    state = persisted(h)
    assert len(state["tasks"][0]["metadata"]["retry_history"]) == 10
    assert not any(item["previous_assignment_id"] == receipt["assignment_id"] for item in state["tasks"][0]["metadata"]["retry_history"])
    before = h.state_path.read_bytes()
    repeated = h.http.post(report_path(), json=late_payload(h))
    assert repeated.status_code == 409 and repeated.json()["observation"] == receipt
    distinct = h.http.post(report_path(), json={**late_payload(h), "result": {"value": "new-unrecorded-content"}})
    assert distinct.status_code == 409 and "observation" not in distinct.json()
    assert h.state_path.read_bytes() == before


def test_normal_idempotent_report_never_becomes_an_observation_or_changes_aggregates(connected):
    h = connected
    first = h.http.post(report_path(), json=late_payload(h))
    assert first.status_code == 200
    committed = h.state_path.read_bytes()
    repeated = h.http.post(report_path(), json={**late_payload(h), "status": "success"})
    assert repeated.status_code == 200 and repeated.json() == first.json()
    assert h.state_path.read_bytes() == committed
    service = h.app.state.compatibility_runner_service
    before = asyncio.run(service.build_report()).model_dump(mode="json", exclude={"report_id", "generated_at"})
    rejected = h.http.post(report_path(), json={**late_payload(h), "status": "failed", "result": {"value": "late-failure"}})
    assert rejected.status_code == 409 and rejected.json().get("observation")
    after = asyncio.run(service.build_report()).model_dump(mode="json", exclude={"report_id", "generated_at"})
    assert after == before
    assert after["completed_tasks"] == 1 and after["failed_tasks"] == 0 and after["artifact_count"] == 0


def test_real_cli_keeps_conflict_and_full_report_after_late_observation_without_reexecuting(connected, tmp_path, monkeypatch, capsys):
    """Real CLI/SQLite/JSON and a controlled local effect, not a domain E3 run."""
    h = connected
    journal_path = tmp_path / "client.sqlite3"
    journal = RunnerJournal(journal_path, base_url="http://test", runner_id="original")
    journal.enqueue([h.first])
    journal.close()
    effects = tmp_path / "effects.txt"
    reports, processed = [], []

    def transport(_base, path, *, method="GET", payload=None):
        if path.endswith("/report") and not reports:
            # Output/upload and report_ready are durable before the restart.
            h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
        response = h.http.request(method, path, json=payload)
        if path.endswith("/report"):
            reports.append({"payload": deepcopy(payload), "body": response.json(), "status": response.status_code})
        if response.status_code >= 400:
            raise error.HTTPError(path, response.status_code, "controlled API rejection", {}, io.BytesIO(response.content))
        return response.json()

    def execute_once(_self, task):
        with effects.open("a", encoding="utf-8") as stream:
            stream.write(task["task_id"] + "\n")
        return {"status": "completed", "result": {"value": 7}, "metadata": {"private_fixture": "private-late-report-body"}}

    def forbid_acknowledgement(*_args):
        pytest.fail("A non-authoritative observation cannot ACK the local report")

    run_once = CompatibilityRunnerClient.run_once

    def track_run_once(client):
        result = run_once(client)
        processed.append(result)
        return result

    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    monkeypatch.setattr(CompatibilityRunnerClient, "execute_task", execute_once)
    monkeypatch.setattr(CompatibilityRunnerClient, "run_once", track_run_once)
    monkeypatch.setattr(RunnerJournal, "acknowledge", forbid_acknowledgement)
    monkeypatch.setattr(sys, "argv", ["runner-client", "--base-url", "http://test", "--runner-id", "original",
        "--capabilities", "local_browser", "--state-file", str(journal_path), "--once"])
    runner_client_module.main()
    journal = RunnerJournal(journal_path, base_url="http://test", runner_id="original")
    try:
        entries = journal.entries()
        assert len(entries) == 1 and entries[0]["phase"] == "conflict" and entries[0]["reason"] == "http_409"
        assert entries[0]["payload"] == reports[0]["payload"]
    finally:
        journal.close()
    assert reports[0]["status"] == 409 and reports[0]["body"]["observation"]["authoritative"] is False
    artifact_id = reports[0]["body"]["observation"]["artifact_id"]
    content = h.http.get(f"{PREFIX}/artifacts/{artifact_id}/content")
    assert content.status_code == 200
    assert content.json() == CompatibilityRunnerTaskReportRequest(**entries[0]["payload"]).model_dump(mode="json")
    runner_client_module.main()
    assert processed == [0, 0] and len(reports) == 1
    assert effects.read_text(encoding="utf-8").splitlines() == ["task"]
    journal = RunnerJournal(journal_path, base_url="http://test", runner_id="original")
    try:
        assert journal.entries() == entries
    finally:
        journal.close()
    output = capsys.readouterr().out
    assert "runner_delivery_conflict" in output and "reported task=" not in output
    assert "private-late-report-body" not in output
    task = h.http.get(f"{PREFIX}/tasks/task/snapshot").json()
    assert task["status"] == "recovery_required" and task["result"] == {}
    assert artifact_id not in [item["artifact_id"] for item in task["artifacts"]]

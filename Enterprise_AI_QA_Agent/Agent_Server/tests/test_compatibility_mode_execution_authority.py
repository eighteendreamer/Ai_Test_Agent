"""Mode bridge authority and returned evidence; isolated ASGI/JSON, not E3."""

from __future__ import annotations

import asyncio
import base64
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

from src.api.routes.compatibility import router
from src.application.compatibility import runner_client as runner_client_module
from src.application.compatibility.runner_client import CompatibilityRunnerClient
from src.application.compatibility.runner_journal import RunnerJournal
from src.application.compatibility.runner_service import (
    CompatibilityModeExecutionAuthorityLost, CompatibilityRunnerService, CompatibilityRunnerStateError,
)
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.modes.compatibility_testing_mode.contracts import CompatibilityDispatchPlan, CompatibilityRunnerTask, RunnerSelector
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.compatibility_runner import (
    CompatibilityModeExecutionReference, CompatibilityQueuedTask, CompatibilityArtifactUploadRequest,
    CompatibilityRunnerTaskReportRequest, CompatibilityTaskRequeueRequest,
)
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


PREFIX = "/api/v1/compatibility"
EXECUTE = f"{PREFIX}/runners/original/tasks/task/mode-calls/execute"
OBSERVATION_TYPE = "runner_mode_execution"


@pytest.fixture
def connected(tmp_path):
    settings = SimpleNamespace(data_dir=str(tmp_path / "data"),
        storage=SimpleNamespace(artifact_root_dir=str(tmp_path / "artifacts")),
        compatibility_runner_heartbeat_timeout_seconds=120)
    service = CompatibilityRunnerService(settings=settings)
    app = FastAPI()
    app.state.compatibility_runner_service = service
    app.state.tool_registry = SimpleNamespace(get=lambda key: SimpleNamespace(key=key, name="Controlled mode tool"))
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as http:
        assert http.post(f"{PREFIX}/runners/register", json={
            "runner_id": "original", "capabilities": ["local_browser"],
            "metadata": {"assignment_fencing_version": 1},
        }).status_code == 200
        asyncio.run(service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
            plan_id="mode-authority-plan", tasks=[CompatibilityRunnerTask(
                task_id="task", environment_id="local", case_ids=["case"],
                runner_selector=RunnerSelector(provider="local_browser", capabilities=["local_browser"]),
                mode_calls=[{"tool_key": "controlled-mode-tool", "reason": "Controlled authority contract", "arguments": {"step": index}} for index in range(2)],
            )],
        )))
        first = http.post(f"{PREFIX}/runners/original/tasks/poll").json()["tasks"][0]
        yield SimpleNamespace(http=http, app=app, service=service, settings=settings, first=first,
            state_path=service._state_path, effects=tmp_path / "effects.txt")


def state(h):
    return json.loads(h.state_path.read_text(encoding="utf-8"))


class ControlledRuntime:
    def __init__(self, h, *, position="normal", error=False, block_save=False):
        self.h, self.position, self.error, self.block_save = h, position, error, block_save
        self.calls, self.contexts = [], []
        self.after_interruption = None

    async def execute(self, tool, call, context):
        h = self.h
        self.calls.append(call)
        self.contexts.append(context)
        with h.effects.open("a", encoding="utf-8") as stream:
            stream.write(str(call.arguments["step"]) + "\n")
        if len(self.calls) == 1 and self.position != "normal":
            if self.position == "manual":
                await h.service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"]))
                await h.service.poll_tasks("original")
            elif self.position == "heartbeat":
                runner = h.service._runners["original"]
                h.service._runners["original"] = runner.model_copy(update={"heartbeat_at": "2000-01-01T00:00:00Z"})
                h.service._save_state()
                await h.service.list_tasks()
            elif self.position == "terminal":
                await h.service.report_task("original", "task", CompatibilityRunnerTaskReportRequest(
                    status="completed", assignment_id=h.first["metadata"]["assignment_id"],
                    result={"summary": "Original authoritative result"},
                ))
            else:
                h.service._runners.pop("original")
                h.service._save_state()
            self.after_interruption = state(h)
            if self.block_save:
                h.state_path.with_suffix(".tmp").mkdir()
            if self.error:
                raise RuntimeError("private-mode-runtime-error")
        return ToolExecutionRecord(call_id=call.id, tool_key=tool.key, tool_name=tool.name,
            status="completed", summary="Controlled returned mode result", input=call.arguments,
            output={"value": call.arguments["step"], "private_fixture": "private-mode-return-body"})


@pytest.mark.parametrize("position", ["manual", "heartbeat", "terminal", "runner-removed"])
@pytest.mark.parametrize("error", [False, True])
def test_losing_mode_execution_retains_output_without_dispatching_sibling_or_settling_new_attempt(connected, caplog, position, error):
    h = connected
    runtime = ControlledRuntime(h, position=position, error=error)
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == (404 if position == "runner-removed" else 409), response.text
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0"], "The superseded bridge dispatched another effect"
    receipt = response.json().get("observation")
    assert receipt is not None, "The original mode result disappeared after authority was lost"
    assert receipt["scope"] == "compatibility_runner_mode_execution_observation_only"
    assert receipt["authoritative"] is False and receipt["execution_authority"] is False
    assert receipt["assignment_id"] == h.first["metadata"]["assignment_id"]
    assert receipt["execution_id"]
    after = state(h)
    assert after["tasks"] == runtime.after_interruption["tasks"]
    assert after["runners"] == runtime.after_interruption["runners"]
    assert after["artifacts"][:-1] == runtime.after_interruption["artifacts"]
    artifact = after["artifacts"][-1]
    assert artifact["type"] == OBSERVATION_TYPE and artifact["artifact_id"] == receipt["artifact_id"]
    assert artifact["metadata"]["runner_mode_execution_observation"] == receipt
    assert all(receipt["artifact_id"] not in [item["artifact_id"] for item in task["artifacts"]] for task in after["tasks"])
    content = h.http.get(artifact["uri"])
    assert content.status_code == 200, content.text
    evidence = content.json()
    assert evidence["reference"]["execution_id"] == receipt["execution_id"]
    assert evidence["mode_calls"] == h.first["mode_calls"]
    assert evidence["error_type"] == ("RuntimeError" if error else None)
    results = evidence["response"]["mode_call_results"]
    assert len(results) == (0 if error else 1)
    if not error:
        assert results[0]["call_index"] == 0 and results[0]["output"]["value"] == 0
    assert evidence["response"]["ok"] is False
    assert "private-mode-return-body" not in response.text + caplog.text
    assert "private-mode-runtime-error" not in response.text + caplog.text
    h.app.state.compatibility_runner_service = CompatibilityRunnerService(settings=h.settings)
    assert h.http.get(artifact["uri"]).json() == evidence


def test_normal_bridge_returns_original_contract_and_persists_bound_queryable_output(connected):
    h = connected
    runtime = ControlledRuntime(h)
    h.app.state.tool_runtime_service = runtime
    params = {"assignment_id": h.first["metadata"]["assignment_id"]}
    response = h.http.post(EXECUTE, params=params)
    assert response.status_code == 200 and response.json()["ok"] is True
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0", "1"]
    persisted = state(h)
    task = persisted["tasks"][0]
    execution_id = task["metadata"].get("mode_calls_execution_id")
    assert execution_id, "A mode execution has no immutable operation identity"
    assert task["status"] == "assigned" and task["result"] == {} and task["artifacts"] == []
    assert task["metadata"]["mode_calls_execution_status"] == "completed"
    assert task["metadata"]["mode_calls_execution_result_count"] == 2
    artifacts = h.http.get(f"{PREFIX}/artifacts", params={"artifact_type": OBSERVATION_TYPE, "task_id": "task"}).json()
    assert len(artifacts) == 1
    content = h.http.get(artifacts[0]["uri"])
    assert content.status_code == 200 and content.json()["response"] == response.json()
    assert content.json()["reference"]["execution_id"] == execution_id
    assert artifacts[0]["metadata"]["runner_mode_execution_observation"]["execution_authority"] is False
    assert len({item.id for item in runtime.calls}) == 2
    assert all(execution_id in item.id for item in runtime.calls)
    before = h.state_path.read_bytes()
    repeated = h.http.post(EXECUTE, params=params)
    assert repeated.status_code == 409 and "already been executed" in repeated.json()["detail"]
    assert h.state_path.read_bytes() == before and len(runtime.calls) == 2
    report = asyncio.run(h.service.build_report())
    assert report.completed_tasks == 0 and report.artifact_count == 0


def test_late_mode_output_save_failure_is_503_and_rolls_back_without_running_sibling(connected):
    h = connected
    runtime = ControlledRuntime(h, position="manual", block_save=True)
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Compatibility runner state is unavailable."}
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0"]
    assert state(h) == runtime.after_interruption
    assert [item.model_dump(mode="json") for item in h.service._tasks.values()] == state(h)["tasks"]
    assert [item.model_dump(mode="json") for item in h.service._artifacts.values()] == state(h)["artifacts"]
    h.state_path.with_suffix(".tmp").rmdir()


def test_unexpected_mode_error_preserves_known_prefix_without_leaking_exception_body(connected, caplog):
    h = connected
    runtime = ControlledRuntime(h)
    execute = runtime.execute

    async def fail_second(tool, call, context):
        if call.arguments["step"] == 1:
            raise RuntimeError("private-mode-runtime-error")
        return await execute(tool, call, context)

    runtime.execute = fail_second
    h.app.state.tool_runtime_service = runtime
    caplog.set_level(logging.INFO, logger="src.api.routes.compatibility")
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 500
    assert response.json() == {"detail": "Mode call execution failed unexpectedly."}
    assert "private-mode-runtime-error" not in response.text + caplog.text
    artifacts = h.http.get(f"{PREFIX}/artifacts", params={"artifact_type": OBSERVATION_TYPE}).json()
    assert len(artifacts) == 1
    evidence = h.http.get(artifacts[0]["uri"]).json()
    assert evidence["error_type"] == "RuntimeError"
    assert len(evidence["response"]["mode_call_results"]) == 1
    assert evidence["response"]["ok"] is False and evidence["response"]["status"] == "failed"
    assert state(h)["tasks"][0]["metadata"]["mode_calls_execution_result_count"] == 1


def real_runtime(h, *, jobs):
    tool = ToolDescriptor(key="controlled-mode-tool", name="Controlled mode tool", description="test",
        category="test", permission_level="safe")
    h.app.state.tool_registry = SimpleNamespace(get=lambda key: tool)
    runtime = ToolRuntimeService(tool_job_service=jobs, compatibility_runner_service=h.service)
    h.app.state.tool_runtime_service = runtime
    return runtime


def test_real_runtime_rechecks_authority_after_tool_job_await_before_handler(connected):
    h = connected
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = real_runtime(h, jobs=jobs)
    create_job = jobs.create_job
    created, effects = [], []

    async def interrupted_create(**kwargs):
        job = await create_job(**kwargs)
        created.append(job)
        await h.service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"]))
        await h.service.poll_tasks("original")
        return job

    async def forbidden_handler(arguments, context):
        effects.append(arguments)
        return {"status": "completed", "summary": "Must not dispatch"}

    jobs.create_job = interrupted_create
    runtime._handlers["controlled-mode-tool"] = forbidden_handler
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 409, response.text
    assert effects == [] and len(created) == 1
    assert created[0].metadata["compatibility_mode_execution"]["assignment_id"] == h.first["metadata"]["assignment_id"]
    assert created[0].metadata["compatibility_mode_call_index"] == 0
    evidence = h.http.get(f'{PREFIX}/artifacts/{response.json()["observation"]["artifact_id"]}/content').json()
    assert evidence["response"]["mode_call_results"] == []
    # The bridge cannot infer a returned result or re-dispatch this old ToolJob.
    assert asyncio.run(jobs.get_job(created[0].id)).output_payload == {}


@pytest.mark.parametrize("with_jobs", [False, True])
def test_real_runtime_keeps_server_reference_through_context_reconstruction(connected, with_jobs):
    h = connected
    jobs = ToolJobService(InMemoryToolJobStore()) if with_jobs else None
    runtime = real_runtime(h, jobs=jobs)
    contexts = []

    async def handler(arguments, context):
        contexts.append(context)
        return {"status": "completed", "summary": "Controlled real runtime result"}

    runtime._handlers["controlled-mode-tool"] = handler
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200, response.text
    assert len(contexts) == 2
    for index, context in enumerate(contexts):
        reference = getattr(context, "compatibility_mode_execution", None)
        assert reference is not None, "Server execution identity was lost while rebuilding the context"
        assert reference.assignment_id == h.first["metadata"]["assignment_id"]
        assert context.compatibility_mode_call_index == index
        if jobs is not None:
            job = asyncio.run(jobs.get_job(context.tool_job_id))
            assert job.metadata["compatibility_mode_execution"] == reference.model_dump(mode="json")
            assert job.metadata["compatibility_mode_call_index"] == index


def saved_late_evidence(h):
    runtime = ControlledRuntime(h, position="manual")
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 409, response.text
    artifact_id = response.json()["observation"]["artifact_id"]
    return runtime, artifact_id, h.http.get(f"{PREFIX}/artifacts/{artifact_id}/content").json()


@pytest.mark.parametrize("drift", [
    "content", "digest", "reference", "definition", "binding", "artifact_type", "uri", "observed_at",
    "scope_missing", "authoritative_true", "authoritative_zero", "execution_authority_zero", "schema_bool",
])
def test_mode_observation_drift_is_503_without_exposing_private_content_or_rewriting(connected, caplog, drift):
    h = connected
    runtime, artifact_id, evidence = saved_late_evidence(h)
    artifact = h.service._artifacts[artifact_id].model_copy(deep=True)
    receipt = artifact.metadata["runner_mode_execution_observation"]
    if drift == "content":
        artifact.metadata["__content_text"] += " "
    elif drift == "digest":
        receipt["content_sha256"] = "0" * 64
    elif drift in {"reference", "definition"}:
        content = json.loads(artifact.metadata["__content_text"])
        if drift == "reference":
            content["reference"]["execution_id"] = "different-execution"
        else:
            content["mode_calls"][0]["arguments"]["step"] = 999
        artifact.metadata["__content_text"] = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    elif drift == "binding":
        artifact.dispatch_id = "different-dispatch"
    elif drift == "artifact_type":
        artifact.type = "evidence"
    elif drift == "uri":
        artifact.uri = "/invalid"
    elif drift == "observed_at":
        receipt["observed_at"] = "different-time"
    elif drift == "scope_missing":
        receipt.pop("scope")
    elif drift == "schema_bool":
        receipt["schema_version"] = True
    else:
        receipt["execution_authority" if drift == "execution_authority_zero" else "authoritative"] = (
            True if drift == "authoritative_true" else 0
        )
    h.service._artifacts[artifact_id] = artifact
    h.service._save_state()
    before = h.state_path.read_bytes()
    response = h.http.get(f"{PREFIX}/artifacts/{artifact_id}/content")
    assert response.status_code == 503 and response.json() == {"detail": "Compatibility runner state is unavailable."}
    assert h.state_path.read_bytes() == before
    assert "private-mode-return-body" not in response.text + caplog.text
    original = CompatibilityQueuedTask.model_validate(runtime.contexts[0].context_bundle["compatibility_task"])
    with pytest.raises(CompatibilityRunnerStateError):
        asyncio.run(h.service.complete_mode_call_execution(original, evidence["response"]))
    assert h.state_path.read_bytes() == before


def test_late_observation_same_content_reuses_original_receipt_but_different_content_appends(connected):
    h = connected
    runtime, artifact_id, evidence = saved_late_evidence(h)
    original = CompatibilityQueuedTask.model_validate(runtime.contexts[0].context_bundle["compatibility_task"])
    before = h.state_path.read_bytes()
    with pytest.raises(CompatibilityModeExecutionAuthorityLost) as repeated:
        asyncio.run(h.service.complete_mode_call_execution(original, evidence["response"]))
    assert repeated.value.observation.artifact_id == artifact_id
    assert h.state_path.read_bytes() == before
    other = deepcopy(evidence["response"])
    other["mode_call_results"][0]["output"]["value"] = "another returned observation"
    with pytest.raises(CompatibilityModeExecutionAuthorityLost) as appended:
        asyncio.run(h.service.complete_mode_call_execution(original, other))
    assert appended.value.observation.artifact_id != artifact_id
    after = state(h)
    assert after["tasks"] == json.loads(before)["tasks"] and after["runners"] == json.loads(before)["runners"]
    assert len(after["artifacts"]) == 2
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0"]


@pytest.mark.parametrize("field", ["mode_calls", "case_ids", "runner_selector"])
def test_definition_drift_during_await_stops_next_dispatch_and_preserves_original_definition(connected, field):
    h = connected
    runtime = ControlledRuntime(h)
    execute = runtime.execute
    after_drift = []

    async def change_definition(tool, call, context):
        record = await execute(tool, call, context)
        current = h.service._tasks["task"].model_copy(deep=True)
        if field == "mode_calls":
            current.mode_calls[1]["arguments"]["step"] = 999
        elif field == "case_ids":
            current.case_ids = ["different-case"]
        else:
            current.runner_selector["provider"] = "different-provider"
        h.service._tasks["task"] = current
        h.service._save_state()
        after_drift.append(state(h))
        return record

    runtime.execute = change_definition
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 409, response.text
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0"]
    assert state(h)["tasks"] == after_drift[0]["tasks"]
    artifact_id = response.json()["observation"]["artifact_id"]
    evidence = h.http.get(f"{PREFIX}/artifacts/{artifact_id}/content").json()
    assert evidence[field] == h.first[field]


@pytest.mark.parametrize("wrong", ["call_id", "name", "arguments", "boolean_arguments", "float_arguments", "index", "execution_id"])
def test_frozen_call_mismatch_rejects_without_state_changes(connected, wrong):
    h = connected
    original = asyncio.run(h.service.begin_mode_call_execution("original", "task", assignment_id=h.first["metadata"]["assignment_id"]))
    reference = CompatibilityModeExecutionReference.model_validate(original.metadata["mode_calls_execution_reference"])
    args = {"id": f"task-{reference.execution_id}-mode-call-1", "name": "controlled-mode-tool", "arguments": {"step": 0}}
    index = 0
    if wrong == "call_id":
        args["id"] = "other-call"
    elif wrong == "name":
        args["name"] = "other-tool"
    elif wrong == "arguments":
        args["arguments"] = {"step": 999}
    elif wrong == "boolean_arguments":
        args["arguments"] = {"step": False}
    elif wrong == "float_arguments":
        args["arguments"] = {"step": 0.0}
    elif wrong == "index":
        index = 1
    else:
        reference = reference.model_copy(update={"execution_id": "other-execution"})
    before = h.state_path.read_bytes()
    with pytest.raises(CompatibilityModeExecutionAuthorityLost):
        asyncio.run(h.service.assert_mode_call_execution(reference, call_index=index, call=ModelToolCall(**args)))
    assert h.state_path.read_bytes() == before


def test_reserved_tool_arguments_keep_existing_ignore_semantics_in_real_runtime(connected):
    h = connected
    task = h.service._tasks["task"].model_copy(deep=True)
    task.mode_calls[0]["arguments"]["_server_approval_granted"] = True
    h.service._tasks["task"] = task
    h.service._save_state()
    runtime = real_runtime(h, jobs=None)
    received = []

    async def handler(arguments, context):
        received.append(arguments)
        return {"status": "completed"}

    runtime._handlers["controlled-mode-tool"] = handler
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200 and response.json()["ok"] is True, response.text
    assert received == [{"step": 0}, {"step": 1}]


def test_execution_log_formatter_renders_identity_but_not_private_payload(connected):
    from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT

    record = logging.makeLogRecord({"name": "mode-execution", "levelno": logging.INFO,
        "msg": "compatibility_mode_execution_observed", "task_id": "task", "execution_id": "execution-test",
        "private_body": "private-mode-return-body"})
    ContextFilter().filter(record)
    rendered = ContextFormatter(LOG_FORMAT).format(record)
    assert '"execution_id":"execution-test"' in rendered
    assert "private-mode-return-body" not in rendered


@pytest.mark.parametrize("interruption", ["manual", "terminal"])
def test_final_call_return_must_revalidate_before_successful_settlement(connected, interruption):
    h = connected
    runtime = ControlledRuntime(h)
    execute = runtime.execute
    interrupted = []

    async def interrupt_last(tool, call, context):
        result = await execute(tool, call, context)
        if len(runtime.calls) == 2:
            if interruption == "manual":
                await h.service.requeue_tasks(CompatibilityTaskRequeueRequest(task_ids=["task"], statuses=["assigned"]))
                await h.service.poll_tasks("original")
            else:
                await h.service.report_task("original", "task", CompatibilityRunnerTaskReportRequest(
                    status="completed", assignment_id=h.first["metadata"]["assignment_id"], result={"summary": "Original result"},
                ))
            interrupted.append(state(h))
        return result

    runtime.execute = interrupt_last
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 409, response.text
    assert state(h)["tasks"] == interrupted[0]["tasks"] and state(h)["runners"] == interrupted[0]["runners"]
    artifact_id = response.json()["observation"]["artifact_id"]
    evidence = h.http.get(f"{PREFIX}/artifacts/{artifact_id}/content").json()
    assert len(evidence["response"]["mode_call_results"]) == 2 and evidence["response"]["ok"] is False


def test_normal_result_and_metadata_commit_failure_rolls_back_together(connected):
    h = connected
    runtime = ControlledRuntime(h)
    execute = runtime.execute
    before_commit = []

    async def block_final_commit(tool, call, context):
        result = await execute(tool, call, context)
        if len(runtime.calls) == 2:
            before_commit.append(state(h))
            h.state_path.with_suffix(".tmp").mkdir()
        return result

    runtime.execute = block_final_commit
    h.app.state.tool_runtime_service = runtime
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 503, response.text
    assert state(h) == before_commit[0] and not h.service._artifacts
    assert h.service._tasks["task"].model_dump(mode="json") == before_commit[0]["tasks"][0]
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0", "1"]
    h.state_path.with_suffix(".tmp").rmdir()


def test_new_explicit_assignment_has_disjoint_execution_and_call_ids(connected):
    h = connected
    original, _, _ = saved_late_evidence(h)
    new = ControlledRuntime(h)
    h.app.state.tool_runtime_service = new
    snapshot = asyncio.run(h.service.read_task_snapshot("task"))
    response = h.http.post(EXECUTE, params={"assignment_id": snapshot.metadata["assignment_id"]})
    assert response.status_code == 200 and response.json()["ok"] is True
    assert {call.id for call in original.calls}.isdisjoint(call.id for call in new.calls)
    old_id = original.contexts[0].compatibility_mode_execution.execution_id
    current = state(h)["tasks"][0]["metadata"]
    assert old_id != current["mode_calls_execution_id"]
    assert current["retry_history"][-1]["previous_mode_call_execution"]["mode_calls_execution_id"] == old_id


def test_begin_snapshot_is_detached_and_finish_requires_original_execution_id(connected):
    h = connected
    started = asyncio.run(h.service.begin_mode_call_execution("original", "task", assignment_id=h.first["metadata"]["assignment_id"]))
    started.mode_calls[0]["arguments"]["step"] = 999
    assert h.service._tasks["task"].mode_calls == h.first["mode_calls"]
    before = h.state_path.read_bytes()
    for identity in (None, "another-execution"):
        with pytest.raises(CompatibilityModeExecutionAuthorityLost):
            asyncio.run(h.service.finish_mode_call_execution("original", "task", status="completed", result_count=0,
                assignment_id=h.first["metadata"]["assignment_id"], execution_id=identity))
        assert h.state_path.read_bytes() == before


def test_external_metadata_cannot_supply_server_mode_identity_or_observation(connected):
    h = connected
    forged = {"mode_calls_execution_id": "forged", "mode_calls_execution_reference": {"execution_id": "forged"}}
    queued = asyncio.run(h.service.enqueue_dispatch_plan(CompatibilityDispatchPlan(
        plan_id="forgery-plan", tasks=[CompatibilityRunnerTask(task_id="unassigned", environment_id="other",
            runner_selector=RunnerSelector(provider="local_browser"), metadata={**forged, "user_note": "retained"})],
    )))[0]
    assert not set(forged) & queued.metadata.keys() and queued.metadata["user_note"] == "retained"
    artifact = asyncio.run(h.service.upload_task_artifact("original", "task", CompatibilityArtifactUploadRequest(
        assignment_id=h.first["metadata"]["assignment_id"], filename="note.txt", content_base64=base64.b64encode(b"safe").decode(),
        metadata={"runner_mode_execution_observation": {"execution_authority": True}, "__content_text": "private-forgery"},
    )))
    assert "runner_mode_execution_observation" not in artifact.metadata and "__content_text" not in artifact.metadata
    completed = asyncio.run(h.service.report_task("original", "task", CompatibilityRunnerTaskReportRequest(
        status="completed", assignment_id=h.first["metadata"]["assignment_id"], metadata=forged,
        artifacts=[{"type": "evidence", "metadata": {"runner_mode_execution_observation": {"authoritative": True}}}],
    )))
    assert not set(forged) & completed.metadata.keys()
    assert "runner_mode_execution_observation" not in completed.artifacts[-1].metadata


def test_real_tool_job_cannot_be_reentered_without_original_server_mode_identity(connected):
    h = connected
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = real_runtime(h, jobs=jobs)
    contexts = []

    async def handler(arguments, context):
        contexts.append(context)
        return {"status": "completed"}

    runtime._handlers["controlled-mode-tool"] = handler
    response = h.http.post(EXECUTE, params={"assignment_id": h.first["metadata"]["assignment_id"]})
    assert response.status_code == 200
    original = contexts[0]
    context = ToolExecutionContext(session_id=original.session_id, turn_id=original.turn_id, trace_id=original.trace_id,
        user_message="", normalized_input="", context_bundle={"compatibility_mode_execution": original.compatibility_mode_execution.model_dump(mode="json")},
        tool_job_id=original.tool_job_id)
    with pytest.raises(CompatibilityModeExecutionAuthorityLost):
        asyncio.run(runtime.execute(h.app.state.tool_registry.get("controlled-mode-tool"),
            ModelToolCall(id=original.call_id, name="controlled-mode-tool", arguments={"step": 0}), context))
    assert len(contexts) == 2


def test_real_cli_keeps_late_mode_execution_conflict_without_report_ack_or_reexecution(connected, tmp_path, monkeypatch, capsys):
    h = connected
    runtime = ControlledRuntime(h, position="manual")
    h.app.state.tool_runtime_service = runtime
    journal_path = tmp_path / "mode-client.sqlite3"
    journal = RunnerJournal(journal_path, base_url="http://test", runner_id="original")
    journal.enqueue([h.first])
    journal.close()
    requests, observations, processed = [], [], []

    def transport(_base, path, *, method="GET", payload=None):
        requests.append(path)
        response = h.http.request(method, path, json=payload)
        if "/mode-calls/execute" in path:
            observations.append(response.json())
        if response.status_code >= 400:
            raise error.HTTPError(path, response.status_code, "controlled API rejection", {}, io.BytesIO(response.content))
        return response.json()

    def forbidden_ack(*_args):
        pytest.fail("A mode observation is not an acknowledged Runner task result")

    run_once = CompatibilityRunnerClient.run_once

    def track_run_once(client):
        result = run_once(client)
        processed.append(result)
        return result

    monkeypatch.setattr(runner_client_module, "_json_request", transport)
    monkeypatch.setattr(CompatibilityRunnerClient, "run_once", track_run_once)
    monkeypatch.setattr(RunnerJournal, "acknowledge", forbidden_ack)
    monkeypatch.setattr(sys, "argv", ["runner-client", "--base-url", "http://test", "--runner-id", "original",
        "--capabilities", "local_browser", "--state-file", str(journal_path), "--executor", "server-mode-calls", "--once"])
    runner_client_module.main()
    runner_client_module.main()
    assert processed == [0, 0] and len(observations) == 1
    assert observations[0]["observation"]["execution_authority"] is False
    assert h.effects.read_text(encoding="utf-8").splitlines() == ["0"]
    assert not any(path.endswith(("/report", "/artifacts/upload")) for path in requests)
    journal = RunnerJournal(journal_path, base_url="http://test", runner_id="original")
    try:
        entries = journal.entries()
        assert len(entries) == 1 and entries[0]["phase"] == "conflict" and entries[0]["reason"] == "http_409"
        assert entries[0]["task"] == h.first
    finally:
        journal.close()
    output = capsys.readouterr().out
    assert "runner_delivery_conflict" in output and "reported task=" not in output
    assert "private-mode-return-body" not in output

"""Worker readback rejects conflicting identities and never uses chat fallback."""
import asyncio
from types import SimpleNamespace

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.modes.api_testing_mode.campaign_state import ApiTestTask, ExecutionPolicy, InputBinding
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.modes.api_testing_mode.worker_result_reader import ApiWorkerResultReader
from src.registry.tools import ToolRegistry
from src.runtime.postgres_api_credential_store import ApiCredentialReferenceError
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.session import SessionStatus
from src.schemas.tool_job import ToolJobStatus


async def environment():
    jobs = ToolJobService(InMemoryToolJobStore())
    task = ApiTestTask(task_id="read", method="GET", path="/orders", status="running", attempts=1,
        worker_session_id="child")
    session = SimpleNamespace(id="child", status=SessionStatus.completed, project_id="project", messages=[],
        metadata={"parent_session_id": "parent", "parent_turn_id": "turn", "task_id": "read",
            "dispatch_role": "api_execution_worker"})
    original = task.model_copy(update={"attempts": 0}).model_dump(mode="json")
    job = await jobs.create_job(tool=ToolRegistry().get("api-test-runner"), call_id="call",
        session_id="child", turn_id="child-turn", trace_id="trace",
        input_payload={"worker_action": "execute_task", "task": original})
    await jobs.mark_running(job)
    result = task.model_copy(update={"status": "completed", "response_status": 200,
        "response_body": {"id": 0, "flag": False}}).model_dump(mode="json")
    await jobs.mark_completed(job.id, summary="owned", output_payload={"task_result": result})
    return jobs, job, task, session, {"session_id": "parent", "turn_id": "turn"}


@pytest.mark.asyncio
async def test_original_result_is_copied_without_transcript_and_zero_false_are_preserved():
    jobs, job, task, session, context = await environment()
    result = await ApiWorkerResultReader(tool_job_service=jobs).read(session=session, task=task, parent_context=context)
    assert result["task_result"]["response_body"] == {"id": 0, "flag": False}
    result["task_result"]["response_body"]["id"] = "caller-change"
    assert (await jobs.get_job(job.id)).output_payload["task_result"]["response_body"]["id"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,error", [
    ("parent_session_id", "foreign", "scope_invalid"),
    ("parent_turn_id", "foreign", "scope_invalid"),
    ("dispatch_role", "foreign", "scope_invalid"),
    ("task_id", "other", "scope_invalid"),
])
async def test_child_owner_mismatch_rejects_result(field, value, error):
    jobs, _, task, session, context = await environment()
    session.metadata[field] = value
    with pytest.raises(ApiCredentialReferenceError, match=error):
        await ApiWorkerResultReader(tool_job_service=jobs).read(session=session, task=task, parent_context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,error", [
    ("method", "POST", "request_invalid"), ("path", "/foreign", "request_invalid"),
    ("attempts", 1, "attempt_invalid"),
])
async def test_original_request_conflict_rejects_result(field, value, error):
    jobs, job, task, session, context = await environment()
    job.input_payload["task"][field] = value
    with pytest.raises(ApiCredentialReferenceError, match=error):
        await ApiWorkerResultReader(tool_job_service=jobs).read(session=session, task=task, parent_context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("task_id", "other"), ("method", "POST"), ("attempts", 2)])
async def test_returned_task_cannot_replace_original_identity(field, value):
    jobs, job, task, session, context = await environment()
    job.output_payload["task_result"][field] = value
    with pytest.raises(ApiCredentialReferenceError, match="identity_invalid"):
        await ApiWorkerResultReader(tool_job_service=jobs).read(session=session, task=task, parent_context=context)


@pytest.mark.asyncio
async def test_duplicate_original_jobs_are_ambiguous_not_latest_wins():
    jobs, job, task, session, context = await environment()
    await jobs.create_job(tool=ToolRegistry().get("api-test-runner"), call_id="duplicate",
        session_id="child", turn_id="child-turn", trace_id="trace", input_payload=job.input_payload)
    with pytest.raises(ApiCredentialReferenceError, match="ambiguous"):
        await ApiWorkerResultReader(tool_job_service=jobs).read(session=session, task=task, parent_context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ToolJobStatus.running, ToolJobStatus.waiting_approval, ToolJobStatus.denied])
async def test_unsettled_or_denied_job_is_not_success_and_restored_parent_stops(status):
    jobs, job, task, session, context = await environment()
    job.status = status
    assert await ApiWorkerResultReader(tool_job_service=jobs).read(session=session, task=task, parent_context=context) is None
    dispatches = []
    class Dispatcher:
        async def dispatch(self, payload, context):
            dispatches.append(payload)
            return {}
    class Sessions:
        async def get_session(self, identity):
            return session
    next_task = ApiTestTask(task_id="new-write", method="POST", path="/orders", status="ready")
    coordinator = ApiSubagentCoordinator(pool=ApiTaskPool([task, next_task]), policy=ExecutionPolicy(),
        coordinator_runtime_service=Dispatcher(), session_store=Sessions(), parent_context=context,
        credential_manager=CredentialManager(), tool_job_service=jobs)
    await coordinator.run_all()
    assert coordinator.has_unsettled_workers
    assert dispatches == []
    assert next_task.status == "ready"


@pytest.mark.asyncio
async def test_recreated_completed_outputs_supply_downstream_zero_value():
    completed = ApiTestTask(task_id="created", method="POST", path="/orders", status="completed", response_body={"id": 0})
    target = ApiTestTask(task_id="read", method="GET", path="/orders/{id}", depends_on=["created"],
        input_bindings=[InputBinding(source_task_id="created", source_path="id", target_path="path.id")])
    coordinator = ApiSubagentCoordinator(pool=ApiTaskPool([completed, target]), policy=ExecutionPolicy(),
        coordinator_runtime_service=None, session_store=None, parent_context={}, credential_manager=CredentialManager())
    coordinator._apply_input_bindings(target)
    assert target.path == "/orders/0"


@pytest.mark.asyncio
async def test_restored_result_read_has_existing_worker_deadline_and_no_dispatch():
    jobs, _, task, _, context = await environment()
    class HangingSessions:
        async def get_session(self, identity):
            await asyncio.Event().wait()
    coordinator = ApiSubagentCoordinator(pool=ApiTaskPool([task]), policy=ExecutionPolicy(request_timeout_seconds=0.05),
        coordinator_runtime_service=None, session_store=HangingSessions(), parent_context=context,
        credential_manager=CredentialManager(), tool_job_service=jobs, poll_interval_seconds=0.1)
    result = await asyncio.wait_for(coordinator.run_all(), timeout=1)
    assert result[0].last_error.startswith("worker_unsettled:")
    assert coordinator.has_unsettled_workers

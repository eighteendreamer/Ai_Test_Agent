"""Actual auth HTTP through both coordinators; controlled worker launching only."""
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from src.domain.models import SessionRecord
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.projects.project_store import PostgresProjectStore
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.modes.api_testing_mode.campaign_state import ApiTestTask, EndpointCandidate, ExecutionPolicy
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.precondition_resolver import PreconditionAnalysis
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import ChatMessage, MessageRole, RuntimeMode, SessionMode, SessionStatus


@contextmanager
def auth_target():
    old, current = uuid4().hex, uuid4().hex
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path == "/login":
                self.reply(200, {"access_token": old})
            elif self.path == "/auth/refresh":
                accepted = self.headers.get("Authorization") == "Bearer " + old
                self.reply(200 if accepted else 401, {"access_token": current} if accepted else {})
            else:
                self.reply(404, {})

        def do_GET(self):
            accepted = (self.headers.get("Authorization") == "Bearer " + current
                if self.path == "/profile" else not self.headers.get("Authorization"))
            self.reply(200 if accepted else 401, {"accepted": accepted})

        def reply(self, status, body):
            events.append({"method": self.command, "path": self.path, "status": status,
                "authorization_present": bool(self.headers.get("Authorization")),
                "old_credential": self.headers.get("Authorization") == "Bearer " + old,
                "current_credential": self.headers.get("Authorization") == "Bearer " + current})
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def test_actual_credential_session_container_stays_out_of_public_tool_output():
    credentials = CredentialManager().create_from_login_response(response_body={"access_token": uuid4().hex})
    raw = {"credential_session": credentials.model_dump(mode="json"), "task_result": {"auth_ref": credentials.credential_session_id},
        "usage": {"token": "ordinary-vocabulary-token"}}
    output, _, paths = OutputSafetyPolicy().sanitize_tool_output(raw)
    assert output["credential_session"] == "[REDACTED]"
    assert credentials.token not in json.dumps(output)
    assert output["task_result"]["auth_ref"] == credentials.credential_session_id
    assert output["usage"] == raw["usage"]
    assert paths == ["output.credential_session"]


class ControlledWorkerLauncher:
    """Consume real worker arguments with the production runner, then save its session."""
    def __init__(self, store, tool_runtime=None):
        self.store = store
        self.runner = ApiTestingModeRuntime(api_docs_service=None)
        self.task_ids = []
        self.tool_runtime = tool_runtime
        self.runs = []

    async def dispatch(self, payload, context):
        records = []
        for worker in payload["workers"]:
            arguments = json.loads(worker["prompt"].split("Tool arguments:\n", 1)[1].split(
                "\n\nAfter the tool", 1)[0])
            self.task_ids.append(worker["task_id"])
            identity, now = str(uuid4()), datetime.now(UTC)
            if self.tool_runtime is None:
                output = await self.runner.handle(arguments, None)
            else:
                await self.store.save_session(SessionRecord(id=identity, title="Owned API worker fixture",
                    status=SessionStatus.running, session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now))
                context = ToolExecutionContext(session_id=identity, turn_id=str(uuid4()), trace_id=str(uuid4()),
                    user_message="执行已有明确认证任务", normalized_input="执行已有明确认证任务", context_bundle={})
                call = ModelToolCall(id=str(uuid4()), name="api-test-runner", arguments=arguments)
                result = await self.tool_runtime.execute(ToolRegistry().get(call.name), call, context)
                self.runs.append((call, context, result))
                output = result.output
            await self.store.save_session(SessionRecord(id=identity, title="Owned API worker fixture",
                status=SessionStatus.completed, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
                messages=[ChatMessage(id=str(uuid4()), role=MessageRole.tool,
                    content=json.dumps(output), created_at=now, metadata={"tool_key": "api-test-runner"})]))
            records.append({"task_id": worker["task_id"], "child_session_id": identity, "status": "completed"})
        return {"ok": True, "workers": records}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["direct", "worker"])
@pytest.mark.parametrize("scenario", ["refresh", "no-broadcast"])
async def test_actual_worker_credentials_follow_completed_auth_source(tmp_path, backend, scenario):
    with auth_target() as (base, events):
        graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many([
            EndpointCandidate(endpoint_id=key, method=method, path=path, full_url=base+path)
            for key, method, path in [("login", "POST", "/login"), ("refresh", "POST", "/auth/refresh"),
                ("profile", "GET", "/profile")]]), preconditions=PreconditionAnalysis())
        if scenario == "no-broadcast":
            graph.tasks.append(ApiTestTask(task_id="public", method="GET", path=base+"/public", status="ready"))
        credentials, store = CredentialManager(), InMemorySessionStore()
        launcher = ControlledWorkerLauncher(store)
        pool = ApiTaskPool(graph.tasks)
        policy = ExecutionPolicy(max_workers=2, max_retries=0)
        if backend == "worker":
            coordinator = ApiSubagentCoordinator(pool=pool, policy=policy, coordinator_runtime_service=launcher,
                session_store=store, parent_context={"session_id": "owned-parent", "turn_id": "owned-turn"},
                credential_manager=credentials)
        else:
            coordinator = ApiTestCoordinator(pool=pool, policy=policy,
                executor_fn=ApiTaskExecutor(credential_manager=credentials).execute)
        results = await coordinator.run_all()
        facts = {"scope": "actual target HTTP, both production coordinators, actual worker runner; controlled launch and in-memory sessions",
            "backend": backend, "scenario": scenario, "events": events,
            "tasks": [{"task_id": t.task_id, "status": t.status, "response_status": t.response_status,
                "auth_ref": t.auth_ref, "depends_on": t.depends_on, "attempts": t.attempts} for t in results],
            "worker_dispatches": launcher.task_ids}
        if directory := os.getenv("QA_API_WORKER_AUTH_DIR"):
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            (output/f"{backend}-{scenario}.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")
        assert all(t.status == "completed" for t in results), facts
        assert events[1]["path"] == "/auth/refresh" and events[1]["old_credential"]
        profile = next(e for e in events if e["path"] == "/profile")
        assert profile["current_credential"] and not profile["old_credential"]
        if scenario == "no-broadcast":
            assert not next(e for e in events if e["path"] == "/public")["authorization_present"]
            assert pool.get("public").auth_ref == ""
        count = len(events)
        assert await coordinator.run_all() == results
        assert len(events) == count


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_WORKER_AUTH_STORAGE") != "1",
    reason="requires isolated real MySQL/PG/Redis/RustFS; controlled worker launch")
async def test_actual_tool_jobs_and_saved_worker_sessions_replay_refreshed_credentials(tmp_path):
    from test_execution_worker_cli import isolated_runtime
    output = Path(os.environ["QA_API_WORKER_AUTH_STORAGE_DIR"])
    output.mkdir(parents=True, exist_ok=False)
    with auth_target() as (base, events):
        async with isolated_runtime(tmp_path) as (settings, _, _, objects, owned):
            await PostgresProjectStore(settings).initialize()
            store = PostgresSessionStore(settings)
            await store.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=objects, settings=settings)
            launcher = ControlledWorkerLauncher(store, runtime)
            credentials = CredentialManager()
            graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many([
                EndpointCandidate(endpoint_id=key, method=method, path=path, full_url=base+path)
                for key, method, path in [("login", "POST", "/login"), ("refresh", "POST", "/auth/refresh"),
                    ("profile", "GET", "/profile")]]), preconditions=PreconditionAnalysis())
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(graph.tasks), policy=ExecutionPolicy(max_retries=0),
                coordinator_runtime_service=launcher, session_store=store, parent_context={"session_id": "owned-parent", "turn_id": "owned-turn"},
                credential_manager=credentials)
            try:
                results = await coordinator.run_all()
                # Retain the actual failed ToolRuntime boundary before isolated stores are removed.
                projection = []
                for _, _, result in launcher.runs:
                    job = await jobs.get_job(result.job_id)
                    credential_value = job.output_payload.get("credential_session")
                    credential = credential_value if isinstance(credential_value, dict) else {}
                    projection.append({"job_id": job.id, "status": job.status.value,
                        "response_status": job.output_payload["task_result"]["response_status"],
                        "credential_header_redacted": credential.get("headers", {}).get("Authorization") == "[REDACTED]",
                        "credential_container_redacted": credential_value == "[REDACTED]",
                        "credential_token_nonempty": bool(credential.get("token")),
                        "redacted_field_count": job.output_payload.get("_security", {}).get("redacted_field_count", 0)})
                (output/"observed-boundary.json").write_text(json.dumps({
                    "events": events, "jobs": projection,
                    "task_statuses": [t.status for t in results],
                    "scope": "actual ToolRuntime and PG worker sessions; controlled launch; no secret values exported"},
                    ensure_ascii=False, indent=2), encoding="utf8")
                assert [t.response_status for t in results] == [200, 200, 200]
                assert all(t.status == "completed" for t in results)
                assert results[1].auth_ref == results[2].auth_ref != results[0].auth_ref
                assert len(events) == len(launcher.runs) == 3
                artifacts, projected_jobs = [], []
                for call, context, result in launcher.runs:
                    job = await jobs.get_job_detail(result.job_id)
                    assert job.status.value == "completed"
                    assert job.output_payload["task_result"]["response_status"] == 200
                    for artifact in job.artifacts:
                        if artifact.path.startswith("rustfs://"):
                            owned.append(artifact.path)
                            raw = (await objects.read_object_uri(artifact.path))["content"]
                            artifacts.append({"job_id": job.id, "uri": artifact.path,
                                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                    # Recreate services from durable stores, without credential regeneration.
                    reader = PostgresSessionStore(settings)
                    saved = await reader.get_session(context.session_id)
                    restored_launcher = ControlledWorkerLauncher(reader)
                    restored = ApiSubagentCoordinator(pool=ApiTaskPool([]), policy=ExecutionPolicy(),
                        coordinator_runtime_service=restored_launcher, session_store=reader,
                        parent_context={}, credential_manager=CredentialManager())
                    payload = restored._extract_api_test_runner_output(saved.messages)
                    assert payload["task_result"]["auth_ref"] == job.output_payload["task_result"]["auth_ref"]
                    replay_runtime = ToolRuntimeService(tool_job_service=ToolJobService(PostgresToolJobStore(settings)),
                        artifact_storage_service=objects, settings=settings)
                    replayed = await replay_runtime.execute(ToolRegistry().get(call.name), call, context)
                    assert replayed.job_id == job.id
                    assert replayed.output["task_result"] == job.output_payload["task_result"]
                    projected_jobs.append({"id": job.id, "session_id": job.session_id, "status": job.status.value,
                        "task_id": job.output_payload["task_result"]["task_id"], "response_status": 200,
                        "auth_ref": job.output_payload["task_result"]["auth_ref"], "replay_same_job": True})
                assert len(events) == 3
                (output/"verification.json").write_text(json.dumps({"scope": "actual ToolRuntime/ToolJob/PG sessions/RustFS/owned HTTP; controlled worker launch; no external model or independent API",
                    "events": events, "jobs": projected_jobs, "artifacts": artifacts,
                    "new_service_reads_original_sessions": True, "terminal_replay_new_http": 0,
                    "full_goal_complete": False}, ensure_ascii=False, indent=2), encoding="utf8")
            finally:
                # Register successful partial uploads even if an assertion fails.
                for _, _, result in launcher.runs:
                    job = await jobs.get_job_detail(result.job_id)
                    for artifact in job.artifacts:
                        if artifact.path.startswith("rustfs://") and artifact.path not in owned:
                            owned.append(artifact.path)

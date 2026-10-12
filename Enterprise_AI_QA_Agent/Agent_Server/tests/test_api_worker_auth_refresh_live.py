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
from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore, ApiCredentialReferenceError
from src.schemas.project import ProjectRecord
from src.infrastructure.postgres_runtime import postgres_connect
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
from src.modes.api_testing_mode.campaign_state import AssertionSpec


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
        self.prompts = []

    async def dispatch(self, payload, context):
        records = []
        for worker in payload["workers"]:
            self.prompts.append(worker["prompt"])
            arguments = json.loads(worker["prompt"].split("Tool arguments:\n", 1)[1].split(
                "\n\nAfter the tool", 1)[0])
            self.task_ids.append(worker["task_id"])
            identity, now = str(uuid4()), datetime.now(UTC)
            if self.tool_runtime is None:
                output = await self.runner.handle(arguments, None)
            else:
                parent = await self.store.get_session(context["session_id"])
                metadata = {"parent_session_id": parent.id, "parent_turn_id": context["turn_id"],
                    "task_id": worker["task_id"], "dispatch_role": "api_execution_worker"}
                await self.store.mutate_metadata(parent.id, lambda stored:
                    stored.setdefault("worker_dispatches", []).append({**metadata, "child_session_id": identity}))
                await self.store.save_session(SessionRecord(id=identity, title="Owned API worker fixture",
                    status=SessionStatus.running, session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
                    project_id=parent.project_id, metadata=metadata))
                execution_context = ToolExecutionContext(session_id=identity, turn_id=str(uuid4()), trace_id=str(uuid4()),
                    user_message="执行已有明确认证任务", normalized_input="执行已有明确认证任务", context_bundle={})
                call = ModelToolCall(id=str(uuid4()), name="api-test-runner", arguments=arguments)
                result = await self.tool_runtime.execute(ToolRegistry().get(call.name), call, execution_context)
                self.runs.append((call, execution_context, result))
                output = result.output
            saved_child = await self.store.get_session(identity)
            await self.store.save_session(SessionRecord(id=identity, title="Owned API worker fixture",
                status=SessionStatus.completed, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
                project_id=saved_child.project_id if saved_child else None,
                metadata=saved_child.metadata if saved_child else {},
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
        assert all(isinstance(a, AssertionSpec) for t in results for a in t.assertions)
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
@pytest.mark.parametrize("include_public", [False, True])
async def test_actual_tool_jobs_and_saved_worker_sessions_replay_refreshed_credentials(tmp_path, include_public):
    from test_execution_worker_cli import isolated_runtime
    output = Path(os.environ["QA_API_WORKER_AUTH_STORAGE_DIR"]) / ("public" if include_public else "core")
    output.mkdir(parents=True, exist_ok=False)
    with auth_target() as (base, events):
        async with isolated_runtime(tmp_path) as (settings, _, _, objects, owned):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            store = PostgresSessionStore(settings)
            await store.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            private_store = PostgresApiCredentialStore(settings)
            references = ApiCredentialReferenceService(store=private_store, session_store=store)
            await references.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="owned-api-reference",
                name="Owned API credential fixture", created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Owned API parent", status=SessionStatus.running,
                session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
                mode_key="api_testing", project_id=project.id, created_at=now, updated_at=now)
            await store.save_session(parent)
            parent_context = {"session_id": parent.id, "turn_id": str(uuid4())}
            runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=objects, settings=settings,
                session_store=store, api_credential_reference_service=references)
            launcher = ControlledWorkerLauncher(store, runtime)
            credentials = CredentialManager()
            graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many([
                EndpointCandidate(endpoint_id=key, method=method, path=path, full_url=base+path)
                for key, method, path in [("login", "POST", "/login"), ("refresh", "POST", "/auth/refresh"),
                    ("profile", "GET", "/profile")]]), preconditions=PreconditionAnalysis())
            if include_public:
                graph.tasks.append(ApiTestTask(task_id="public", method="GET", path=base+"/public", status="ready"))
            expected_count = 4 if include_public else 3
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(graph.tasks), policy=ExecutionPolicy(max_retries=0),
                coordinator_runtime_service=launcher, session_store=store, parent_context=parent_context,
                credential_manager=credentials, credential_reference_service=references)
            try:
                results = await coordinator.run_all()
                # Retain the actual failed ToolRuntime boundary before isolated stores are removed.
                projection = []
                for _, _, result in launcher.runs:
                    job = await jobs.get_job(result.job_id)
                    credential_value = job.output_payload.get("credential_session")
                    credential = credential_value if isinstance(credential_value, dict) else {}
                    projection.append({"job_id": job.id, "status": job.status.value,
                        "response_status": job.output_payload.get("task_result", {}).get("response_status"),
                        "error": job.output_payload.get("error"),
                        "credential_header_redacted": credential.get("headers", {}).get("Authorization") == "[REDACTED]",
                        "credential_container_redacted": credential_value == "[REDACTED]",
                        "credential_token_nonempty": bool(credential.get("token")),
                        "redacted_field_count": job.output_payload.get("_security", {}).get("redacted_field_count", 0)})
                (output/"observed-boundary.json").write_text(json.dumps({
                    "events": events, "jobs": projection,
                    "task_statuses": [t.status for t in results],
                    "scope": "actual ToolRuntime and PG worker sessions; controlled launch; no secret values exported"},
                    ensure_ascii=False, indent=2), encoding="utf8")
                assert [t.response_status for t in results] == [200] * expected_count
                assert all(t.status == "completed" for t in results)
                assert results[1].auth_ref == results[2].auth_ref != results[0].auth_ref
                assert len(events) == len(launcher.runs) == expected_count
                if include_public:
                    assert not next(e for e in events if e["path"] == "/public")["authorization_present"]
                    assert results[3].auth_ref == ""
                assert all(set(call.arguments) == {"worker_action", "dispatch_ref"} for call, _, _ in launcher.runs)
                secrets = [credentials.get(t.auth_ref).token for t in results if t.auth_ref]
                assert all(secret not in prompt for secret in secrets for prompt in launcher.prompts)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    ciphertexts = []
                    for table in private_store.tables:
                        cur.execute(f"SELECT ciphertext FROM {table}")
                        ciphertexts.extend(row["ciphertext"] for row in cur.fetchall())
                    assert all(secret not in text for secret in secrets for text in ciphertexts)
                artifacts, projected_jobs = [], []
                for call, context, result in launcher.runs:
                    job = await jobs.get_job_detail(result.job_id)
                    assert job.status.value == "completed"
                    assert job.output_payload["task_result"]["response_status"] == 200
                    for artifact in job.artifacts:
                        if artifact.path.startswith("rustfs://"):
                            owned.append(artifact.path)
                            raw = (await objects.read_object_uri(artifact.path))["content"]
                            assert all(secret.encode() not in raw for secret in secrets)
                            artifacts.append({"job_id": job.id, "uri": artifact.path,
                                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                    # Recreate services from durable stores, without credential regeneration.
                    reader = PostgresSessionStore(settings)
                    saved = await reader.get_session(context.session_id)
                    assert all(secret not in json.dumps(job.output_payload) + json.dumps(job.input_payload)
                        + json.dumps([m.model_dump(mode="json") for m in saved.messages]) for secret in secrets)
                    new_references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=reader)
                    frozen, restored_credential, original_parent = await new_references.resolve_dispatch(call.arguments["dispatch_ref"], context)
                    assert frozen["task"]["task_id"] == job.output_payload["task_result"]["task_id"]
                    assert original_parent.id == parent.id
                    restored_launcher = ControlledWorkerLauncher(reader)
                    restored = ApiSubagentCoordinator(pool=ApiTaskPool([]), policy=ExecutionPolicy(),
                        coordinator_runtime_service=restored_launcher, session_store=reader,
                        parent_context={}, credential_manager=CredentialManager())
                    payload = restored._extract_api_test_runner_output(saved.messages)
                    assert payload["task_result"]["auth_ref"] == job.output_payload["task_result"]["auth_ref"]
                    replay_runtime = ToolRuntimeService(tool_job_service=ToolJobService(PostgresToolJobStore(settings)),
                        artifact_storage_service=objects, settings=settings, session_store=reader,
                        api_credential_reference_service=new_references)
                    from dataclasses import replace
                    replayed = await replay_runtime.execute(ToolRegistry().get(call.name), call,
                        replace(context, tool_job_id=job.id))
                    assert replayed.job_id == job.id
                    assert replayed.output["task_result"] == job.output_payload["task_result"]
                    projected_jobs.append({"id": job.id, "session_id": job.session_id, "status": job.status.value,
                        "task_id": job.output_payload["task_result"]["task_id"], "response_status": 200,
                        "auth_ref": job.output_payload["task_result"]["auth_ref"], "replay_same_job": True})
                assert len(events) == expected_count
                denied = {}
                sample_call, sample_context, _ = launcher.runs[1]
                for field in ("task", "credential_session", "execution_checkpoint", "auth_token_field"):
                    result = await ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references).handle(
                        {**sample_call.arguments, field: {}}, sample_context)
                    assert result["error"] == "api_worker_frozen_input_override" and result["http_request_sent"] is False
                    denied[field] = result["error"]
                wrong_settings = settings.model_copy(deep=True)
                from cryptography.fernet import Fernet
                wrong_settings.database.channel_credential_encryption_key = Fernet.generate_key().decode()
                wrong_service = ApiCredentialReferenceService(store=PostgresApiCredentialStore(wrong_settings), session_store=reader)
                result = await ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=wrong_service).handle(
                    sample_call.arguments, sample_context)
                assert result["error"] == "api_credential_decryption_failed" and not result["http_request_sent"]
                denied["wrong_key"] = result["error"]
                wrong_settings.database.channel_credential_encryption_key = ""
                no_key = ApiCredentialReferenceService(store=PostgresApiCredentialStore(wrong_settings), session_store=reader)
                result = await ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=no_key).handle(sample_call.arguments, sample_context)
                assert result["error"] == "api_credential_key_unavailable" and not result["http_request_sent"]
                denied["missing_key"] = result["error"]
                original_child = await reader.get_session(sample_context.session_id)
                for field, value in [("parent_turn_id", str(uuid4())), ("task_id", "different-task"),
                                     ("dispatch_role", "other-worker")]:
                    await reader.mutate_metadata(original_child.id, lambda meta, field=field, value=value: meta.update({field: value}))
                    result = await ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references).handle(sample_call.arguments, sample_context)
                    assert not result["http_request_sent"] and result["status"] == "failed"
                    denied[field] = result["error"]
                    await reader.mutate_metadata(original_child.id, lambda meta: meta.update(original_child.metadata))
                other_project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="other-api-reference",
                    name="Other owned project", created_at=now, updated_at=now))
                original_child.project_id = other_project.id
                await reader.save_session(original_child)
                result = await ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references).handle(sample_call.arguments, sample_context)
                assert result["error"] == "api_worker_project_scope_invalid" and not result["http_request_sent"]
                denied["project"] = result["error"]
                original_child.project_id = parent.project_id
                await reader.save_session(original_child)
                missing = await ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references).handle(
                    {"worker_action": "execute_task", "dispatch_ref": str(uuid4())}, sample_context)
                assert missing["error"] == "api_credential_reference_unavailable" and not missing["http_request_sent"]
                denied["missing_reference"] = missing["error"]
                frozen, _, _ = await references.resolve_dispatch(sample_call.arguments["dispatch_ref"], sample_context)
                same = await private_store.save_dispatch(frozen, owner_session_id=parent.id, project_id=parent.project_id)
                assert same == sample_call.arguments["dispatch_ref"]
                from copy import deepcopy
                conflicting = deepcopy(frozen)
                conflicting["task"]["path"] = base + "/unregistered-target"
                with pytest.raises(ApiCredentialReferenceError, match="api_credential_identity_conflict"):
                    await private_store.save_dispatch(conflicting, owner_session_id=parent.id, project_id=parent.project_id)
                assert await private_store.load_dispatch(same, owner_session_id=parent.id, project_id=parent.project_id) == frozen
                # A real second parent, even in the same project, cannot read the original credential.
                other_parent = SessionRecord(id=str(uuid4()), title="Other owned API parent", status=SessionStatus.running,
                    session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
                    mode_key="api_testing", project_id=parent.project_id, created_at=now, updated_at=now)
                await reader.save_session(other_parent)
                with pytest.raises(ApiCredentialReferenceError, match="api_credential_reference_unavailable"):
                    await references.load_parent_credential(results[1].auth_ref, {"session_id": other_parent.id})
                denied["parent_session"] = "api_credential_reference_unavailable"
                sample_job = await jobs.get_job(launcher.runs[1][2].job_id)
                terminal_runtime = ToolRuntimeService(tool_job_service=ToolJobService(PostgresToolJobStore(settings)),
                    artifact_storage_service=objects, settings=settings, session_store=reader,
                    api_credential_reference_service=references)
                for name, rejected_call, rejected_context in [
                    ("foreign_job", sample_call, replace(sample_context, session_id=other_parent.id, tool_job_id=sample_job.id)),
                    ("changed_job_input", sample_call.model_copy(update={"arguments": {**sample_call.arguments, "dispatch_ref": str(uuid4())}}),
                        replace(sample_context, tool_job_id=sample_job.id)),
                    ("missing_job", sample_call, replace(sample_context, tool_job_id=str(uuid4())))]:
                    rejected = await terminal_runtime.execute(ToolRegistry().get(sample_call.name), rejected_call, rejected_context)
                    assert rejected.status == "failed" and rejected.job_id is None and "task_result" not in rejected.output
                    denied[name] = "recovery_scope_rejected_without_saved_output"
                unchanged = await jobs.get_job(sample_job.id)
                assert unchanged.status == sample_job.status and unchanged.output_payload == sample_job.output_payload
                from src.modes.api_testing_mode.campaign_state import ApiTestingState
                from src.modes.api_testing_mode.contracts import STATE_METADATA_KEY, PHASE_REPORT_READY
                metadata_runtime = ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references)
                finished_state = ApiTestingState(phase=PHASE_REPORT_READY,
                    credential_session=credentials.get(results[1].auth_ref))
                state_payload = metadata_runtime._state_payload(finished_state)
                public_state = OutputSafetyPolicy().sanitize_tool_output({STATE_METADATA_KEY: state_payload})[0][STATE_METADATA_KEY]
                assert "credential_session" not in public_state
                assert all(secret not in json.dumps(public_state) for secret in secrets)
                parent_execution = ToolExecutionContext(session_id=parent.id, turn_id=parent_context["turn_id"], trace_id=str(uuid4()),
                    user_message="", normalized_input="", context_bundle={STATE_METADATA_KEY: public_state})
                await metadata_runtime.handle({}, parent_execution)
                assert metadata_runtime._credential_manager.get_latest().token == credentials.get(results[1].auth_ref).token
                assert len(events) == expected_count
                (output/"verification.json").write_text(json.dumps({"scope": "actual ToolRuntime/ToolJob/PG sessions/RustFS/owned HTTP; controlled worker launch; no external model or independent API",
                    "events": events, "jobs": projected_jobs, "artifacts": artifacts,
                    "new_service_reads_original_sessions": True, "terminal_replay_new_http": 0,
                    "encrypted_private_rows": len(ciphertexts), "opaque_worker_prompts": True,
                    "generated_credentials_absent_from_public_output": True, "denied_before_http": denied,
                    "fixed_dispatch_conflict_preserves_original": True, "parent_state_restored_from_reference": True,
                    "full_goal_complete": False}, ensure_ascii=False, indent=2), encoding="utf8")
            finally:
                # Register successful partial uploads even if an assertion fails.
                for _, _, result in launcher.runs:
                    job = await jobs.get_job_detail(result.job_id)
                    for artifact in job.artifacts:
                        if artifact.path.startswith("rustfs://") and artifact.path not in owned:
                            owned.append(artifact.path)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_WORKER_AUTH_STORAGE") != "1",
    reason="full parent ToolRuntime and encrypted Worker inputs require isolated real stores")
async def test_parent_campaign_private_state_report_and_original_job_recovery(tmp_path):
    from test_execution_worker_cli import isolated_runtime
    from src.modes.api_testing_mode.campaign_state import ApiTestingState, ApiTestCampaign
    from src.modes.api_testing_mode.contracts import STATE_METADATA_KEY, PHASE_CAMPAIGN_READY
    from dataclasses import replace
    output = Path(os.environ["QA_API_WORKER_AUTH_STORAGE_DIR"]) / "parent"
    output.mkdir(parents=True, exist_ok=False)
    with auth_target() as (base, events):
        async with isolated_runtime(tmp_path) as (settings, _, _, objects, owned):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="parent-reference",
                name="Parent campaign fixture", created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Parent API campaign", status=SessionStatus.running,
                session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
                mode_key="api_testing", project_id=project.id, created_at=now, updated_at=now)
            await sessions.save_session(parent)
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
            await references.initialize()
            runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=objects, settings=settings,
                session_store=sessions, api_credential_reference_service=references)
            launcher = ControlledWorkerLauncher(sessions, runtime)
            runtime.set_coordinator_runtime_service(launcher)
            graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many([
                EndpointCandidate(endpoint_id=key, method=method, path=path, full_url=base+path)
                for key, method, path in [("login", "POST", "/login"), ("refresh", "POST", "/auth/refresh"),
                    ("profile", "GET", "/profile")]]), preconditions=PreconditionAnalysis())
            state = ApiTestingState(phase=PHASE_CAMPAIGN_READY,
                campaign=ApiTestCampaign(campaign_id=str(uuid4()), project_name=project.name, tasks=graph.tasks,
                    execution_policy=ExecutionPolicy(max_retries=0)))
            context = ToolExecutionContext(session_id=parent.id, turn_id=str(uuid4()), trace_id=str(uuid4()),
                user_message="", normalized_input="", context_bundle={STATE_METADATA_KEY: state.model_dump(mode="json")})
            call = ModelToolCall(id=str(uuid4()), name="api-test-runner", arguments={})
            result = await runtime.execute(ToolRegistry().get(call.name), call, context)
            job = await jobs.get_job_detail(result.job_id)
            for artifact in job.artifacts:
                if artifact.path.startswith("rustfs://"):
                    owned.append(artifact.path)
            assert result.status == "completed" and result.output["report"]["completed"] == 3
            assert result.output["report"]["failed"] == 0 and len(events) == 3
            projected_state = result.output[STATE_METADATA_KEY]
            assert "credential_session" not in projected_state
            ref = projected_state["execution_checkpoint"]["credential_ref"]
            current = await references.load_parent_credential(ref, {"session_id": parent.id})
            assert current.token not in json.dumps(result.output) + json.dumps(context.context_bundle)
            assert current.token not in json.dumps(job.output_payload)
            artifacts = []
            for artifact in job.artifacts:
                if artifact.path.startswith("rustfs://"):
                    raw = (await objects.read_object_uri(artifact.path))["content"]
                else:
                    assert artifact.path == "inline://artifact" and artifact.metadata["__storage_mode"] == "inline_text"
                    raw = artifact.metadata["__content_text"].encode("utf8")
                assert current.token.encode() not in raw
                if artifact.artifact_type == "api_test_report_json":
                    report_content = json.loads(raw)
                    assert report_content["completed"] == 3 and report_content["failed"] == 0
                saved_filename = "report.json" if artifact.artifact_type == "api_test_report_json" else "report.md"
                (output / saved_filename).write_bytes(raw)
                artifacts.append({"id": artifact.id, "uri": artifact.path, "type": artifact.artifact_type,
                    "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "saved_filename": saved_filename})
            assert {a["type"] for a in artifacts} == {"api_test_report_json", "api_test_report_md"}
            reread = await ToolJobService(PostgresToolJobStore(settings)).get_job_detail(job.id)
            assert reread.artifacts == job.artifacts
            restored = ToolRuntimeService(tool_job_service=ToolJobService(PostgresToolJobStore(settings)),
                settings=settings, session_store=PostgresSessionStore(settings), artifact_storage_service=objects,
                api_credential_reference_service=ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings),
                    session_store=PostgresSessionStore(settings)))
            replayed = await restored.execute(ToolRegistry().get(call.name), call, replace(context, tool_job_id=job.id))
            assert replayed.job_id == job.id and replayed.output["report"] == result.output["report"]
            assert len(events) == 3 and len(launcher.runs) == 3
            (output / "verification.json").write_text(json.dumps({"scope": "full parent ToolRuntime, controlled child launch, actual PG inline report and fresh-service read; no external AI",
                "project_id": project.id, "parent_job_id": job.id, "events": events,
                "report": result.output["report"], "artifacts": artifacts,
                "public_state_uses_credential_ref": True, "terminal_replay_new_http": 0,
                "full_goal_complete": False}, ensure_ascii=False, indent=2), encoding="utf8")

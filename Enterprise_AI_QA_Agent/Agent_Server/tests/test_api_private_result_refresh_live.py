"""Refresh body bindings must consume private originals, never public redactions."""
from contextlib import contextmanager
from dataclasses import replace
import asyncio
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import sys
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from src.application.projects.project_store import PostgresProjectStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.domain.models import SessionRecord
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestingState, ApiTestTask, AssertionSpec, ExecutionPolicy, InputBinding
from src.modes.api_testing_mode.contracts import STATE_METADATA_KEY, PHASE_TASK_RUNNING, PHASE_CAMPAIGN_READY
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.postgres_api_credential_store import ApiCredentialReferenceError
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.schemas.project import ProjectRecord
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from test_execution_worker_cli import isolated_runtime


@contextmanager
def refresh_body_target():
    secrets = [uuid4().hex for _ in range(3)]
    access, refresh, renewed = secrets
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path == "/login":
                self.reply(200, {"access_token": access, "refresh_token": refresh}, body)
            elif self.path == "/auth/refresh":
                accepted = body.get("refresh_token") == refresh
                self.reply(200 if accepted else 401, {"access_token": renewed} if accepted else {}, body)
            else:
                self.reply(404, {}, body)

        def do_GET(self):
            accepted = self.path == "/profile" and self.headers.get("Authorization") == "Bearer " + renewed
            self.reply(200 if accepted else 401, {"accepted": accepted}, {})

        def reply(self, status, body, sent):
            events.append({"method": self.command, "path": self.path, "status": status,
                "refresh_body_matches": sent.get("refresh_token") == refresh,
                "redaction_sent": sent.get("refresh_token") == "[REDACTED]"})
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
        yield f"http://127.0.0.1:{server.server_port}", events, secrets
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def refresh_tasks(base):
    check = [AssertionSpec(kind="status_code", expected=200)]
    return [
        ApiTestTask(task_id="login", name="Login", method="POST", path="/login", full_url=base+"/login",
            execution_mode="auth", status="ready", assertions=check),
        ApiTestTask(task_id="refresh", name="Refresh", method="POST", path="/auth/refresh", full_url=base+"/auth/refresh",
            execution_mode="auth", status="blocked", depends_on=["login"], assertions=check, input_bindings=[
                InputBinding(source_task_id="login", source_path="refresh_token", target_path="body.refresh_token")]),
        ApiTestTask(task_id="profile", name="Profile", method="GET", path="/profile", full_url=base+"/profile",
            status="blocked", depends_on=["refresh"], assertions=check),
    ]


@pytest.mark.asyncio
async def test_direct_actual_refresh_body_uses_login_output():
    with refresh_body_target() as (base, events, _):
        manager = CredentialManager()
        coordinator = ApiTestCoordinator(pool=ApiTaskPool(refresh_tasks(base)), policy=ExecutionPolicy(max_retries=0),
            executor_fn=ApiTaskExecutor(credential_manager=manager).execute)
        results = await coordinator.run_all()
        assert [t.response_status for t in results] == [200, 200, 200]
        assert all(t.status == "completed" for t in results)
        assert events[1]["refresh_body_matches"] and len(events) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_after", [0, 1, 2])
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_PRIVATE_RESULTS") != "1", reason="owned real PG/MySQL/Redis stores")
async def test_private_worker_actual_refresh_body_and_public_output(tmp_path, pause_after):
    evidence = Path(os.environ["QA_API_PRIVATE_RESULTS_DIR"]) / f"pause-{pause_after}"
    evidence.mkdir(parents=True, exist_ok=False)
    facts = {"external_ai_calls": 0, "controlled_worker_launch": True, "full_goal_complete": False}
    with refresh_body_target() as (base, events, secrets):
        try:
            async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
                projects = PostgresProjectStore(settings)
                await projects.initialize()
                sessions = PostgresSessionStore(settings)
                await sessions.initialize()
                now = datetime.now(UTC)
                project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="private-refresh",
                    name="Private refresh fixture", created_at=now, updated_at=now))
                parent = SessionRecord(id=str(uuid4()), title="Owned parent", project_id=project.id,
                    status=SessionStatus.running, session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now)
                await sessions.save_session(parent)
                jobs = ToolJobService(PostgresToolJobStore(settings))
                await jobs.initialize()
                references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
                await references.initialize()
                runtime = ToolRuntimeService(tool_job_service=jobs, session_store=sessions, settings=settings,
                    api_credential_reference_service=references)
                launcher = ControlledWorkerLauncher(sessions, runtime)
                context = {"session_id": parent.id, "turn_id": str(uuid4())}

                class BoundaryPause(Exception):
                    pass

                class PausingCoordinator(ApiSubagentCoordinator):
                    async def _dispatch_batch(self, batch):
                        await super()._dispatch_batch(batch)
                        if pause_after and len(events) == pause_after:
                            raise BoundaryPause()

                coordinator = PausingCoordinator(pool=ApiTaskPool(refresh_tasks(base)), policy=ExecutionPolicy(max_retries=0),
                    coordinator_runtime_service=launcher, session_store=sessions, parent_context=context,
                    credential_manager=CredentialManager(), credential_reference_service=references, tool_job_service=jobs)
                if pause_after:
                    with pytest.raises(BoundaryPause):
                        await coordinator.run_all()
                    before_jobs = [(await jobs.get_job(r.job_id)).model_dump(mode="json") for _, _, r in launcher.runs]
                    state = ApiTestingState(phase=PHASE_TASK_RUNNING, campaign=ApiTestCampaign(campaign_id=str(uuid4()),
                        project_id=project.id, tasks=coordinator._pool.all_tasks),
                        credential_session=coordinator._credential_manager.get_latest())
                    projection = ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references)
                    projection._credential_manager = coordinator._credential_manager
                    saved = projection._state_payload(state)
                    assert all(secret not in json.dumps(saved) for secret in secrets)
                    await sessions.mutate_metadata(parent.id, lambda meta: meta.update({STATE_METADATA_KEY: saved}))
                    # All read services are recreated from PG; completed Jobs and
                    # task verdicts are never reset to arrange this continuation.
                    sessions = PostgresSessionStore(settings)
                    jobs = ToolJobService(PostgresToolJobStore(settings))
                    references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
                    reread = await sessions.get_session(parent.id)
                    restored = ApiTestingState.model_validate(reread.metadata[STATE_METADATA_KEY])
                    coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(restored.campaign.tasks), policy=ExecutionPolicy(max_retries=0),
                        coordinator_runtime_service=launcher, session_store=sessions, parent_context=context,
                        credential_manager=CredentialManager(), credential_reference_service=references, tool_job_service=jobs)
                    results = await coordinator.run_all()
                    assert [(await jobs.get_job(j["id"])).model_dump(mode="json") for j in before_jobs] == before_jobs
                    facts["controlled_boundary_pg_checkpoint_new_services"] = pause_after
                else:
                    runtime.set_coordinator_runtime_service(launcher)
                    state = ApiTestingState(phase=PHASE_CAMPAIGN_READY, campaign=ApiTestCampaign(
                        campaign_id=str(uuid4()), project_id=project.id, project_name=project.name,
                        tasks=refresh_tasks(base), execution_policy=ExecutionPolicy(max_retries=0)))
                    parent_execution = ToolExecutionContext(session_id=parent.id, turn_id=context["turn_id"],
                        trace_id=str(uuid4()), user_message="", normalized_input="",
                        context_bundle={STATE_METADATA_KEY: state.model_dump(mode="json")})
                    parent_call = ModelToolCall(id=str(uuid4()), name="api-test-runner", arguments={})
                    parent_record = await runtime.execute(ToolRegistry().get(parent_call.name), parent_call, parent_execution)
                    parent_job = await jobs.get_job(parent_record.job_id)
                    assert parent_record.status == "completed" and parent_record.output["report"]["completed"] == 3
                    assert parent_record.output["report"]["failed"] == 0
                    public_parent = json.dumps({"record": parent_record.output, "job": parent_job.model_dump(mode="json"),
                        "context": parent_execution.context_bundle}, ensure_ascii=False, indent=2)
                    assert all(secret not in public_parent for secret in secrets)
                    (evidence/"public-parent.json").write_text(public_parent, encoding="utf8")
                    results = ApiTestingState.model_validate(parent_record.output[STATE_METADATA_KEY]).campaign.tasks
                    replayed_parent = await runtime.execute(ToolRegistry().get(parent_call.name), parent_call,
                        replace(parent_execution, tool_job_id=parent_job.id))
                    assert replayed_parent.output == parent_record.output and len(events) == 3
                    facts["full_parent_toolruntime_report_and_snapshot_safe"] = True
                facts.update(statuses=[t.status for t in results], response_statuses=[t.response_status for t in results])
                assert [t.response_status for t in results] == [200, 200, 200], facts
                assert all(t.status == "completed" for t in results), facts
                assert events[1]["refresh_body_matches"] and len(events) == 3
                public = json.dumps({"jobs": [(await jobs.get_job(r.job_id)).model_dump(mode="json")
                    for _, _, r in launcher.runs], "prompts": launcher.prompts})
                assert all(secret not in public for secret in secrets)
                facts["public_credentials_absent"] = True
                ciphertexts = []
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    for table in PostgresApiCredentialStore(settings).tables:
                        cur.execute(f"SELECT ciphertext FROM {table}")
                        ciphertexts.extend(row["ciphertext"] for row in cur.fetchall())
                assert all(secret not in "".join(ciphertexts) for secret in secrets)
                facts["encrypted_rows"] = len(ciphertexts)
                originals = [(await jobs.get_job(r.job_id)).model_dump(mode="json") for _, _, r in launcher.runs]
                # Fresh completed parent readback and original child Job replay
                # must produce no additional login/refresh/business HTTP.
                completed = ApiSubagentCoordinator(pool=ApiTaskPool([t.model_copy(deep=True) for t in results]),
                    policy=ExecutionPolicy(max_retries=0), coordinator_runtime_service=launcher,
                    session_store=PostgresSessionStore(settings), parent_context=context, credential_manager=CredentialManager(),
                    credential_reference_service=references, tool_job_service=jobs)
                assert all(t.status == "completed" for t in await completed.run_all())
                for call, child_context, record in launcher.runs:
                    replayed = await runtime.execute(ToolRegistry().get(call.name), call,
                        replace(child_context, tool_job_id=record.job_id))
                    assert replayed.output == record.output
                assert len(events) == 3 and len(launcher.runs) == 3
                assert [(await jobs.get_job(j["id"])).model_dump(mode="json") for j in originals] == originals
                facts["terminal_replay_extra_http"] = 0
                # A ref from another child/Job of the same parent is invalid.
                sample_call, child_context, sample_record = launcher.runs[0]
                sample_job = await jobs.get_job(sample_record.job_id)
                child = await sessions.get_session(child_context.session_id)
                from src.modes.api_testing_mode.worker_result_reader import ApiWorkerResultReader
                tampered = results[0].model_copy(deep=True)
                tampered.execution_checkpoint["private_result_ref"] = str(uuid4())
                with pytest.raises(ApiCredentialReferenceError, match="reference_conflict"):
                    await ApiWorkerResultReader(tool_job_service=jobs, credential_reference_service=references).read(
                        session=child, task=tampered, parent_context=context)
                missing_job_identity = await ApiTestingModeRuntime(api_docs_service=None,
                    credential_reference_service=references).handle(sample_call.arguments, child_context)
                assert missing_job_identity["error"] == "api_worker_result_job_identity_missing"
                assert missing_job_identity["http_request_sent"] is False and len(events) == 3
                with pytest.raises(ApiCredentialReferenceError, match="binding_invalid"):
                    await references.load_worker_result(launcher.runs[1][2].output["private_result_ref"],
                        dispatch_ref=sample_call.arguments["dispatch_ref"], job=sample_job, session=child, parent_context=context)
                with pytest.raises(ApiCredentialReferenceError, match="scope_invalid"):
                    await references.load_worker_result(sample_record.output["private_result_ref"],
                        dispatch_ref=sample_call.arguments["dispatch_ref"], job=sample_job, session=child,
                        parent_context={**context, "session_id": str(uuid4())})
                facts["foreign_child_job_and_parent_rejected"] = True
                with pytest.raises(ApiCredentialReferenceError, match="scope_invalid"):
                    await references.load_worker_result(sample_record.output["private_result_ref"],
                        dispatch_ref=sample_call.arguments["dispatch_ref"], job=sample_job, session=child,
                        parent_context={**context, "turn_id": str(uuid4())})
                other_project_child = replace(child, project_id=str(uuid4()))
                with pytest.raises(ApiCredentialReferenceError, match="scope_invalid"):
                    await references.load_worker_result(sample_record.output["private_result_ref"],
                        dispatch_ref=sample_call.arguments["dispatch_ref"], job=sample_job, session=other_project_child,
                        parent_context=context)
                with pytest.raises(ApiCredentialReferenceError, match="reference_unavailable"):
                    await references.load_worker_result(str(uuid4()), dispatch_ref=sample_call.arguments["dispatch_ref"],
                        job=sample_job, session=child, parent_context=context)
                vault = PostgresApiCredentialStore(settings)
                original = await vault.load_worker_result(sample_record.output["private_result_ref"],
                    owner_session_id=parent.id, project_id=project.id)
                assert await vault.save_worker_result(original, owner_session_id=parent.id,
                    project_id=project.id) == sample_record.output["private_result_ref"]
                from copy import deepcopy
                conflicting = deepcopy(original)
                conflicting["task_result"]["response_status"] = 599
                with pytest.raises(ApiCredentialReferenceError, match="identity_conflict"):
                    await vault.save_worker_result(conflicting, owner_session_id=parent.id, project_id=project.id)
                assert await vault.load_worker_result(sample_record.output["private_result_ref"],
                    owner_session_id=parent.id, project_id=project.id) == original
                denied = []
                from cryptography.fernet import Fernet
                for name, key, error in [("missing_key", "", "key_unavailable"),
                    ("wrong_key", Fernet.generate_key().decode(), "decryption_failed")]:
                    invalid_settings = settings.model_copy(deep=True)
                    invalid_settings.database.channel_credential_encryption_key = key
                    invalid = ApiCredentialReferenceService(store=PostgresApiCredentialStore(invalid_settings), session_store=sessions)
                    with pytest.raises(ApiCredentialReferenceError, match=error):
                        await invalid.load_worker_result(sample_record.output["private_result_ref"],
                            dispatch_ref=sample_call.arguments["dispatch_ref"], job=sample_job, session=child, parent_context=context)
                    # A completed producer remains completed; its unreadable
                    # original stops all subsequent sends, including fresh reads.
                    guarded = ApiSubagentCoordinator(pool=ApiTaskPool([results[0].model_copy(deep=True),
                        ApiTestTask(task_id="new", method="GET", path="/profile", full_url=base+"/profile",
                            status="blocked", depends_on=["login"])]), policy=ExecutionPolicy(max_retries=0),
                        coordinator_runtime_service=launcher, session_store=sessions, parent_context=context,
                        credential_manager=CredentialManager(), credential_reference_service=invalid, tool_job_service=jobs)
                    stopped = await guarded.run_all()
                    assert stopped[0].status == "completed" and stopped[1].status == "blocked"
                    assert guarded.has_unsettled_workers and len(events) == 3
                    denied.append(name)
                facts.update(turn_and_project_mismatch_rejected=True, immutable_result_conflict_preserves_original=True,
                    unavailable_private_material_stops_without_replay=denied,
                    checkpoint_reference_conflict_rejected=True, missing_job_identity_rejected_before_http=True)
                (evidence/"public-jobs.json").write_text(json.dumps(originals, ensure_ascii=False, indent=2), encoding="utf8")
        finally:
            facts["events"] = events
            (evidence/"facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")


async def process_campaign(parent_id, turn_id, pause_after):
    """A real process with fresh production stores; only worker launch is controlled."""
    from src.core.config import get_settings
    settings = get_settings()
    sessions = PostgresSessionStore(settings)
    jobs = ToolJobService(PostgresToolJobStore(settings))
    references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
    await references.initialize()
    parent = await sessions.get_session(parent_id)
    state = ApiTestingState.model_validate(parent.metadata[STATE_METADATA_KEY])
    manager = CredentialManager()
    projection = ApiTestingModeRuntime(api_docs_service=None, credential_reference_service=references)
    projection._credential_manager = manager
    runtime = ToolRuntimeService(tool_job_service=jobs, session_store=sessions, settings=settings,
        api_credential_reference_service=references)
    launcher = ControlledWorkerLauncher(sessions, runtime)

    async def persist(tasks, parked):
        state.campaign.tasks = tasks
        state.credential_session = manager.get_latest()
        saved = projection._state_payload(state)
        await sessions.mutate_metadata(parent_id, lambda meta: meta.update({STATE_METADATA_KEY: saved,
            "private_result_probe": {"pid": os.getpid(), "parked": parked,
                "completed": sum(t.status == "completed" for t in tasks)}}))

    class ParkedCoordinator(ApiSubagentCoordinator):
        async def _dispatch_batch(self, batch):
            await super()._dispatch_batch(batch)
            if pause_after and len(self._pool.completed_tasks()) == pause_after:
                await persist(self._pool.all_tasks, True)
                print(json.dumps({"pid": os.getpid(), "parked_after": pause_after}), flush=True)
                await asyncio.Event().wait()  # Parent forcibly terminates this confirmed live process.

    coordinator = ParkedCoordinator(pool=ApiTaskPool(state.campaign.tasks), policy=ExecutionPolicy(max_retries=0),
        coordinator_runtime_service=launcher, session_store=sessions,
        parent_context={"session_id": parent_id, "turn_id": turn_id}, credential_manager=manager,
        credential_reference_service=references, tool_job_service=jobs)
    results = await coordinator.run_all()
    assert all(t.status == "completed" for t in results)
    await persist(results, False)
    print(json.dumps({"pid": os.getpid(), "completed": len(results), "new_dispatches": len(launcher.runs)}), flush=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_after", [1, 2])
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_PRIVATE_RESULTS") != "1", reason="isolated stores and real process kill/restart")
async def test_real_process_kill_restores_private_outputs_without_resending(tmp_path, pause_after):
    evidence = Path(os.environ["QA_API_PRIVATE_RESULTS_DIR"]) / f"process-{pause_after}"
    evidence.mkdir(parents=True, exist_ok=False)
    with refresh_body_target() as (base, events, secrets):
        async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
            await references.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="process-refresh",
                name="Process refresh fixture", created_at=now, updated_at=now))
            state = ApiTestingState(phase=PHASE_TASK_RUNNING, campaign=ApiTestCampaign(campaign_id=str(uuid4()),
                project_id=project.id, tasks=refresh_tasks(base)))
            parent = SessionRecord(id=str(uuid4()), title="Process parent", project_id=project.id,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
                metadata={STATE_METADATA_KEY: state.model_dump(mode="json")})
            await sessions.save_session(parent)
            turn_id = str(uuid4())
            args = (str(Path(__file__).resolve()), "--resume-private", parent.id, turn_id)
            child = processes.start("private-refresh-before", *args, str(pause_after))
            deadline = asyncio.get_running_loop().time() + 40
            while True:
                saved = await sessions.get_session(parent.id)
                if saved.metadata.get("private_result_probe", {}).get("parked"):
                    break
                assert child.poll() is None, (tmp_path/"private-refresh-before.stderr").read_text(encoding="utf8")
                assert asyncio.get_running_loop().time() < deadline, "private result checkpoint was not observed"
                await asyncio.sleep(.1)
            assert child.poll() is None and len(events) == pause_after
            old_jobs = [job.model_dump(mode="json") for receipt in saved.metadata["worker_dispatches"]
                for job in await jobs.list_jobs(session_id=receipt["child_session_id"])]
            assert all(job["status"] == "completed" for job in old_jobs)
            assert all(secret not in json.dumps(saved.metadata) for secret in secrets)
            child.kill()
            first_exit = await processes.wait(child, timeout=10)
            assert first_exit != 0
            second = processes.start("private-refresh-after", *args, "0")
            assert await processes.wait(second, timeout=40) == 0, (tmp_path/"private-refresh-after.stderr").read_text(encoding="utf8")
            final = await sessions.get_session(parent.id)
            assert final.metadata["private_result_probe"]["pid"] != saved.metadata["private_result_probe"]["pid"]
            assert final.metadata["private_result_probe"]["completed"] == 3
            assert len(events) == 3 and [e["status"] for e in events] == [200, 200, 200]
            assert [(await jobs.get_job(job["id"])).model_dump(mode="json") for job in old_jobs] == old_jobs
            replay = processes.start("private-refresh-replay", *args, "0")
            assert await processes.wait(replay, timeout=40) == 0
            assert len(events) == 3
            public = json.dumps(final.metadata, ensure_ascii=False, indent=2)
            assert all(secret not in public for secret in secrets)
            (evidence/"public-parent.json").write_text(public, encoding="utf8")
            for name in ("private-refresh-before", "private-refresh-after", "private-refresh-replay"):
                for suffix in ("stdout", "stderr"):
                    path = tmp_path/f"{name}.{suffix}"
                    assert all(secret not in path.read_text(encoding="utf8") for secret in secrets)
                    shutil.copyfile(path, evidence/path.name)
            (evidence/"facts.json").write_text(json.dumps({"events": events, "forced_exit_code": first_exit,
                "parked_after_completed": pause_after, "different_process_id": True, "terminal_replay_extra_http": 0,
                "original_jobs_unchanged": True, "public_credentials_absent": True,
                "scope": "actual forced process termination, PG checkpoint, fresh coordinator and stores; controlled worker launch",
                "external_ai_calls": 0, "full_goal_complete": False}, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    assert sys.argv[1] == "--resume-private"
    asyncio.run(process_campaign(sys.argv[2], sys.argv[3], int(sys.argv[4])))

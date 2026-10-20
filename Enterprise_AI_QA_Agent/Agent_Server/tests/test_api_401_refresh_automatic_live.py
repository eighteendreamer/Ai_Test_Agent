"""Real local HTTP acceptance for the explicit automatic 401 recovery contract."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from threading import Event, Thread
from uuid import uuid4
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.modes.api_testing_mode.auth_refresh import AuthRefreshContractError, mark_refresh_completed
from src.modes.api_testing_mode.campaign_state import ApiTestTask, AssertionSpec, ExecutionPolicy, InputBinding
from src.modes.api_testing_mode.capability_mapper import CAP_LOGIN, CAP_REFRESH_TOKEN
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestingState
from src.modes.api_testing_mode.contracts import PHASE_CAMPAIGN_READY, PHASE_TASK_RUNNING, STATE_METADATA_KEY
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.application.projects.project_store import PostgresProjectStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.domain.models import SessionRecord
from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.project import ProjectRecord
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from test_execution_worker_cli import isolated_runtime


@contextmanager
def automatic_target(*, replay_succeeds=True, refresh_succeeds=True):
    old, refresh_value, current = (uuid4().hex for _ in range(3))
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            body = json.loads(raw)
            event = {"method": "POST", "path": self.path,
                "authorization": self.headers.get("Authorization", ""), "body": body}
            requests.append(event)
            if self.path == "/login":
                self.reply(200, {"access_token": old, "refresh_token": refresh_value}, event)
            elif self.path == "/auth/refresh" and refresh_succeeds and body.get("refresh_token") == refresh_value:
                self.reply(200, {"access_token": current}, event)
            else:
                self.reply(401, {"ok": False}, event)

        def do_GET(self):
            event = {"method": "GET", "path": self.path,
                "authorization": self.headers.get("Authorization", ""), "body": None}
            requests.append(event)
            accepted = self.path == "/profile" and replay_succeeds and self.headers.get("Authorization") == "Bearer " + current
            self.reply(200 if accepted else 401, {"ok": accepted}, event)

        def reply(self, status, body, event):
            event["status"] = status
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
        yield f"http://127.0.0.1:{server.server_port}", requests, (old, refresh_value, current)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@contextmanager
def timeout_retry_target(*, first_delay: float):
    requests = []
    delivered_attempts = []
    first_handler_finished = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            attempt = len(requests) + 1
            requests.append({"attempt": attempt, "path": self.path})
            try:
                if attempt == 1:
                    time.sleep(first_delay)
                body = json.dumps({"attempt": attempt, "ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                delivered_attempts.append(attempt)
            except (BrokenPipeError, ConnectionResetError):
                # The client should close the first slow read after its timeout.
                pass
            finally:
                if attempt == 1:
                    first_handler_finished.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, delivered_attempts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        assert first_handler_finished.wait(5), "slow target handler did not settle after client timeout"


def campaign_tasks(base):
    success = [AssertionSpec(kind="status_code", expected=200)]
    return [
        ApiTestTask(task_id="login", method="POST", path=base + "/login", full_url=base + "/login",
            capability=CAP_LOGIN, execution_mode="auth", status="ready", assertions=success),
        ApiTestTask(task_id="refresh", method="POST", path=base + "/auth/refresh", full_url=base + "/auth/refresh",
            capability=CAP_REFRESH_TOKEN, execution_mode="auth", status="blocked", depends_on=["login"], assertions=success,
            input_bindings=[InputBinding(source_task_id="login", source_path="refresh_token", target_path="body.refresh_token")]),
        ApiTestTask(task_id="profile", method="GET", path=base + "/profile", full_url=base + "/profile",
            execution_mode="read", status="blocked", depends_on=["login"], assertions=success,
            auth_refresh_task_id="refresh", allow_401_replay=True),
    ]


def redacted_events(events, secrets):
    """Keep HTTP shape in evidence without persisting fixture credentials."""
    redacted = deepcopy(events)
    for event in redacted:
        if event.get("authorization"):
            event["authorization"] = "[REDACTED]"
        body = event.get("body")
        if isinstance(body, dict) and body.get("refresh_token"):
            body["refresh_token"] = "[REDACTED]"
    assert all(secret not in json.dumps(redacted) for secret in secrets)
    return redacted


@pytest.mark.asyncio
async def test_explicit_readonly_401_refresh_replays_once_and_records_original_response():
    with automatic_target() as (base, requests, secrets):
        tasks = campaign_tasks(base)
        coordinator = ApiTestCoordinator(pool=ApiTaskPool(tasks), policy=ExecutionPolicy(max_retries=0),
            executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute)
        results = await coordinator.run_all()
        profile = next(task for task in results if task.task_id == "profile")
        assert [item["path"] for item in requests] == ["/login", "/profile", "/auth/refresh", "/profile"]
        assert [item["body"].get("refresh_token") for item in requests if item["path"] == "/auth/refresh"] == [secrets[1]]
        assert profile.status == "completed" and profile.response_status == 200
        audit = profile.execution_checkpoint["auth_refresh"]
        assert audit["state"] == "replay_completed"
        assert audit["original_401"]["response_status"] == 401
        assert audit["replay_attempt"] == 2
        assert next(task for task in results if task.task_id == "refresh").status == "completed"


@pytest.mark.asyncio
async def test_restart_after_refresh_completion_reuses_refresh_and_replays_once():
    """A durable completed refresh must not be sent again after restart."""
    with automatic_target() as (base, requests, _):
        tasks = campaign_tasks(base)
        manager = CredentialManager()
        executor = ApiTaskExecutor(credential_manager=manager).execute
        coordinator = ApiTestCoordinator(
            pool=ApiTaskPool(tasks), policy=ExecutionPolicy(max_retries=0), executor_fn=executor,
        )

        await coordinator._execute_one(tasks[0])
        await coordinator._execute_one(tasks[2])
        coordinator._pool.mark_ready("refresh")
        await coordinator._execute_one(tasks[1])
        tasks[2].auth_ref = tasks[1].auth_ref
        mark_refresh_completed(tasks[2], tasks[1])

        restored_tasks = deepcopy(tasks)
        restored_manager = CredentialManager()
        restored_manager.restore_session(manager.get(tasks[1].auth_ref))
        restored = ApiTestCoordinator(
            pool=ApiTaskPool(restored_tasks), policy=ExecutionPolicy(max_retries=0),
            executor_fn=ApiTaskExecutor(credential_manager=restored_manager).execute,
        )
        results = await restored.run_all()

        profile = next(task for task in results if task.task_id == "profile")
        refresh = next(task for task in results if task.task_id == "refresh")
        assert [item["path"] for item in requests] == ["/login", "/profile", "/auth/refresh", "/profile"]
        assert refresh.attempts == 1
        assert profile.status == "completed" and profile.response_status == 200
        assert profile.execution_checkpoint["auth_refresh"]["state"] == "replay_completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["expected-401", "write-target", "refresh-fails", "second-401"])
async def test_401_recovery_contract_never_replays_unsafe_or_negative_cases(variant):
    with automatic_target(replay_succeeds=variant != "second-401", refresh_succeeds=variant != "refresh-fails") as (base, requests, _):
        tasks = campaign_tasks(base)
        profile = tasks[-1]
        if variant == "expected-401":
            profile.auth_refresh_task_id = ""
            profile.allow_401_replay = False
            profile.assertions = [AssertionSpec(kind="status_code", expected=401)]
            tasks[1].status = "ready"  # It is an ordinary explicit task in this negative case.
        elif variant == "write-target":
            profile.method = "POST"
            profile.path = profile.full_url = base + "/profile"
        coordinator_error = None
        try:
            coordinator = ApiTestCoordinator(pool=ApiTaskPool(tasks), policy=ExecutionPolicy(max_retries=0),
                executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute)
        except AuthRefreshContractError as exc:
            coordinator_error = str(exc)
        if variant in {"write-target"}:
            assert coordinator_error and "read-only" in coordinator_error
            assert requests == []
            return
        assert coordinator_error is None
        results = await coordinator.run_all()
        profile = next(task for task in results if task.task_id == "profile")
        if variant == "expected-401":
            assert profile.status == "completed" and profile.response_status == 401
            assert [item["path"] for item in requests].count("/profile") == 1
        elif variant == "refresh-fails":
            assert profile.status == "failed" and profile.execution_checkpoint["auth_refresh"]["state"] == "refresh_failed"
            assert [item["path"] for item in requests].count("/profile") == 1
        else:
            assert profile.status == "failed" and profile.execution_checkpoint["auth_refresh"]["state"] == "replay_failed"
            assert [item["path"] for item in requests].count("/profile") == 2


def test_refresh_relation_rejects_missing_shared_auth_lineage_before_http():
    tasks = campaign_tasks("http://127.0.0.1:1")
    tasks[-1].depends_on = []
    with pytest.raises(AuthRefreshContractError, match="shared declared authentication"):
        ApiTestCoordinator(pool=ApiTaskPool(tasks), policy=ExecutionPolicy(max_retries=0),
            executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_401_AUTO_WORKER") != "1",
    reason="requires isolated PG/MySQL/Redis stores and controlled Worker launch")
async def test_worker_automatic_refresh_uses_same_contract_and_private_results(tmp_path):
    evidence = Path(os.environ["QA_API_401_AUTO_WORKER_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    with automatic_target() as (base, requests, secrets):
        async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
            await references.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="auto-401-worker",
                name="Automatic 401 Worker", created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Automatic refresh parent", project_id=project.id,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now)
            await sessions.save_session(parent)
            runtime = ToolRuntimeService(tool_job_service=jobs, session_store=sessions, settings=settings,
                api_credential_reference_service=references)
            launcher = ControlledWorkerLauncher(sessions, runtime)
            context = {"session_id": parent.id, "turn_id": str(uuid4())}
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(campaign_tasks(base)),
                policy=ExecutionPolicy(max_retries=0), coordinator_runtime_service=launcher,
                session_store=sessions, parent_context=context, credential_manager=CredentialManager(),
                credential_reference_service=references, tool_job_service=jobs)
            results = await coordinator.run_all()
            profile = next(task for task in results if task.task_id == "profile")
            assert [item["path"] for item in requests] == ["/login", "/profile", "/auth/refresh", "/profile"]
            assert profile.status == "completed" and profile.response_status == 200, [
                {"id": task.task_id, "status": task.status, "error": task.last_error,
                 "response_status": task.response_status,
                 "checkpoint": task.execution_checkpoint} for task in results]
            assert profile.execution_checkpoint["auth_refresh"]["state"] == "replay_completed"
            assert all(secret not in json.dumps(launcher.prompts) for secret in secrets)
            jobs_public = json.dumps([ (await jobs.get_job(record.job_id)).model_dump(mode="json")
                for _, _, record in launcher.runs], ensure_ascii=False)
            assert all(secret not in jobs_public for secret in secrets)
            (evidence / "verification.json").write_text(json.dumps({
                "events": redacted_events(requests, secrets), "statuses": [task.status for task in results],
                "private_public_boundary_safe": True, "external_ai_calls": 0,
                "full_goal_complete": False,
            }, ensure_ascii=False, indent=2), encoding="utf8")


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_401_AUTO_WORKER") != "1",
    reason="requires isolated PG/MySQL/Redis stores and controlled Worker launch")
async def test_worker_refresh_failure_is_reported_without_replaying_target(tmp_path):
    evidence = Path(os.environ["QA_API_401_AUTO_WORKER_DIR"]) / "refresh-failure-report"
    evidence.mkdir(parents=True, exist_ok=False)
    with automatic_target(refresh_succeeds=False) as (base, requests, secrets):
        async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(
                store=PostgresApiCredentialStore(settings), session_store=sessions,
            )
            await references.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(
                id=str(uuid4()), project_key="auto-401-refresh-failure",
                name="Automatic 401 refresh failure", created_at=now, updated_at=now,
            ))
            parent = SessionRecord(
                id=str(uuid4()), title="Automatic refresh failure parent", project_id=project.id,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
            )
            await sessions.save_session(parent)
            tool_runtime = ToolRuntimeService(
                tool_job_service=jobs, session_store=sessions, settings=settings,
                api_credential_reference_service=references,
            )
            launcher = ControlledWorkerLauncher(sessions, tool_runtime)
            runtime = ApiTestingModeRuntime(
                api_docs_service=None, settings=settings, coordinator_runtime_service=launcher,
                session_store=sessions, credential_reference_service=references, tool_job_service=jobs,
            )
            state = ApiTestingState(
                phase=PHASE_CAMPAIGN_READY,
                campaign=ApiTestCampaign(
                    campaign_id=str(uuid4()), project_id=project.id, project_name=project.name,
                    objective="登录后读取资料；首次401时刷新一次，刷新失败后不得重放资料请求",
                    tasks=campaign_tasks(base), execution_policy=ExecutionPolicy(max_retries=0),
                ),
            )
            context = SimpleNamespace(
                session_id=parent.id, turn_id=str(uuid4()), trace_id=str(uuid4()),
                selected_agent_key="api-testing-agent", selected_model_key="", context_bundle={},
            )

            await runtime._execute_campaign(state, context)
            output = runtime._build_output(state)
            task_by_id = {task.task_id: task for task in state.campaign.tasks}
            report_by_id = {task["task_id"]: task for task in state.report.tasks}
            public_payload = json.dumps(output, ensure_ascii=False)
            assert [event["path"] for event in requests] == [
                "/login", "/profile", "/auth/refresh",
            ]
            assert [event["status"] for event in requests] == [200, 401, 401]
            assert task_by_id["profile"].status == "failed"
            assert task_by_id["profile"].execution_checkpoint["auth_refresh"]["state"] == "refresh_failed"
            assert task_by_id["profile"].attempts == 1
            assert task_by_id["refresh"].status == "failed"
            assert state.phase == "report_ready"
            assert output["status"] == "completed" and output["phase"] == "report_ready"
            assert output["report"]["completed"] == 1
            assert output["report"]["failed"] == 2
            assert output["report"]["skipped"] == 0
            assert report_by_id["profile"]["status"] == "failed"
            assert report_by_id["profile"]["response_status"] == 401
            assert report_by_id["refresh"]["status"] == "failed"
            assert "/profile" in output["report_markdown"] and "401" in output["report_markdown"]
            assert all(secret not in public_payload for secret in secrets)
            (evidence / "verification.json").write_text(json.dumps({
                "events": redacted_events(requests, secrets),
                "campaign_phase": state.phase,
                "report_counts": {key: output["report"][key] for key in ("completed", "failed", "skipped")},
                "reported_profile_status": report_by_id["profile"]["status"],
                "reported_refresh_status": report_by_id["refresh"]["status"],
                "target_replayed": task_by_id["profile"].attempts > 1,
                "credentials_absent_from_public_output": True,
                "external_ai_calls": 0,
                "full_goal_complete": False,
            }, ensure_ascii=False, indent=2), encoding="utf8")


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_401_AUTO_WORKER") != "1",
    reason="requires isolated PG/MySQL/Redis stores and controlled Worker launch")
async def test_worker_campaign_timeout_retries_read_and_reports_final_result(tmp_path):
    evidence = Path(os.environ["QA_API_401_AUTO_WORKER_DIR"]) / "request-timeout-retry"
    evidence.mkdir(parents=True, exist_ok=False)
    with timeout_retry_target(first_delay=1.0) as (base, requests, delivered_attempts):
        async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(
                store=PostgresApiCredentialStore(settings), session_store=sessions,
            )
            await references.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(
                id=str(uuid4()), project_key="api-timeout-retry",
                name="API request timeout retry", created_at=now, updated_at=now,
            ))
            parent = SessionRecord(
                id=str(uuid4()), title="API request timeout retry", project_id=project.id,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
            )
            await sessions.save_session(parent)
            tool_runtime = ToolRuntimeService(
                tool_job_service=jobs, session_store=sessions, settings=settings,
                api_credential_reference_service=references,
            )
            launcher = ControlledWorkerLauncher(sessions, tool_runtime)
            runtime = ApiTestingModeRuntime(
                api_docs_service=None, settings=settings, coordinator_runtime_service=launcher,
                session_store=sessions, credential_reference_service=references, tool_job_service=jobs,
            )
            task = ApiTestTask(
                task_id="slow-read", method="GET", path="/slow", full_url=base + "/slow", status="ready",
            )
            state = ApiTestingState(
                phase=PHASE_CAMPAIGN_READY,
                campaign=ApiTestCampaign(
                    campaign_id=str(uuid4()), project_id=project.id, project_name=project.name,
                    objective="短超时只读请求失败后有限重试，报告最终真实结果",
                    tasks=[task],
                    execution_policy=ExecutionPolicy(max_retries=1, request_timeout_seconds=0.5),
                ),
            )
            context = SimpleNamespace(
                session_id=parent.id, turn_id=str(uuid4()), trace_id=str(uuid4()),
                selected_agent_key="api-testing-agent", selected_model_key="", context_bundle={},
            )

            await runtime._execute_campaign(state, context)
            output = runtime._build_output(state)
            final_task = state.campaign.tasks[0]
            first_result = launcher.runs[0][2].output["task_result"]
            second_result = launcher.runs[1][2].output["task_result"]
            assert requests == [{"attempt": 1, "path": "/slow"}, {"attempt": 2, "path": "/slow"}]
            assert delivered_attempts == [2]
            assert first_result["status"] == "failed"
            assert first_result["request_outcome"] == "known_failed"
            assert "timed out" in first_result["last_error"].lower()
            assert first_result["timeout_seconds"] == 0.5
            assert second_result["status"] == "completed" and second_result["response_status"] == 200
            assert second_result["timeout_seconds"] == 0.5
            first_result_ref = first_result["execution_checkpoint"].get("private_result_ref")
            second_result_ref = second_result["execution_checkpoint"].get("private_result_ref")
            assert first_result_ref and second_result_ref and first_result_ref != second_result_ref
            assert launcher.task_ids == ["slow-read", "slow-read:attempt:2"]
            assert final_task.status == "completed" and final_task.attempts == 2
            assert final_task.timeout_seconds == 0.5 and final_task.response_body == {"attempt": 2, "ok": True}
            assert final_task.execution_checkpoint["private_result_ref"] == second_result_ref
            assert state.phase == "report_ready"
            assert output["report"]["completed"] == 1 and output["report"]["failed"] == 0
            (evidence / "verification.json").write_text(json.dumps({
                "requests": requests,
                "response_delivered_attempts": delivered_attempts,
                "attempt_results": [
                    {"status": first_result["status"], "request_outcome": first_result["request_outcome"],
                     "error": first_result["last_error"], "timeout_seconds": first_result["timeout_seconds"]},
                    {"status": second_result["status"], "response_status": second_result["response_status"],
                     "timeout_seconds": second_result["timeout_seconds"]},
                ],
                "worker_dispatch_ids": launcher.task_ids,
                "final_task_status": final_task.status,
                "final_attempt_count": final_task.attempts,
                "report_counts": {key: output["report"][key] for key in ("completed", "failed", "skipped")},
                "external_ai_calls": 0,
                "full_goal_complete": False,
            }, ensure_ascii=False, indent=2), encoding="utf8")


async def process_api_401_campaign(parent_id: str, turn_id: str, pause_after: int) -> None:
    """Run one Worker campaign in a separate process and optionally park it."""
    from src.core.config import get_settings
    from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime

    settings = get_settings()
    sessions = PostgresSessionStore(settings)
    jobs = ToolJobService(PostgresToolJobStore(settings))
    references = ApiCredentialReferenceService(
        store=PostgresApiCredentialStore(settings), session_store=sessions,
    )
    await references.initialize()
    parent = await sessions.get_session(parent_id)
    assert parent is not None
    state = ApiTestingState.model_validate(parent.metadata[STATE_METADATA_KEY])
    manager = CredentialManager()
    runtime = ToolRuntimeService(
        tool_job_service=jobs, session_store=sessions, settings=settings,
        api_credential_reference_service=references,
    )
    launcher = ControlledWorkerLauncher(sessions, runtime)
    projection = ApiTestingModeRuntime(
        api_docs_service=None, settings=settings, session_store=sessions,
        credential_reference_service=references, tool_job_service=jobs,
    )
    projection._credential_manager = manager
    parked = False

    async def persist(tasks, marker: str, completed: bool = False):
        state.campaign.tasks = list(tasks)
        latest = manager.get_latest()
        if latest is not None:
            state.credential_session = latest.model_copy(deep=True)
        payload = projection._state_payload(state)
        probe = {
            "pid": os.getpid(), "marker": marker,
            "completed": completed,
            "task_statuses": {task.task_id: task.status for task in tasks},
            "request_count": sum(1 for task in tasks if task.attempts),
        }
        await sessions.mutate_metadata(
            parent_id,
            lambda metadata: metadata.update({STATE_METADATA_KEY: payload, "api401_probe": probe}),
        )

    class ParkedCoordinator(ApiSubagentCoordinator):
        async def _dispatch_batch(self, batch):
            nonlocal parked
            await super()._dispatch_batch(batch)
            if parked or not pause_after:
                return
            by_id = {task.task_id: task for task in self._pool.all_tasks}
            target = by_id["profile"]
            refresh = by_id["refresh"]
            pause = (
                pause_after == 1 and by_id["login"].status == "completed"
            ) or (
                pause_after == 2 and target.execution_checkpoint.get("auth_refresh", {}).get("state") == "pending"
            ) or (
                pause_after == 3 and refresh.status == "completed" and target.status == "pending"
            )
            if pause:
                parked = True
                await persist(self._pool.all_tasks, f"pause-{pause_after}")
                print(json.dumps({"pid": os.getpid(), "parked_after": pause_after}), flush=True)
                await asyncio.Event().wait()

    coordinator = ParkedCoordinator(
        pool=ApiTaskPool(state.campaign.tasks), policy=ExecutionPolicy(max_retries=0),
        coordinator_runtime_service=launcher, session_store=sessions,
        parent_context={"session_id": parent_id, "turn_id": turn_id},
        credential_manager=manager, credential_reference_service=references,
        tool_job_service=jobs,
    )
    results = await coordinator.run_all()
    await persist(results, "completed", completed=True)
    print(json.dumps({"pid": os.getpid(), "completed": len(results)}), flush=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_after", [1, 2, 3])
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_401_AUTO_PROCESS") != "1",
    reason="requires isolated PG/MySQL/Redis/RustFS and real process kill/restart")
async def test_real_process_kill_restores_automatic_401_without_duplicate_requests(tmp_path, pause_after):
    evidence = Path(os.environ["QA_API_401_AUTO_PROCESS_DIR"]) / f"pause-{pause_after}"
    evidence.mkdir(parents=True, exist_ok=False)
    with automatic_target() as (base, requests, secrets):
        async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(
                store=PostgresApiCredentialStore(settings), session_store=sessions,
            )
            await references.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(
                id=str(uuid4()), project_key=f"auto-401-process-{pause_after}",
                name="Automatic 401 process recovery", created_at=now, updated_at=now,
            ))
            state = ApiTestingState(
                phase=PHASE_TASK_RUNNING,
                campaign=ApiTestCampaign(
                    campaign_id=str(uuid4()), project_id=project.id,
                    tasks=campaign_tasks(base), execution_policy=ExecutionPolicy(max_retries=0),
                ),
            )
            parent = SessionRecord(
                id=str(uuid4()), title="Automatic 401 process parent", project_id=project.id,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing",
                created_at=now, updated_at=now,
                metadata={STATE_METADATA_KEY: state.model_dump(mode="json")},
            )
            await sessions.save_session(parent)
            turn_id = str(uuid4())
            args = (str(Path(__file__).resolve()), "--resume-api-401", parent.id, turn_id)
            first = processes.start(f"api-401-before-{pause_after}", *args, str(pause_after))
            deadline = asyncio.get_running_loop().time() + 60
            while True:
                saved = await sessions.get_session(parent.id)
                probe = saved.metadata.get("api401_probe", {}) if saved else {}
                if probe.get("marker") == f"pause-{pause_after}":
                    break
                assert first.poll() is None, (
                    tmp_path / f"api-401-before-{pause_after}.stderr"
                ).read_text(encoding="utf8")
                assert asyncio.get_running_loop().time() < deadline, "401 recovery checkpoint was not observed"
                await asyncio.sleep(.1)
            expected_before = {
                1: ["/login"],
                2: ["/login", "/profile"],
                3: ["/login", "/profile", "/auth/refresh"],
            }[pause_after]
            assert first.poll() is None
            assert [event["path"] for event in requests] == expected_before
            assert all(secret not in json.dumps(saved.metadata) for secret in secrets)

            dispatches_before = list(saved.metadata.get("worker_dispatches", []))
            old_jobs = []
            for dispatch in dispatches_before:
                old_jobs.extend(
                    (await jobs.list_jobs(session_id=dispatch["child_session_id"]))
                )
            old_job_snapshots = [job.model_dump(mode="json") for job in old_jobs]
            first.kill()
            first_exit = await processes.wait(first, timeout=15)
            assert first_exit != 0

            second = processes.start(f"api-401-after-{pause_after}", *args, "0")
            assert await processes.wait(second, timeout=60) == 0, (
                tmp_path / f"api-401-after-{pause_after}.stderr"
            ).read_text(encoding="utf8")
            final = await sessions.get_session(parent.id)
            assert final is not None
            assert final.metadata["api401_probe"]["completed"] is True
            assert [event["path"] for event in requests] == [
                "/login", "/profile", "/auth/refresh", "/profile",
            ]
            assert all(secret not in json.dumps(final.metadata) for secret in secrets)
            assert [
                (await jobs.get_job(job.id)).model_dump(mode="json") for job in old_jobs
            ] == old_job_snapshots

            replay = processes.start(f"api-401-replay-{pause_after}", *args, "0")
            assert await processes.wait(replay, timeout=60) == 0
            assert [event["path"] for event in requests] == [
                "/login", "/profile", "/auth/refresh", "/profile",
            ]
            (evidence / "facts.json").write_text(json.dumps({
                "events": redacted_events(requests, secrets), "pause_after": pause_after,
                "forced_exit_code": first_exit, "terminal_replay_extra_http": 0,
                "original_jobs_unchanged": True, "public_credentials_absent": True,
                "external_ai_calls": 0, "full_goal_complete": False,
            }, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    if sys.argv[1] == "--resume-api-401":
        asyncio.run(process_api_401_campaign(sys.argv[2], sys.argv[3], int(sys.argv[4])))

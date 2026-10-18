"""Real HTTP must never promote a refresh/identity token to a bearer token."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from src.modes.api_testing_mode.campaign_state import ApiTestTask, AssertionSpec, ExecutionPolicy
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.doc_parser import ApiDocParser
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore


@contextmanager
def role_target(variant):
    values = {role: uuid4().hex for role in ("access", "refresh", "identity", "decoy")}
    bodies = {
        "refresh-only": {"refresh_token": values["refresh"]},
        "id-only": {"id_token": values["identity"]},
        "both": {"refresh_token": values["refresh"], "id_token": values["identity"],
                 "access_token": values["access"]},
        "nested": {"data": {"sessions": [{"credential": values["access"]}]}, "token": values["decoy"]},
        "literal-path": {"data.credential": values["access"], "data": {"credential": values["decoy"]}},
        "custom-missing": {"token": values["decoy"]},
        "camel": {"accessToken": values["access"]},
        "legacy-token": {"token": values["access"]},
    }
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body):
            auth = self.headers.get("Authorization", "")
            role = next((key for key, value in values.items() if auth == "Bearer " + value), "absent" if not auth else "other")
            events.append({"method": self.command, "path": self.path, "status": status, "credential_role": role})
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            self.reply(200, bodies[variant])

        def do_GET(self):
            accepted = self.headers.get("Authorization") == "Bearer " + values["access"]
            self.reply(200 if accepted else 401, {"accepted": accepted})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events, values
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("variant", ["refresh-only", "id-only", "both", "nested", "literal-path", "custom-missing", "camel", "legacy-token"])
async def test_actual_http_token_role_and_explicit_path_are_preserved(tmp_path, worker, variant, caplog):
    field = {"nested": "data.sessions[0].credential", "literal-path": "data.credential",
             "custom-missing": "data.credential"}.get(variant, "access_token")
    available = variant not in {"refresh-only", "id-only", "custom-missing"}
    with role_target(variant) as (base, events, values):
        tasks = [ApiTestTask(task_id="login", method="POST", path="/login", full_url=base+"/login",
                             execution_mode="auth", status="ready"),
                 ApiTestTask(task_id="profile", method="GET", path="/profile", full_url=base+"/profile",
                             status="blocked", depends_on=["login"],
                             assertions=[AssertionSpec(kind="status_code", expected=200 if available else 401)])]
        manager, pool, policy = CredentialManager(), ApiTaskPool(tasks), ExecutionPolicy(max_retries=2)
        if worker:
            store = InMemorySessionStore()
            launcher = ControlledWorkerLauncher(store)
            coordinator = ApiSubagentCoordinator(pool=pool, policy=policy, credential_manager=manager,
                coordinator_runtime_service=launcher, session_store=store,
                parent_context={"session_id": "parent", "turn_id": "turn"}, auth_token_field=field)
        else:
            coordinator = ApiTestCoordinator(pool=pool, policy=policy,
                executor_fn=ApiTaskExecutor(credential_manager=manager, auth_token_field=field).execute)
        results = await coordinator.run_all()
        before = [task.model_dump(mode="json") for task in results]
        count = len(events)
        await coordinator.run_all()
        facts = {"variant": variant, "worker": worker, "token_field": field, "events": events,
                 "valid_session": manager.has_valid_session(), "terminal_extra_http": len(events)-count,
                 "statuses": [t.status for t in results], "external_ai_calls": 0}
        output = Path(os.getenv("QA_API_TOKEN_ROLE_DIR", str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(facts, ensure_ascii=False, indent=2)
        assert all(value not in raw and value not in caplog.text for value in values.values())
        (output/f"{variant}-{'worker' if worker else 'inline'}.json").write_text(raw, encoding="utf8")
        assert len(events) == 2 and facts["terminal_extra_http"] == 0, facts
        assert before == [task.model_dump(mode="json") for task in results]
        assert events[-1]["credential_role"] == ("access" if available else "absent"), facts
        assert events[-1]["status"] == (200 if available else 401), facts
        assert manager.has_valid_session() is available, facts
        assert all(t.status == "completed" for t in results), facts


@pytest.mark.parametrize("bad", [None, False, 12345, {}, ["value"], "", "line\r\nbreak"])
def test_invalid_explicit_token_does_not_fall_back_to_unrelated_token(bad, caplog):
    decoy = uuid4().hex
    manager = CredentialManager()
    session = manager.create_from_login_response(response_body={"data": {"credential": bad}, "token": decoy},
                                                 token_field="data.credential")
    token_absent = not bool(session.token)
    assert token_absent and not manager.has_valid_session()
    assert manager.build_request_headers(session.credential_session_id) == {}
    assert "api_login_token_unavailable" in caplog.text and decoy not in caplog.text


@pytest.mark.parametrize("content,expected", [
    ('Bearer authentication: {"refresh_token":"sample","access_token":"sample"}', "access_token"),
    ('Bearer authentication: {"id_token":"sample","accessToken":"sample"}', "accessToken"),
    ('Bearer token header. Response: {"access_token":"sample"}', "access_token"),
    ('Bearer authentication: {"refresh_token":"sample"}', None),
    ('Bearer authentication: {"id_token":"sample"}', None),
    ('Bearer authentication: {"my_token_count":20}', None),
    ('Bearer authentication: {"token":"sample"}', "token"),
])
def test_document_token_hint_respects_field_boundaries_and_access_role(content, expected):
    hint = ApiDocParser(api_docs_service=None)._detect_auth_hint(content, [])
    assert hint.get("token_field") == expected, hint


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_TOKEN_ROLE_STORAGE") != "1",
                    reason="isolated real PG credential references and ToolJobs")
@pytest.mark.parametrize("variant", ["nested", "refresh-only"])
async def test_real_private_dispatch_preserves_token_mapping_and_terminal_job(tmp_path, variant):
    from dataclasses import replace
    from datetime import datetime, timezone
    from test_execution_worker_cli import isolated_runtime
    from src.application.projects.project_store import PostgresProjectStore
    from src.application.runtime.tool_job_service import ToolJobService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService
    from src.domain.models import SessionRecord
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
    from src.registry.tools import ToolRegistry
    from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.schemas.project import ProjectRecord
    from src.schemas.session import RuntimeMode, SessionMode, SessionStatus

    available = variant == "nested"
    field = "data.sessions[0].credential" if available else "access_token"
    with role_target(variant) as (base, events, values):
        async with isolated_runtime(tmp_path) as (settings, _, _, objects, _):
            projects, sessions = PostgresProjectStore(settings), PostgresSessionStore(settings)
            await projects.initialize()
            await sessions.initialize()
            now = datetime.now(timezone.utc)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="token-role", name="Token role fixture",
                created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Token role parent", project_id=project.id,
                created_at=now, updated_at=now, status=SessionStatus.running,
                session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background, mode_key="api_testing")
            await sessions.save_session(parent)
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            private = PostgresApiCredentialStore(settings)
            references = ApiCredentialReferenceService(store=private, session_store=sessions)
            await references.initialize()
            runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=objects, settings=settings,
                session_store=sessions, api_credential_reference_service=references)
            launcher, manager = ControlledWorkerLauncher(sessions, runtime), CredentialManager()
            tasks = [ApiTestTask(task_id="login", method="POST", path="/login", full_url=base+"/login",
                                 execution_mode="auth", status="ready"),
                     ApiTestTask(task_id="profile", method="GET", path="/profile", full_url=base+"/profile",
                                 status="blocked", depends_on=["login"],
                                 assertions=[AssertionSpec(kind="status_code", expected=200 if available else 401)])]
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(tasks), policy=ExecutionPolicy(),
                coordinator_runtime_service=launcher, session_store=sessions,
                parent_context={"session_id": parent.id, "turn_id": str(uuid4())}, credential_manager=manager,
                credential_reference_service=references, auth_token_field=field)
            results = await coordinator.run_all()
            assert len(events) == 2 and all(t.status == "completed" for t in results)
            assert events[-1]["credential_role"] == ("access" if available else "absent")
            assert manager.has_valid_session() is available
            reader = PostgresSessionStore(settings)
            restored_references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=reader)
            recovered_runtime = ToolRuntimeService(tool_job_service=ToolJobService(PostgresToolJobStore(settings)),
                artifact_storage_service=objects, settings=settings, session_store=reader,
                api_credential_reference_service=restored_references)
            saved_jobs = []
            for call, context, result in launcher.runs:
                assert set(call.arguments) == {"worker_action", "dispatch_ref"}
                frozen, restored_credential, owner = await restored_references.resolve_dispatch(call.arguments["dispatch_ref"], context)
                assert frozen["auth_token_field"] == field and owner.id == parent.id
                if frozen["task"]["task_id"] == "profile":
                    selected_access = restored_credential.token == values["access"]
                    assert selected_access is available
                job = await jobs.get_job(result.job_id)
                saved = await reader.get_session(context.session_id)
                public = json.dumps(job.output_payload) + json.dumps(job.input_payload) + json.dumps(
                    [m.model_dump(mode="json") for m in saved.messages])
                assert values["access"] not in public and values["refresh"] not in public
                replayed = await recovered_runtime.execute(ToolRegistry().get(call.name), call,
                                                           replace(context, tool_job_id=job.id))
                assert replayed.job_id == job.id and replayed.output["task_result"] == job.output_payload["task_result"]
                saved_jobs.append({"job_id": job.id, "response_status": job.output_payload["task_result"]["response_status"],
                                   "frozen_field": frozen["auth_token_field"], "replay_same_job": True})
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                counts = {}
                for table in private.tables:
                    cur.execute(f"SELECT ciphertext FROM {table}")
                    rows = cur.fetchall()
                    counts[table] = len(rows)
                    assert all(value not in row["ciphertext"] for row in rows for value in values.values())
            assert len(events) == 2
            facts = {"variant": variant, "events": events, "jobs": saved_jobs, "private_row_counts": counts,
                     "terminal_extra_http": len(events)-2, "public_selected_credentials_redacted": True,
                     "encrypted_private_storage": True, "external_ai_calls": 0,
                     "scope": "isolated real stores, production ToolRuntime/Worker runner, service recreation; no process kill"}
            output = Path(os.getenv("QA_API_TOKEN_ROLE_DIR", str(tmp_path)))
            output.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(facts, ensure_ascii=False, indent=2)
            assert all(value not in raw for value in values.values())
            (output/f"{variant}-storage.json").write_text(raw, encoding="utf8")

"""Real Set-Cookie login/rotation, scopes and reusable private snapshots."""
import asyncio
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from datetime import UTC, datetime
from threading import Thread
from uuid import uuid4

import pytest

from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, AssertionSpec, CredentialSession, ExecutionPolicy
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore


@contextmanager
def cookie_target(redirect=False):
    values = {name: uuid4().hex for name in ("login", "refresh", "admin", "secure", "obsolete")}
    events = []

    class Handler(BaseHTTPRequestHandler):
        def observe(self):
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            sid = cookies["sid"].value if "sid" in cookies else ""
            source = next((name for name, value in values.items() if sid == value), "absent" if not sid else "unknown")
            events.append({"method": self.command, "path": self.path, "cookie_names": sorted(cookies), "sid_source": source})
            return cookies, source

        def reply(self, status=204, cookies=(), location=None, body=None):
            raw = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            cookies, source = self.observe()
            if self.path == "/api/login":
                self.reply(303 if redirect else 204, cookies=[
                    f"sid={values['login']}; Path=/api; Max-Age=3600; HttpOnly; SameSite=Lax",
                    f"sid={values['admin']}; Path=/admin; Max-Age=3600",
                    f"secure_only={values['secure']}; Path=/api; Secure; Max-Age=3600",
                    f"obsolete={values['obsolete']}; Path=/api; Max-Age=3600",
                    "foreign=discard; Domain=unrelated.example; Path=/",
                ], location="/api/finish" if redirect else None)
            elif self.path == "/api/refresh" and source == "login":
                self.reply(cookies=[f"sid={values['refresh']}; Path=/api; Max-Age=3600; HttpOnly; SameSite=Lax",
                                    "obsolete=; Path=/api; Max-Age=0"])
            else:
                self.reply(401, body={"ok": False})

        def do_GET(self):
            cookies, source = self.observe()
            if self.path == "/api/finish":
                self.reply(204 if source == "login" else 401)
            elif self.path == "/api/profile":
                self.reply(200 if source == "refresh" else 401,
                    body={"ok": source == "refresh", "opaque_echo": values["refresh"]})
            else:
                # Scope probes are public endpoints: all return 200, even when
                # a credential leaked. The test checks what actually arrived.
                self.reply(200, body={"ok": True})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events, values
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def campaign_tasks(url):
    return [ApiTestTask(task_id=identity, method=method, path=path, full_url=address,
            execution_mode=mode, capability=capability, depends_on=deps, status="blocked" if deps else "ready")
        for identity, method, path, address, mode, capability, deps in [
            ("login", "POST", "/api/login", url+"/api/login", "auth", "login", []),
            ("refresh", "POST", "/api/refresh", url+"/api/refresh", "auth", "refresh_token", ["login"]),
            ("profile", "GET", "/api/profile", url+"/api/profile", "read", "detail", ["login", "refresh"]),
            ("outside", "GET", "/outside", url+"/outside", "read", "", ["refresh"]),
            ("other-host", "GET", "/api/check", url.replace("127.0.0.1", "localhost")+"/api/check", "read", "", ["refresh"]),
            ("secure", "GET", "/api/secure", url+"/api/secure", "read", "", ["refresh"]),
        ]]


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("redirect", [False, True])
def test_cookie_only_login_rotation_scope_and_terminal_replay(tmp_path, worker, redirect):
    with cookie_target(redirect) as (url, events, values):
        credentials, tasks, policy = CredentialManager(), campaign_tasks(url), ExecutionPolicy(max_workers=3, max_retries=0)
        if worker:
            store = InMemorySessionStore()
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(tasks), policy=policy,
                coordinator_runtime_service=ControlledWorkerLauncher(store), session_store=store,
                parent_context={"session_id": "parent", "turn_id": "turn"}, credential_manager=credentials)
        else:
            coordinator = ApiTestCoordinator(pool=ApiTaskPool(tasks), policy=policy,
                executor_fn=ApiTaskExecutor(credential_manager=credentials).execute)
        result = asyncio.run(coordinator.run_all())
        before = [task.model_dump(mode="json") for task in result]
        count = len(events)
        asyncio.run(coordinator.run_all())
        assert before == [task.model_dump(mode="json") for task in result] and len(events) == count
        report = ReportBuilder(credential_manager=credentials).build(campaign=ApiTestCampaign(campaign_id="cookie", tasks=result))
        facts = {"events": events, "statuses": [t.status for t in result], "report": report.model_dump(mode="json"),
                 "external_ai_calls": 0, "terminal_extra_http": len(events)-count}
        directory = Path(os.getenv("QA_API_COOKIE_DIR", str(tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(facts, ensure_ascii=False, indent=2)
        assert all(value not in raw for value in values.values())
        (directory/f"{'worker' if worker else 'inline'}-{'redirect' if redirect else 'direct'}.json").write_text(raw, encoding="utf8")
        assert all(t.status == "completed" for t in result), facts
        assert len(events) == 6+int(redirect), facts
        assert next(e for e in events if e["path"] == "/api/refresh")["sid_source"] == "login"
        assert next(e for e in events if e["path"] == "/api/profile")["sid_source"] == "refresh"
        assert next(e for e in events if e["path"] == "/outside")["cookie_names"] == []
        assert next(e for e in events if e["path"] == "/api/check")["cookie_names"] == []
        assert next(e for e in events if e["path"] == "/api/secure")["cookie_names"] == ["sid"]
        session = credentials.get(result[1].auth_ref)
        assert session.auth_type == "cookie"
        records = session.cookie_records
        assert sum(record.name == "sid" for record in records) == 2
        assert not any(record.name in {"obsolete", "foreign"} for record in records)
        assert any(record.secure for record in records)
        api_sid = next(record for record in records if record.name == "sid" and record.path == "/api")
        assert api_sid.domain == "127.0.0.1" and not api_sid.domain_specified
        assert api_sid.expires is not None and "HttpOnly" in api_sid.rest and api_sid.rest["SameSite"] == "Lax"
        private = session.model_dump(mode="json")
        restored = CredentialManager()
        restored.restore_session(CredentialSession.model_validate(private))
        assert restored.get(session.credential_session_id).model_dump(mode="json") == private
        public, _, _ = OutputSafetyPolicy().sanitize_tool_output({"credential_session": private, "cookie_records": private["cookie_records"]})
        assert all(value not in json.dumps(public) for value in values.values())


def test_legacy_cookie_serialization_stays_byte_equivalent():
    session = CredentialManager().create_from_user_input(extracted={"auth_type": "cookie", "cookie": "sid=fixture"})
    original = session.model_dump(mode="json")
    assert "cookie_records" not in original
    restored = CredentialSession.model_validate(original)
    assert restored.model_dump(mode="json") == original


def test_expired_restored_cookies_are_not_sent_or_considered_available():
    with cookie_target() as (url, events, values):
        credentials = CredentialManager()
        login = campaign_tasks(url)[0]
        asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(login))
        session = credentials.get(login.auth_ref)
        for record in session.cookie_records:
            record.expires = 1
        restored = CredentialManager()
        restored.restore_session(CredentialSession.model_validate(session.model_dump(mode="json")))
        assert not restored.has_valid_session()
        task = ApiTestTask(task_id="expired", method="GET", path=url+"/api/check", auth_ref=session.credential_session_id)
        result = asyncio.run(ApiTaskExecutor(credential_manager=restored).execute(task))
        assert result.status == "completed" and events[-1]["cookie_names"] == []


def test_empty_scoped_snapshot_is_authoritative_over_legacy_cookie_dictionary():
    with cookie_target() as (url, events, values):
        credentials = CredentialManager()
        session = CredentialSession(credential_session_id=str(uuid4()), auth_type="cookie", cookie_jar={"sid": values["login"]},
                                    cookie_records=[])
        credentials.restore_session(session)
        assert not credentials.has_valid_session()
        assert session.model_dump(mode="json")["cookie_records"] == []
        result = asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(
            ApiTestTask(task_id="revoked", method="GET", path=url+"/api/check", auth_ref=session.credential_session_id)))
        assert result.status == "completed" and events[-1]["cookie_names"] == []


def test_expected_auth_401_does_not_mint_a_login_session():
    with cookie_target() as (url, events, values):
        credentials = CredentialManager()
        task = ApiTestTask(task_id="negative-auth", method="POST", path=url+"/api/refresh", execution_mode="auth",
                           assertions=[AssertionSpec(kind="status_code", expected=401)])
        result = asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(task))
        assert result.status == "completed" and result.response_status == 401 and result.auth_ref == ""
        assert credentials.get_latest() is None and len(events) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_COOKIE_STORAGE") != "1", reason="isolated real PG/ToolJob/RustFS credential flow")
async def test_cookie_attributes_private_storage_worker_read_and_replay(tmp_path):
    from test_execution_worker_cli import isolated_runtime
    from src.application.projects.project_store import PostgresProjectStore
    from src.application.runtime.tool_job_service import ToolJobService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService
    from src.domain.models import SessionRecord
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
    from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.schemas.project import ProjectRecord
    from src.schemas.session import SessionStatus, SessionMode, RuntimeMode
    from src.registry.tools import ToolRegistry
    from dataclasses import replace

    with cookie_target(True) as (url, events, values):
        async with isolated_runtime(tmp_path) as (settings, _, _, objects, owned):
            projects, sessions = PostgresProjectStore(settings), PostgresSessionStore(settings)
            await projects.initialize()
            await sessions.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="cookie-private", name="Cookie fixture",
                created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Cookie parent", project_id=project.id, created_at=now, updated_at=now,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing")
            await sessions.save_session(parent)
            context = {"session_id": parent.id, "turn_id": str(uuid4())}
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            private = PostgresApiCredentialStore(settings)
            references = ApiCredentialReferenceService(store=private, session_store=sessions)
            await references.initialize()
            runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=objects, settings=settings,
                session_store=sessions, api_credential_reference_service=references)
            launcher, credentials = ControlledWorkerLauncher(sessions, runtime), CredentialManager()
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(campaign_tasks(url)[:3]), policy=ExecutionPolicy(max_retries=0),
                coordinator_runtime_service=launcher, session_store=sessions, parent_context=context,
                credential_manager=credentials, credential_reference_service=references)
            result = await coordinator.run_all()
            assert all(t.status == "completed" for t in result)
            assert [t.response_status for t in result] == [204, 204, 200]
            reader = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=PostgresSessionStore(settings))
            restored = await reader.load_parent_credential(result[-1].auth_ref, context)
            assert restored.model_dump(mode="json") == credentials.get(result[-1].auth_ref).model_dump(mode="json")
            fresh = CredentialManager()
            fresh.restore_session(restored)
            read = await ApiTaskExecutor(credential_manager=fresh).execute(
                ApiTestTask(task_id="fresh-read", method="GET", path=url+"/api/profile", auth_ref=restored.credential_session_id))
            assert read.response_status == 200 and read.status == "completed"
            public_jobs = []
            for call, worker_context, outcome in launcher.runs:
                job = await jobs.get_job(outcome.job_id)
                public_jobs.append(job.output_payload)
                assert all(value not in json.dumps(job.output_payload) for value in values.values())
                if job.output_payload["task_result"]["task_id"] == "profile":
                    assert job.output_payload["task_result"]["response_body"]["opaque_echo"] == "[REDACTED]"
            assert all(value not in prompt for value in values.values() for prompt in launcher.prompts)
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                ciphertexts = []
                for table in private.tables:
                    cur.execute(f"SELECT ciphertext FROM {table}")
                    ciphertexts.extend(row["ciphertext"] for row in cur.fetchall())
                assert all(value not in text for value in values.values() for text in ciphertexts)
            # Existing immutable rows without the new field keep the same digest.
            old = CredentialManager().create_from_user_input(extracted={"auth_type": "cookie", "cookie": "sid=fixture"})
            original = old.model_dump(mode="json")
            await private.save_credential(original, owner_session_id=parent.id, project_id=parent.project_id)
            await private.save_credential(CredentialSession.model_validate(original).model_dump(mode="json"),
                owner_session_id=parent.id, project_id=parent.project_id)
            before = len(events)
            replay_runtime = ToolRuntimeService(tool_job_service=ToolJobService(PostgresToolJobStore(settings)),
                artifact_storage_service=objects, settings=settings, session_store=PostgresSessionStore(settings),
                api_credential_reference_service=reader)
            for call, worker_context, outcome in launcher.runs:
                replay = await replay_runtime.execute(ToolRegistry().get(call.name), call,
                    replace(worker_context, tool_job_id=outcome.job_id))
                assert replay.job_id == outcome.job_id
            assert len(events) == before
            facts = {"events": events, "statuses": [t.status for t in result], "fresh_service_profile_status": read.response_status,
                "cookie_attribute_roundtrip_equal": True, "opaque_worker_prompts": True, "encrypted_rows_without_plain_values": True,
                "public_output_no_plain_cookie_values": True, "terminal_extra_http": len(events)-before,
                "old_payload_idempotent_with_unchanged_hash": True, "external_ai_calls": 0,
                "scope": "real ToolRuntime/PG sessions/encrypted credentials, controlled Worker launch; no independent API or external AI"}
            directory = Path(os.environ["QA_API_COOKIE_STORAGE_DIR"])
            directory.mkdir(parents=True, exist_ok=False)
            (directory/"verification.json").write_text(json.dumps(facts, indent=2), encoding="utf8")

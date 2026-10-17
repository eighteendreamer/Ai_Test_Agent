"""Real HTTP evidence for explicit token expiry without unsafe 401 replay."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

from src.modes.api_testing_mode.campaign_state import ApiTestTask, AssertionSpec
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor


def save_facts(name, facts):
    if output := os.getenv("QA_API_TOKEN_EXPIRY_DIR"):
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{name}.json"
        assert not target.exists(), "do not overwrite previous evidence"
        target.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")


def public_requests(requests):
    return [{"path": r["path"], "authorization_present": bool(r["authorization"]),
             "issued_token_used": r["authorization"] == "Bearer issued-token"} for r in requests]


@contextmanager
def token_target(expired=True):
    requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            requests.append({"path": path, "authorization": self.headers.get("Authorization", "")})
            if path == "/login":
                self._reply(200, {"access_token": "issued-token", "expires_in": 0 if expired else 3600})
            else:
                self._reply(401, {"ok": False})

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            authorization = self.headers.get("Authorization", "")
            requests.append({"path": path, "authorization": authorization})
            accepted = authorization == "Bearer issued-token" and not expired
            self._reply(200 if accepted else 401, {"ok": accepted})

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_login_expiry_is_saved_and_negative_test_sends_expired_token_once():
    with token_target() as (base_url, requests):
        credentials = CredentialManager()
        executor = ApiTaskExecutor(credential_manager=credentials)
        login = ApiTestTask(task_id="login", method="POST", path="/login", full_url=base_url + "/login",
                            execution_mode="auth", request_body={"user": "fixture"},
                            assertions=[AssertionSpec(kind="status_code", expected=200)])
        login_result = asyncio.run(executor.execute(login))
        assert login_result.status == "completed"
        session = credentials.get(login_result.auth_ref)
        assert session is not None and session.expires_at and not credentials.has_valid_session()
        assert credentials.build_request_headers(session.credential_session_id) == {"Authorization": "Bearer issued-token"}

        protected = ApiTestTask(task_id="protected", method="GET", path="/protected",
                                full_url=base_url + "/protected", auth_ref=session.credential_session_id,
                                assertions=[AssertionSpec(kind="status_code", expected=401)])
        result = asyncio.run(executor.execute(protected))
        assert result.status == "completed" and result.response_status == 401 and result.attempts == 1
        assert requests == [
            {"path": "/login", "authorization": ""},
            {"path": "/protected", "authorization": "Bearer issued-token"},
        ]
        assert credentials.get_latest().credential_session_id == session.credential_session_id
        save_facts("negative", {"requests": public_requests(requests), "expiry_recorded": bool(session.expires_at),
            "available": credentials.has_valid_session(), "statuses": [login_result.status, result.status],
            "response_status": result.response_status, "attempts": result.attempts, "external_ai_calls": 0})


def test_explicit_future_expiry_remains_usable_and_past_user_input_is_unavailable():
    with token_target(expired=False) as (base_url, requests):
        credentials = CredentialManager()
        session = credentials.create_from_login_response(
            response_body={"access_token": "issued-token", "expires_in": 3600},
            login_endpoint="POST /login",
        )
        assert credentials.has_valid_session()
        result = asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(
            ApiTestTask(task_id="protected", method="GET", path="/protected", full_url=base_url + "/protected",
                        auth_ref=session.credential_session_id)))
        assert result.status == "completed" and requests[-1]["authorization"] == "Bearer issued-token"

        past = CredentialManager().create_from_user_input(
            extracted={"auth_type": "bearer", "token": "old-token", "expires_at": "1970-01-01T00:00:00Z"}
        )
        expired_manager = CredentialManager()
        expired_manager.restore_session(past)
        assert not expired_manager.has_valid_session()
        assert expired_manager.build_request_headers(past.credential_session_id) == {"Authorization": "Bearer old-token"}
        save_facts("live", {"requests": public_requests(requests), "expiry_recorded": bool(session.expires_at),
            "available": credentials.has_valid_session(), "status": result.status,
            "past_session_available": expired_manager.has_valid_session(), "external_ai_calls": 0})


@pytest.mark.parametrize("ttl", [True, -1, 0.5, "bad", float("inf"), float("nan"), 10**100, {}])
def test_invalid_ttl_is_unknown_and_log_has_no_credential_value(ttl, caplog):
    from uuid import uuid4
    value = uuid4().hex
    credentials = CredentialManager()
    session = credentials.create_from_login_response(response_body={"access_token": value, "expires_in": ttl})
    assert session.expires_at == "" and credentials.has_valid_session()
    assert "api_credential_expiry_invalid" in caplog.text and value not in caplog.text


def test_cookie_only_rotation_keeps_old_token_expiry_and_new_token_lifetime_is_independent():
    from src.modes.api_testing_mode.campaign_state import ApiCookieRecord
    credentials = CredentialManager()
    old = credentials.create_from_login_response(response_body={"access_token": "fixture-old", "expires_in": 0})
    original = old.model_dump(mode="json")
    assert old.expires_at and not credentials.has_valid_session()
    cookie_rotation = credentials.create_from_login_response(response_body={}, prior_credential_session_id=old.credential_session_id,
        cookie_records=[ApiCookieRecord(name="sid", value="fixture-cookie", domain="127.0.0.1")])
    assert cookie_rotation.token == old.token and cookie_rotation.expires_at == old.expires_at
    assert not credentials.has_valid_session()
    new = credentials.create_from_login_response(response_body={"access_token": "fixture-new"},
        prior_credential_session_id=cookie_rotation.credential_session_id)
    assert new.expires_at == "" and credentials.has_valid_session()
    assert old.model_dump(mode="json") == original


@pytest.mark.parametrize("expiry", ["bad", "2026-10-01T08:00:00", "1735689600000"])
def test_ambiguous_absolute_expiry_is_not_guessed_or_rewritten(expiry, caplog):
    credentials = CredentialManager()
    session = credentials.create_from_user_input(extracted={"token": "fixture-value", "expires_at": expiry})
    assert credentials.has_valid_session() and session.expires_at == expiry
    assert "api_credential_expiry_invalid" in caplog.text and session.token not in caplog.text


def test_known_expired_session_reaches_existing_auth_input_state_without_http():
    from src.modes.api_testing_mode.campaign_state import ApiTestingState, CredentialSession, EndpointCandidate
    from src.modes.api_testing_mode.contracts import PHASE_AWAITING_AUTH_INPUT, PRECOND_AUTH_TOKEN
    from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
    runtime = ApiTestingModeRuntime(api_docs_service=object())
    old = CredentialSession(credential_session_id="expired", token="fixture-value",
        headers={"Authorization": "Bearer fixture-value"}, expires_at="1970-01-01T08:00:00+08:00")
    original = old.model_dump(mode="json")
    runtime._credential_manager.restore_session(old)
    state = ApiTestingState(credential_session=old, selected_endpoints=[EndpointCandidate(
        endpoint_id="profile", method="GET", path="/profile", preconditions=[PRECOND_AUTH_TOKEN])])
    result = runtime._check_preconditions(state)
    assert result.phase == PHASE_AWAITING_AUTH_INPUT and result.campaign is None
    assert result.pending_selection.kind == "credential"
    assert old.model_dump(mode="json") == original


@pytest.mark.parametrize("worker", [False, True])
def test_both_coordinators_preserve_expected_401_and_terminal_replay(worker):
    from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
    from src.modes.api_testing_mode.campaign_state import CredentialSession, ExecutionPolicy
    from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
    from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
    from src.modes.api_testing_mode.task_pool import ApiTaskPool
    from src.runtime.store import InMemorySessionStore
    with token_target() as (url, requests):
        credentials, store = CredentialManager(), InMemorySessionStore()
        tasks = [ApiTestTask(task_id="login", method="POST", path=url+"/login", execution_mode="auth", status="ready"),
            ApiTestTask(task_id="profile", method="GET", path=url+"/protected", depends_on=["login"], status="blocked",
                        assertions=[AssertionSpec(kind="status_code", expected=401)])]
        pool, policy = ApiTaskPool(tasks), ExecutionPolicy(max_retries=2)
        if worker:
            coordinator = ApiSubagentCoordinator(pool=pool, policy=policy, credential_manager=credentials,
                coordinator_runtime_service=ControlledWorkerLauncher(store), session_store=store,
                parent_context={"session_id": "owned-parent", "turn_id": "owned-turn"})
        else:
            coordinator = ApiTestCoordinator(pool=pool, policy=policy,
                executor_fn=ApiTaskExecutor(credential_manager=credentials).execute)
        result = asyncio.run(coordinator.run_all())
        session = credentials.get(result[0].auth_ref)
        original = [task.model_dump(mode="json") for task in result]
        count = len(requests)
        asyncio.run(coordinator.run_all())
        assert original == [task.model_dump(mode="json") for task in result]
        assert [task.status for task in result] == ["completed", "completed"]
        assert session.expires_at and not credentials.has_valid_session()
        assert result[1].response_status == 401 and len(requests) == count == 2
        restored = CredentialManager()
        restored.restore_session(CredentialSession.model_validate(session.model_dump(mode="json")))
        assert not restored.has_valid_session()
        assert restored.get(session.credential_session_id).expires_at == session.expires_at
        save_facts("worker" if worker else "inline", {"requests": public_requests(requests),
            "statuses": [task.status for task in result], "expiry_roundtrip_equal": True,
            "available": restored.has_valid_session(), "response_status": result[1].response_status,
            "terminal_extra_http": len(requests)-count, "external_ai_calls": 0})


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_TOKEN_EXPIRY_STORAGE") != "1", reason="isolated real PG encrypted credential storage")
async def test_real_private_storage_preserves_absolute_expiry_and_old_hash(tmp_path):
    from datetime import datetime, timezone
    from uuid import uuid4
    from test_execution_worker_cli import isolated_runtime
    from src.application.projects.project_store import PostgresProjectStore
    from src.domain.models import SessionRecord
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.modes.api_testing_mode.campaign_state import CredentialSession
    from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
    from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.schemas.project import ProjectRecord
    from src.schemas.session import RuntimeMode, SessionMode, SessionStatus

    with token_target() as (url, requests):
        async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
            projects, sessions = PostgresProjectStore(settings), PostgresSessionStore(settings)
            await projects.initialize()
            await sessions.initialize()
            now = datetime.now(timezone.utc)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="token-expiry", name="Expiry fixture",
                created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Expiry parent", project_id=project.id, created_at=now, updated_at=now,
                status=SessionStatus.running, session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background, mode_key="api_testing")
            await sessions.save_session(parent)
            store = PostgresApiCredentialStore(settings)
            references = ApiCredentialReferenceService(store=store, session_store=sessions)
            await references.initialize()
            credentials = CredentialManager()
            login = await ApiTaskExecutor(credential_manager=credentials).execute(ApiTestTask(
                task_id="login", method="POST", path=url+"/login", execution_mode="auth"))
            session = credentials.get(login.auth_ref)
            raw = session.model_dump(mode="json")
            assert session.expires_at and login.status == "completed"
            await references.save_parent_credential(session, {"session_id": parent.id})
            reader = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=PostgresSessionStore(settings))
            restored = await reader.load_parent_credential(session.credential_session_id, {"session_id": parent.id})
            assert restored.model_dump(mode="json") == raw and len(requests) == 1
            fresh = CredentialManager()
            fresh.restore_session(restored)
            assert not fresh.has_valid_session()
            read = await ApiTaskExecutor(credential_manager=fresh).execute(ApiTestTask(
                task_id="negative-read", method="GET", path=url+"/protected", auth_ref=restored.credential_session_id,
                assertions=[AssertionSpec(kind="status_code", expected=401)]))
            assert read.status == "completed" and read.response_status == 401 and len(requests) == 2
            # Re-saving the fixed old JSON must not refresh a TTL or change its digest.
            await store.save_credential(raw, owner_session_id=parent.id, project_id=project.id)
            legacy = CredentialManager().create_from_login_response(response_body={"access_token": "fixture-legacy"})
            old_raw = legacy.model_dump(mode="json")
            await store.save_credential(old_raw, owner_session_id=parent.id, project_id=project.id)
            await store.save_credential(CredentialSession.model_validate(old_raw).model_dump(mode="json"),
                                        owner_session_id=parent.id, project_id=project.id)
            assert await store.load_credential(legacy.credential_session_id, owner_session_id=parent.id, project_id=project.id) == old_raw
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                for table in store.tables:
                    cur.execute(f"SELECT ciphertext FROM {table}")
                    assert all("issued-token" not in row["ciphertext"] and "fixture-legacy" not in row["ciphertext"] for row in cur.fetchall())
            save_facts("storage", {"requests": public_requests(requests), "expiry_roundtrip_equal": True,
                "old_hash_compatible": True, "encrypted_without_plain_values": True, "restored_available": fresh.has_valid_session(),
                "negative_status": read.response_status, "external_ai_calls": 0,
                "scope": "real HTTP and PG private store/service recreation; no independent API/Worker process kill"})

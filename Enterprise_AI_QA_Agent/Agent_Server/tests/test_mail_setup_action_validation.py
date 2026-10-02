from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.mail import router as mail_router
from src.application.mail.mail_service import MailService
from src.infrastructure.mail.providers import tencent_agently
from src.schemas.email_config import EmailConfigRecord


@pytest.mark.asyncio
async def test_unknown_setup_action_is_rejected() -> None:
    app = FastAPI()
    app.state.mail_service = MailService(None)
    app.include_router(mail_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/providers/agentmail/setup-action",
            json={"action": "stauts", "payload": {}},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["auth_login", "auth_login_status", "whoami"])
async def test_api_key_provider_without_oauth_action_returns_not_implemented(action):
    # Real default AgentMail adapter, without overriding any provider operation.
    records = [EmailConfigRecord(id=8, config_name="fixture", provider="agentmail", enabled=True)]
    app = FastAPI()
    app.state.mail_service = MailService(SimpleNamespace(list_all=lambda: records))
    app.include_router(mail_router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/mail/providers/agentmail/setup-action",
            json={"action": action, "payload": {"session_id": "unused"}},
        )
    assert response.status_code == 501, response.text


@pytest.mark.asyncio
async def test_login_without_enabled_mailbox_is_a_configuration_conflict():
    app = FastAPI()
    app.state.mail_service = MailService(SimpleNamespace(list_all=list))
    app.include_router(mail_router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/mail/providers/tencent_agently/setup-action",
            json={"action": "auth_login", "payload": {}},
        )
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "auth_status", "auth_login_status", "whoami"])
async def test_missing_configuration_remains_readable_for_status_queries(action):
    app = FastAPI()
    app.state.mail_service = MailService(SimpleNamespace(list_all=list))
    app.include_router(mail_router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/mail/providers/tencent_agently/setup-action",
            json={"action": action, "payload": {}},
        )
    assert response.status_code == 200
    assert response.json()["error"] == "no_enabled_config"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [FileNotFoundError, RuntimeError, OSError, ValueError])
async def test_controlled_auth_process_start_failure_is_not_a_successful_request(tmp_path, monkeypatch, caplog, error_type):
    # Controlled Popen boundary; no CLI/OAuth or Redis lock is run, no PATH edits.
    monkeypatch.chdir(tmp_path)

    def popen(*args, **kwargs):
        raise error_type("private authorization diagnostic")

    monkeypatch.setattr("src.infrastructure.mail.providers.tencent_agently.subprocess.Popen", popen)
    record = EmailConfigRecord(config_name="fixture", provider="tencent_agently", enabled=True)
    app = FastAPI()
    app.state.mail_service = MailService(SimpleNamespace(list_all=lambda: [record]))
    app.include_router(mail_router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.post("/api/v1/mail/providers/tencent_agently/setup-action", json={"action": "auth_login", "payload": {}})
    assert response.status_code == (503 if error_type is FileNotFoundError else 500), response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "private authorization diagnostic" not in response.text
    assert "private authorization diagnostic" not in caplog.text
    assert any(getattr(record, "action", None) == "auth_login" for record in caplog.records)


@pytest.mark.asyncio
async def test_started_auth_session_failure_remains_readable_status(tmp_path, monkeypatch):
    # Unlike a Popen exception, this controlled process was created successfully.
    # Its later exit is a session status, not an HTTP execution exception.
    monkeypatch.chdir(tmp_path)

    class Process:
        stdout = iter([])
        returncode = 3

        def wait(self):
            return self.returncode

    monkeypatch.setattr(tencent_agently.subprocess, "Popen", lambda *args, **kwargs: Process())
    record = EmailConfigRecord(config_name="fixture", provider="tencent_agently", enabled=True)
    app = FastAPI()
    app.state.mail_service = MailService(SimpleNamespace(list_all=lambda: [record]))
    app.include_router(mail_router, prefix="/api/v1")
    session_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/mail/providers/tencent_agently/setup-action", json={"action": "auth_login", "payload": {}})
            assert response.status_code == 200
            session_id = response.json()["session_id"]
            assert response.json()["status"] == "failed"
            status = await client.post("/api/v1/mail/providers/tencent_agently/setup-action", json={"action": "auth_login_status", "payload": {"session_id": session_id}})
        assert status.status_code == 200
        assert status.json()["session_id"] == session_id
        assert status.json()["status"] == "failed"
    finally:
        with tencent_agently._AUTH_LOGIN_LOCK:
            tencent_agently._AUTH_LOGIN_SESSIONS.pop(session_id, None)

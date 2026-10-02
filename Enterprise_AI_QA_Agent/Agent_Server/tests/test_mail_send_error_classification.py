"""Mail write HTTP contracts; no CLI or real provider request is executed."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.mail import router as mail_router
from src.application.mail import MailService
from src.schemas.email_config import EmailConfigRecord


class _ConfigStore:
    def __init__(self, records):
        self.records = records

    def list_all(self):
        return list(self.records)

    def get_by_id(self, config_id):
        for record in self.records:
            if record.id == config_id:
                return record
        raise KeyError(config_id)


def _record(config_id=7, **overrides):
    return EmailConfigRecord(
        id=config_id,
        config_name="contract-fixture",
        provider="openmail",
        enabled=True,
        is_default=True,
        extra_config={"mailbox_id": "fixture-inbox"},
        **overrides,
    )


def _client(store):
    app = FastAPI()
    app.state.mail_service = MailService(store)
    app.include_router(mail_router, prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["prepare", "confirm"])
@pytest.mark.parametrize("configuration", ["unavailable", "empty", "disabled", "non_default"])
async def test_mail_write_distinguishes_configuration_state_from_bad_input(action, configuration):
    # Ordinary API requests and real default adapters, without injecting exceptions.
    store = None
    config_id = None
    if configuration == "empty":
        store = _ConfigStore([])
    elif configuration == "disabled":
        record = _record().model_copy(update={"enabled": False, "is_default": False})
        store = _ConfigStore([record])
        config_id = record.id
    elif configuration == "non_default":
        record = _record(8).model_copy(update={"is_default": False})
        store = _ConfigStore([_record(), record])
        config_id = record.id
    payload = (
        {"recipients": ["qa@example.invalid"], "subject": "contract", "content": "contract"}
        if action == "prepare"
        else {"confirmation_token": "unused-fixture"}
    )
    payload["config_id"] = config_id
    async with _client(store) as client:
        response = await client.post(f"/api/v1/mail/test-send/{action}", json=payload)
    assert response.status_code == (503 if configuration == "unavailable" else 409), response.text
    assert "contract-fixture" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["prepare", "confirm"])
async def test_missing_mailbox_keeps_not_found_status(action):
    payload = (
        {"recipients": ["qa@example.invalid"], "subject": "contract", "content": "contract"}
        if action == "prepare"
        else {"confirmation_token": "unused-fixture"}
    )
    payload["config_id"] = 99
    async with _client(_ConfigStore([_record()])) as client:
        response = await client.post(f"/api/v1/mail/test-send/{action}", json=payload)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rest_preview_still_requires_confirmation_and_invalid_body_is_bad_input():
    async with _client(_ConfigStore([_record()])) as client:
        preview = await client.post(
            "/api/v1/mail/test-send/prepare",
            json={"recipients": ["qa@example.invalid"], "subject": "contract"},
        )
        assert preview.status_code == 200
        assert preview.json()["sent"] is False
        assert preview.json()["confirmation_required"] is True
        response = await client.post(
            "/api/v1/mail/test-send/confirm",
            json={"confirmation_token": preview.json()["confirmation_token"]},
        )
    # OpenMail validates the empty body before reaching its HTTP transport.
    assert response.status_code == 400
    assert "non-empty email body" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "timeout"])
async def test_confirm_classifies_controlled_provider_transport_failures(monkeypatch, caplog, failure):
    # Robustness contract, not evidence of a real provider outage. Reuse the
    # HTTP 422 envelope from test_mail_service's existing upstream-error fixture.
    calls = []

    def transport(method, url, **kwargs):
        calls.append((method, url))
        request = httpx.Request(method, url)
        if failure == "timeout":
            raise httpx.ReadTimeout("private transport diagnostic", request=request)
        return httpx.Response(
            422,
            request=request,
            json={"error": "inbox_limit_reached", "message": "Upgrade to create more."},
        )

    monkeypatch.setattr(httpx, "request", transport)
    record = _record().model_copy(update={"api_key": "fixture-only"})
    async with _client(_ConfigStore([record])) as client:
        preview = await client.post(
            "/api/v1/mail/test-send/prepare",
            json={"recipients": ["qa@example.invalid"], "subject": "contract", "content": "contract"},
        )
        assert preview.status_code == 200
        assert not calls
        response = await client.post(
            "/api/v1/mail/test-send/confirm",
            json={"confirmation_token": preview.json()["confirmation_token"]},
        )
    assert calls == [("POST", "https://api.openmail.sh/v1/inboxes/fixture-inbox/send")]
    assert response.status_code == 502, response.text
    assert "Upgrade" not in response.text
    assert "private transport diagnostic" not in response.text
    events = [record for record in caplog.records if record.name == "src.api.routes.mail"]
    assert len(events) == 1
    assert events[0].action == "confirm_send"
    assert events[0].config_id is None
    assert "Upgrade" not in caplog.text
    assert "private transport diagnostic" not in caplog.text


@pytest.mark.asyncio
async def test_mailbox_switch_invalidates_previous_preview_with_conflict_status():
    # Use a normal preparation and a supported active-mailbox transition. The
    # real default REST adapter never executes because its preview is obsolete.
    store = _ConfigStore([_record()])
    async with _client(store) as client:
        preview = await client.post(
            "/api/v1/mail/test-send/prepare",
            json={"recipients": ["qa@example.invalid"], "subject": "contract", "content": "contract"},
        )
        assert preview.status_code == 200
        store.records = [_record(8)]
        response = await client.post(
            "/api/v1/mail/test-send/confirm",
            json={"confirmation_token": preview.json()["confirmation_token"]},
        )
    assert response.status_code == 409, response.text

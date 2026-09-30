from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.mail import router as mail_router
from src.application.mail.contracts import (
    MailCapability,
    MailProviderAdapter,
    MailSendRequest,
    MailSendResult,
)
from src.application.mail.mail_service import MailService
from src.application.mail.provider_registry import (
    MailProviderRegistry,
    build_default_mail_provider_registry,
)

_DEFAULT_WEBHOOK_PROVIDERS = (
    "agentmail",
    "dead_simple_email",
    "openmail",
    "robotomail",
)


def _app(mail_service: MailService) -> FastAPI:
    app = FastAPI()
    app.state.mail_service = mail_service
    app.include_router(mail_router, prefix="/api/v1")
    return app


@pytest.mark.parametrize("provider", _DEFAULT_WEBHOOK_PROVIDERS)
def test_default_provider_does_not_advertise_unimplemented_webhook(provider: str) -> None:
    adapter = build_default_mail_provider_registry().resolve(provider)

    assert MailCapability.WEBHOOK not in adapter.capabilities()
    assert "webhook" not in adapter.descriptor()["capabilities"]
    assert not callable(getattr(adapter, "handle_webhook", None))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", _DEFAULT_WEBHOOK_PROVIDERS)
async def test_unimplemented_webhook_does_not_return_success(provider: str) -> None:
    app = _app(MailService(None))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/v1/mail/webhooks/{provider}",
            json={"event": "message.received"},
        )

    assert response.status_code == 501
    assert response.json() == {
        "detail": f"Webhook handling is not implemented for provider '{provider}'."
    }


@pytest.mark.asyncio
async def test_provider_catalog_matches_webhook_implementation() -> None:
    app = _app(MailService(None))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/mail/providers")

    assert response.status_code == 200
    providers = {item["provider"]: item for item in response.json()["providers"]}
    for provider in _DEFAULT_WEBHOOK_PROVIDERS:
        assert "webhook" not in providers[provider]["capabilities"]


@pytest.mark.asyncio
async def test_malformed_webhook_json_returns_client_error() -> None:
    app = _app(MailService(None))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/webhooks/agentmail",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid JSON webhook payload."}


class _WebhookAdapter(MailProviderAdapter):
    provider_key = "webhook_test"

    def __init__(self, *, advertised: bool = True) -> None:
        self.advertised = advertised
        self.received: list[object] = []

    def capabilities(self) -> set[MailCapability]:
        capabilities = {MailCapability.SEND}
        if self.advertised:
            capabilities.add(MailCapability.WEBHOOK)
        return capabilities

    def send(self, record, request: MailSendRequest) -> MailSendResult:
        raise NotImplementedError

    def handle_webhook(self, body: object) -> dict[str, object]:
        self.received.append(body)
        return {"ok": True, "handled": True}


@pytest.mark.asyncio
async def test_advertised_webhook_handler_receives_payload() -> None:
    adapter = _WebhookAdapter()
    registry = MailProviderRegistry([adapter])
    app = _app(MailService(None, registry))
    payload = {"id": "event-1", "type": "message.received"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/mail/webhooks/webhook_test", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handled": True}
    assert adapter.received == [payload]


@pytest.mark.asyncio
async def test_webhook_handler_without_declared_capability_is_not_called() -> None:
    adapter = _WebhookAdapter(advertised=False)
    registry = MailProviderRegistry([adapter])
    app = _app(MailService(None, registry))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/webhooks/webhook_test",
            json={"event": "message.received"},
        )

    assert response.status_code == 501
    assert adapter.received == []

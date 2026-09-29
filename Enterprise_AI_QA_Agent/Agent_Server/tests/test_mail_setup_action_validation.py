from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.mail import router as mail_router
from src.application.mail.mail_service import MailService


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

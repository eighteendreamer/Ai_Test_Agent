from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.oauth import router as oauth_router


INJECTED_HTML = '<img src=x onerror=alert(1)>'


class _OAuthService:
    def __init__(self) -> None:
        self.failure_reason = ""
        self.completed_error = ""

    def mark_failed_flow(self, _state: str, reason: str) -> None:
        self.failure_reason = reason

    async def handle_callback(self, *, code: str, state: str) -> None:
        return None

    def get_completed_flow(self, _state: str):
        return SimpleNamespace(status="failed", error=self.completed_error)


def _client() -> tuple[TestClient, _OAuthService]:
    app = FastAPI()
    app.include_router(oauth_router, prefix="/api/v1")
    service = _OAuthService()
    app.state.oauth_token_service = service
    return TestClient(app), service


@pytest.mark.parametrize("path", ["/callback", "/github/callback"])
def test_oauth_provider_error_is_html_escaped(path: str) -> None:
    client, service = _client()

    response = client.get(
        f"/api/v1/oauth{path}",
        params={"state": "state-1", "error": "access_denied", "error_description": INJECTED_HTML},
    )

    assert response.status_code == 200
    assert "<img" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    assert service.failure_reason.endswith(INJECTED_HTML)


def test_oauth_completed_failure_is_html_escaped() -> None:
    client, service = _client()
    service.completed_error = INJECTED_HTML

    response = client.get("/api/v1/oauth/callback", params={"state": "state-1", "code": "code-1"})

    assert response.status_code == 200
    assert "<img" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text

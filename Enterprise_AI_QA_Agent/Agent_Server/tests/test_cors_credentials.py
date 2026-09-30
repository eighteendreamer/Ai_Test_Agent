from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("origins", "expected_origin", "expected_credentials"),
    [
        ('["*"]', "*", None),
        ('["https://trusted.example"]', "https://trusted.example", "true"),
    ],
)
def test_real_app_cors_credentials_follow_origin_scope(
    origins: str,
    expected_origin: str,
    expected_credentials: str | None,
) -> None:
    script = """
import os
from fastapi.testclient import TestClient
from src.main import app

token = os.environ["API_AUTH_TOKEN"]
class SettingsStub:
    def list_model_configs(self):
        return []

app.state.settings_service = SettingsStub()
response = TestClient(app).get(
    "/api/v1/settings/models",
    headers={
        "Origin": "https://trusted.example",
        "Cookie": "test=marker",
        "Authorization": f"Bearer {token}",
    },
)
assert response.status_code == 200
assert response.headers.get("access-control-allow-origin") == EXPECTED_ORIGIN
assert response.headers.get("access-control-allow-credentials") == EXPECTED_CREDENTIALS
preflight = TestClient(app).options(
    "/api/v1/settings/models",
    headers={
        "Origin": "https://trusted.example",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    },
)
assert preflight.status_code == 200
assert preflight.headers.get("access-control-allow-origin") == EXPECTED_ORIGIN
assert preflight.headers.get("access-control-allow-credentials") == EXPECTED_CREDENTIALS
assert "authorization" in preflight.headers.get("access-control-allow-headers", "").lower()
""".replace("EXPECTED_ORIGIN", repr(expected_origin)).replace(
        "EXPECTED_CREDENTIALS", repr(expected_credentials)
    )
    env = {
        **os.environ,
        "API_AUTH_TOKEN": "cors-test-token-" + "x" * 40,
        "FRONTEND__CORS_ORIGINS": origins,
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

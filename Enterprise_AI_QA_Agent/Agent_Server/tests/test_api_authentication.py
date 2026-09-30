from __future__ import annotations

import os
import subprocess
import sys

_TEST_API_TOKEN = "test-api-auth-token-" + "x" * 40


def _run_python(script: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **overrides}
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_app_protects_api_and_preserves_only_required_public_routes() -> None:
    script = """
import asyncio
import os
from fastapi.testclient import TestClient
from fastapi import FastAPI
from src.api.api_auth_middleware import APIAuthenticationMiddleware
from src.main import app

token = os.environ["API_AUTH_TOKEN"]
app.state.settings_service = type("SettingsStub", (), {
    "list_model_configs": lambda self: [],
})()
app.state.docker_management_service = type("DockerStub", (), {
    "overview": lambda self: asyncio.sleep(0, {"docker": "stub"}),
})()
client = TestClient(app)

for path in ("/api/v1/settings/models", "/api/v1/docker/overview", "/metrics", "/docs"):
    assert client.get(path).status_code == 401, path
    assert client.get(path, headers={"Authorization": "Bearer wrong-token"}).status_code == 401, path

assert client.get("/api/v1/settings/models?api_token=" + token).status_code == 401
headers = {"Authorization": f"Bearer {token}"}
assert client.get("/api/v1/settings/models", headers=headers).status_code == 200
assert client.get("/api/v1/docker/overview", headers=headers).status_code == 200
assert client.get("/metrics", headers=headers).status_code == 200
assert client.get("/openapi.json", headers=headers).status_code == 200

unicode_auth = APIAuthenticationMiddleware(
    app,
    api_token="é" * 32,
    api_v1_prefix="/api/v1",
    app_env="test",
)
assert unicode_auth._is_valid_bearer(("Bearer " + "é" * 32).encode("utf-8"))

assert client.get("/api/v1/health/live").json() == {"status": "ok"}
assert client.get("/api/v1/health").status_code == 401
assert client.get("/api/v1/oauth/callback").status_code == 200
assert client.get("/api/v1/oauth/google/callback").status_code == 200
assert client.post("/api/v1/mail/webhooks/provider").status_code == 401

preflight = client.options(
    "/api/v1/settings/models",
    headers={
        "Origin": "http://127.0.0.1:5175",
        "Access-Control-Request-Method": "GET",
    },
)
assert preflight.status_code == 200
assert preflight.headers.get("access-control-allow-origin") == "http://127.0.0.1:5175"

development_app = FastAPI()
development_app.add_middleware(
    APIAuthenticationMiddleware,
    api_token=None,
    api_v1_prefix="/api/v1",
    app_env="development",
)
@development_app.get("/api/v1/private")
async def private_route():
    return {"ok": True}

assert TestClient(development_app).get("/api/v1/private").status_code == 200
remote_client = TestClient(development_app, client=("203.0.113.10", 12345))
assert remote_client.get("/api/v1/private").status_code == 503
"""
    result = _run_python(
        script,
        API_AUTH_TOKEN=_TEST_API_TOKEN,
        FRONTEND__CORS_ORIGINS='["http://127.0.0.1:5175"]',
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_settings_require_a_strong_api_auth_token() -> None:
    script = """
from src.core.config import Settings

try:
    Settings()
except ValueError as exc:
    assert "API_AUTH_TOKEN" in str(exc)
else:
    raise AssertionError("production settings accepted a missing API_AUTH_TOKEN")

import os
os.environ["API_AUTH_TOKEN"] = "x" * 31
try:
    Settings()
except ValueError as exc:
    assert "API_AUTH_TOKEN" in str(exc)
else:
    raise AssertionError("production settings accepted a short API_AUTH_TOKEN")

os.environ["API_AUTH_TOKEN"] = "x" * 32
assert Settings().api_auth_token.get_secret_value() == "x" * 32
"""
    result = _run_python(script, APP_ENV="production", API_AUTH_TOKEN="")
    assert result.returncode == 0, result.stdout + result.stderr

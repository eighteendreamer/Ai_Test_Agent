"""Real HTTP authentication acceptance for the API testing mode."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor


@contextmanager
def _auth_target():
    requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            requests.append({
                "path": path,
                "cookie": self.headers.get("Cookie", ""),
                "x_api_key": self.headers.get("X-API-Key", ""),
                "authorization": self.headers.get("Authorization", ""),
            })
            if path == "/cookie-protected" and self.headers.get("Cookie") == "sid=abc; tenant=blue":
                self._reply(200, {"ok": True, "auth": "cookie"})
                return
            if path == "/custom-protected" and self.headers.get("X-API-Key") == "fixture-key":
                self._reply(200, {"ok": True, "auth": "custom"})
                return
            if path == "/bearer-protected" and self.headers.get("Authorization") == "Bearer issued-token":
                self._reply(200, {"ok": True, "auth": "bearer"})
                return
            self._reply(401, {"ok": False})

        def _reply(self, status: int, body: dict) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

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


def test_user_cookie_header_is_parsed_and_sent_to_real_protected_endpoint():
    with _auth_target() as (base_url, requests):
        credentials = CredentialManager()
        session = credentials.create_from_user_input(
            extracted={"auth_type": "cookie", "cookie": "sid=abc; tenant=blue"}
        )
        task = asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(
            ApiTestTask(task_id="cookie", method="GET", path=base_url + "/cookie-protected",
                        auth_ref=session.credential_session_id)
        ))
        assert task.status == "completed", task.model_dump(mode="json")
        assert requests[-1]["cookie"] == "sid=abc; tenant=blue"


def test_custom_auth_header_is_sent_to_real_protected_endpoint():
    with _auth_target() as (base_url, requests):
        credentials = CredentialManager()
        session = credentials.create_from_user_input(
            extracted={
                "auth_type": "custom",
                "headers": {"X-API-Key": "fixture-key"},
            }
        )
        task = asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(
            ApiTestTask(task_id="custom", method="GET", path=base_url + "/custom-protected",
                        auth_ref=session.credential_session_id)
        ))
        assert task.status == "completed", task.model_dump(mode="json")
        assert requests[-1]["x_api_key"] == "fixture-key"


def test_dynamic_login_token_still_authenticates_downstream_request():
    with _auth_target() as (base_url, requests):
        credentials = CredentialManager()
        session = credentials.create_from_login_response(
            response_body={"access_token": "issued-token"},
            login_endpoint="POST /login",
        )
        task = asyncio.run(ApiTaskExecutor(credential_manager=credentials).execute(
            ApiTestTask(task_id="bearer", method="GET", path=base_url + "/bearer-protected",
                        auth_ref=session.credential_session_id)
        ))
        assert task.status == "completed", task.model_dump(mode="json")
        assert requests[-1]["authorization"] == "Bearer issued-token"

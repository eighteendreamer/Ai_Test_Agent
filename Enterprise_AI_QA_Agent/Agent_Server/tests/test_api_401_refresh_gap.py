"""Actual local HTTP evidence for the missing explicit 401 refresh contract."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from src.modes.api_testing_mode.campaign_state import ApiTestTask, AssertionSpec
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor


@contextmanager
def refresh_target():
    old, current = "old-" + uuid4().hex, "new-" + uuid4().hex
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append({"method": "POST", "path": self.path,
                "authorization": self.headers.get("Authorization", "")})
            if self.path == "/auth/refresh" and self.headers.get("Authorization") == "Bearer " + old:
                self.reply(200, {"access_token": current})
            else:
                self.reply(401, {"ok": False})

        def do_GET(self):
            requests.append({"method": "GET", "path": self.path,
                "authorization": self.headers.get("Authorization", "")})
            if self.path == "/profile" and self.headers.get("Authorization") == "Bearer " + current:
                self.reply(200, {"ok": True})
            else:
                self.reply(401, {"ok": False})

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", old, current, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.asyncio
async def test_401_does_not_guess_refresh_or_replay_and_explicit_sequence_works(tmp_path):
    with refresh_target() as (base, old, current, requests):
        credentials = CredentialManager()
        old_session = credentials.create_from_login_response(response_body={"access_token": old})
        executor = ApiTaskExecutor(credential_manager=credentials)

        profile = ApiTestTask(task_id="profile", method="GET", path=base + "/profile",
            full_url=base + "/profile", auth_ref=old_session.credential_session_id,
            assertions=[AssertionSpec(kind="status_code", expected=200)])
        result = await executor.execute(profile)
        assert result.status == "failed" and result.response_status == 401
        assert [item["path"] for item in requests] == ["/profile"]

        refresh = ApiTestTask(task_id="refresh", method="POST", path=base + "/auth/refresh",
            full_url=base + "/auth/refresh", execution_mode="auth",
            auth_ref=old_session.credential_session_id,
            assertions=[AssertionSpec(kind="status_code", expected=200)])
        refreshed = await executor.execute(refresh)
        assert refreshed.status == "completed" and refreshed.auth_ref
        assert credentials.get(refreshed.auth_ref).token == current

        replay = ApiTestTask(task_id="profile-replay", method="GET", path=base + "/profile",
            full_url=base + "/profile", auth_ref=refreshed.auth_ref,
            assertions=[AssertionSpec(kind="status_code", expected=200)])
        replayed = await executor.execute(replay)
        assert replayed.status == "completed" and replayed.response_status == 200
        assert [item["path"] for item in requests] == ["/profile", "/auth/refresh", "/profile"]

        write = ApiTestTask(task_id="write", method="POST", path=base + "/orders",
            full_url=base + "/orders", execution_mode="write",
            auth_ref=refreshed.auth_ref,
            assertions=[AssertionSpec(kind="status_code", expected=401)])
        denied = await executor.execute(write)
        assert denied.status == "completed" and denied.response_status == 401
        assert [item["path"] for item in requests].count("/orders") == 1

        facts = {"scope": "actual local HTTP and existing ApiTaskExecutor/CredentialManager",
            "automatic_refresh": False, "initial_401_requests": 1,
            "explicit_refresh_and_replay_requests": 2, "write_401_replay_requests": 1,
            "old_credential_not_replayed_after_explicit_refresh": True,
            "external_ai_calls": 0, "full_goal_complete": False}
        if directory := os.getenv("QA_API_401_REFRESH_EVIDENCE_DIR"):
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            (output / "verification.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")

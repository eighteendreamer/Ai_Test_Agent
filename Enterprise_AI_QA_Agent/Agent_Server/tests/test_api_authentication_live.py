"""Real HTTP authentication acceptance for the API testing mode."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.campaign_state import EndpointCandidate, ExecutionPolicy
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.precondition_resolver import PreconditionAnalysis
from src.modes.api_testing_mode.task_pool import ApiTaskPool


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


def test_real_coordinator_refreshes_auth_before_protected_request():
    """A refresh endpoint must replace the credential used by later tasks."""

    requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            authorization = self.headers.get("Authorization", "")
            requests.append({"path": path, "authorization": authorization})
            if path == "/login":
                self._reply(200, {"access_token": "token-1"})
                return
            if path == "/auth/refresh" and authorization == "Bearer token-1":
                self._reply(200, {"access_token": "token-2"})
                return
            self._reply(401, {"ok": False})

        def do_GET(self) -> None:  # noqa: N802
            authorization = self.headers.get("Authorization", "")
            requests.append({"path": urlsplit(self.path).path, "authorization": authorization})
            self._reply(200 if authorization == "Bearer token-2" else 401,
                        {"ok": authorization == "Bearer token-2"})

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
        base_url = f"http://127.0.0.1:{server.server_port}"
        endpoints = [
            EndpointCandidate(endpoint_id="login", method="POST", path="/login", full_url=base_url + "/login"),
            EndpointCandidate(endpoint_id="refresh", method="POST", path="/auth/refresh", full_url=base_url + "/auth/refresh"),
            EndpointCandidate(endpoint_id="profile", method="GET", path="/profile", full_url=base_url + "/profile"),
        ]
        mapped = CapabilityMapper().map_many(endpoints)
        graph = DependencyPlanner().plan(
            mapped_endpoints=mapped,
            preconditions=PreconditionAnalysis(),
            base_url=base_url,
        )
        assert [task.execution_mode for task in graph.tasks] == ["auth", "auth", "read"]
        graph.tasks[0].request_body = {"username": "fixture"}
        credentials = CredentialManager()
        executor = ApiTaskExecutor(credential_manager=credentials)
        results = asyncio.run(ApiTestCoordinator(
            pool=ApiTaskPool(graph.tasks),
            policy=ExecutionPolicy(max_workers=2),
            executor_fn=executor.execute,
        ).run_all())
        assert [task.status for task in results] == ["completed", "completed", "completed"], (
            [task.model_dump(mode="json") for task in results], requests
        )
        assert [(item["path"], item["authorization"]) for item in requests] == [
            ("/login", ""),
            ("/auth/refresh", "Bearer token-1"),
            ("/profile", "Bearer token-2"),
        ]
        assert all(task.auth_ref for task in results[1:])
        assert results[1].auth_ref == results[2].auth_ref
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()

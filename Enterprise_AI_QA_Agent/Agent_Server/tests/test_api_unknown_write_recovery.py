"""Real HTTP proof that an unknown write outcome is never retried."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from threading import Event

from src.modes.api_testing_mode.campaign_state import ApiTestTask, ExecutionPolicy
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.evaluation import ApiTestingEvaluationPolicy
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.task_pool import ApiTaskPool


@contextmanager
def delayed_write_target(*, status_code: int = 201, delay_seconds: float = 0.15):
    calls: list[dict] = []
    received = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append({"method": self.command, "path": self.path})
            received.set()
            time.sleep(delay_seconds)
            body = json.dumps({"created": len(calls)}).encode()
            try:
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                # The client timeout is the controlled failure being tested.
                pass

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/orders", calls, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def _record(name: str, payload: dict) -> None:
    directory = os.getenv("QA_API_UNKNOWN_WRITE_EVIDENCE_DIR")
    if directory:
        path = Path(directory) / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_actual_unknown_write_timeout_is_not_retried_and_is_reported_for_reconciliation():
    with delayed_write_target() as (url, calls, received):
        task = ApiTestTask(
            task_id="unknown-write",
            method="POST",
            path=url,
            full_url=url,
            execution_mode="write",
            request_body={"sku": "controlled"},
            timeout_seconds=0.05,
            status="ready",
        )
        executor = ApiTaskExecutor(credential_manager=CredentialManager())
        tasks = asyncio.run(ApiTestCoordinator(
            pool=ApiTaskPool([task]),
            policy=ExecutionPolicy(max_retries=1),
            executor_fn=executor.execute,
        ).run_all())
        result = tasks[0]
        classification = ApiTestingEvaluationPolicy()._categorize_failure(result)
        _record("unknown-write", {
            "request_count": len(calls),
            "server_requests": calls,
            "task": result.model_dump(mode="json"),
            "classification": {"category": classification[0], "severity": classification[1],
                                "is_transient": classification[2]},
        })
        assert len(calls) == 1
        assert result.attempts == 1
        assert result.status == "failed"
        assert result.request_outcome == "unknown"
        assert result.response_status is None and result.response_body is None
        assert "requires read-only reconciliation" in result.last_error
        assert classification == ("unknown_side_effect", "critical", False)


def test_actual_write_server_error_is_unknown_and_not_retried():
    with delayed_write_target(status_code=500, delay_seconds=0) as (url, calls, _received):
        task = ApiTestTask(
            task_id="server-error-write",
            method="POST",
            path=url,
            full_url=url,
            execution_mode="write",
            request_body={"sku": "controlled"},
            timeout_seconds=1,
            status="ready",
        )
        result = asyncio.run(ApiTestCoordinator(
            pool=ApiTaskPool([task]),
            policy=ExecutionPolicy(max_retries=1),
            executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute,
        ).run_all())[0]
        _record("server-error-write", {"request_count": len(calls), "server_requests": calls,
                                        "task": result.model_dump(mode="json")})
        assert len(calls) == 1
        assert result.attempts == 1
        assert result.response_status == 500
        assert result.request_outcome == "unknown"

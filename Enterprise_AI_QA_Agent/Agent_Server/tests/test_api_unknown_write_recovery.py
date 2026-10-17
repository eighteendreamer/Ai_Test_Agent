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

import pytest

from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, AssertionSpec, ExecutionPolicy
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.evaluation import ApiTestingEvaluationPolicy
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.runtime.store import InMemorySessionStore
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher


@contextmanager
def reconciliation_target(*, effect_present: bool):
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            calls.append({"method": self.command, "path": self.path})
            self._reply(500, {"ok": False})

        def do_GET(self):
            calls.append({"method": self.command, "path": self.path})
            self._reply(200 if effect_present else 404, {"exists": effect_present})

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


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
        assert "reconciliation" in result.last_error and "unknown" in result.last_error
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
            assertions=[{"kind": "status_code", "expected": 500, "description": "Expected server error"}],
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
        assert result.status == "failed"
        assert "reconciliation" in result.last_error and "unknown" in result.last_error


def test_unknown_write_blocks_dependents_even_when_error_assertion_matches():
    calls = []

    async def executor(task):
        calls.append(task.task_id)
        if task.task_id == "create":
            task.status = "failed"
            task.request_outcome = "unknown"
            task.response_status = 500
            task.check_results = [{"name": "expected error", "passed": True}]
            task.last_error = "Write request outcome is unknown; read-only reconciliation is required before dependent tasks or retry."
        else:
            task.status = "completed"
            task.request_outcome = "completed"
        return task

    create = ApiTestTask(task_id="create", method="POST", path="/orders", execution_mode="write",
        status="ready")
    dependent = ApiTestTask(task_id="read", method="GET", path="/orders/{id}", depends_on=["create"],
        status="pending")
    result = asyncio.run(ApiTestCoordinator(pool=ApiTaskPool([create, dependent]),
        policy=ExecutionPolicy(max_retries=1), executor_fn=executor).run_all())
    assert calls == ["create"]
    assert result[0].status == "failed" and result[0].request_outcome == "unknown"
    assert result[1].status == "skipped"
    assert "dependency create failed" in result[1].last_error


def test_unknown_write_evaluation_requires_read_only_reconciliation():
    task = ApiTestTask(task_id="unknown", method="POST", path="/orders", status="failed",
        request_outcome="unknown", response_status=500,
        last_error="Write request outcome is unknown; read-only reconciliation is required before dependent tasks or retry.")
    evaluation = ApiTestingEvaluationPolicy().evaluate(
        campaign=ApiTestCampaign(campaign_id="unknown-write", objective="unknown write", tasks=[task]),
    )
    assert any("只读查证" in item and "禁止重试" in item for item in evaluation.recommendations)
    assert evaluation.failure_classifications[0].category == "unknown_side_effect"


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("effect_present,observation", [(False, "effect_absent"), (True, "effect_present")])
def test_explicit_read_only_reconciliation_runs_without_replaying_unknown_write(tmp_path, worker, effect_present, observation):
    with reconciliation_target(effect_present=effect_present) as (base, calls):
        unknown = ApiTestTask(task_id="create", method="POST", path=base+"/orders", full_url=base+"/orders",
            execution_mode="write", status="ready", request_body={"key": "fixture"})
        reconcile = ApiTestTask(task_id="check", method="GET", path=base+"/orders?key=fixture",
            full_url=base+"/orders?key=fixture", execution_mode="read", status="blocked",
            reconciliation_for="create", reconciliation_observation=observation,
            assertions=[AssertionSpec(kind="status_code", expected=200 if effect_present else 404)])
        dependent = ApiTestTask(task_id="after", method="GET", path=base+"/orders/details", full_url=base+"/orders/details",
            status="blocked", depends_on=["create"])
        campaign = ApiTestCampaign(campaign_id="explicit-reconciliation", tasks=[unknown, reconcile, dependent])
        credentials = CredentialManager()
        if worker:
            store = InMemorySessionStore()
            runner = ApiSubagentCoordinator(pool=ApiTaskPool(campaign.tasks), policy=ExecutionPolicy(max_retries=2),
                credential_manager=credentials, coordinator_runtime_service=ControlledWorkerLauncher(store),
                session_store=store, parent_context={"session_id": "owned-parent", "turn_id": "owned-turn"})
        else:
            runner = ApiTestCoordinator(pool=ApiTaskPool(campaign.tasks), policy=ExecutionPolicy(max_retries=2),
                executor_fn=ApiTaskExecutor(credential_manager=credentials).execute)
        result = asyncio.run(runner.run_all())
        count = len(calls)
        asyncio.run(runner.run_all())
        report = ReportBuilder().build(campaign=ApiTestCampaign(campaign_id=campaign.campaign_id, tasks=result))
        _record(f"explicit-reconciliation-{observation}", {"calls": calls, "tasks": [t.model_dump(mode="json") for t in result],
            "report": report.model_dump(mode="json"), "terminal_extra_http": len(calls)-count, "external_ai_calls": 0})
        assert [item.status for item in result] == ["failed", "completed", "skipped"]
        assert result[0].request_outcome == "unknown" and result[0].reconciliation_status == observation
        assert result[0].attempts == 1 and result[1].attempts == 1 and result[2].attempts == 0
        assert [item["method"] for item in calls] == ["POST", "GET"] and len(calls) == count == 2
        assert report.tasks[0]["reconciliation_status"] == observation
        assert report.tasks[0]["reconciliation_required"] is False


def test_explicit_reconciliation_without_observation_remains_inconclusive():
    with reconciliation_target(effect_present=False) as (base, calls):
        unknown = ApiTestTask(task_id="create", method="POST", path=base+"/orders", full_url=base+"/orders",
            execution_mode="write", status="ready")
        check = ApiTestTask(task_id="check", method="GET", path=base+"/orders", full_url=base+"/orders",
            status="blocked", reconciliation_for="create",
            assertions=[AssertionSpec(kind="status_code", expected=404)])
        result = asyncio.run(ApiTestCoordinator(pool=ApiTaskPool([unknown, check]), policy=ExecutionPolicy(max_retries=0),
            executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute).run_all())
        assert result[0].request_outcome == "unknown" and result[0].reconciliation_status == "inconclusive"
        assert result[1].status == "completed" and result[1].reconciliation_observation == ""
        assert len(calls) == 2


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_reconciliation_task_must_be_read_only_before_any_http(method):
    unknown = ApiTestTask(task_id="write", method="POST", path="/write", status="ready")
    check = ApiTestTask(task_id="check", method=method, path="/check", status="blocked", reconciliation_for="write")
    with pytest.raises(ValueError, match="reconciliation_must_be_read_only"):
        ApiTaskPool([unknown, check])


def test_api_report_contains_redacted_request_and_response_details():
    task = ApiTestTask(task_id="reported", method="POST", path="/login", status="completed",
        request_headers={"Authorization": "Bearer secret-value"},
        request_body={"password": "secret-password", "enabled": False},
        response_status=200, response_headers={"Set-Cookie": "sid=secret-cookie"},
        response_body={"access_token": "secret-token", "ok": True})
    report = ReportBuilder().build(campaign=ApiTestCampaign(campaign_id="api-report", tasks=[task]))
    payload = report.tasks[0]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "secret-value" not in encoded and "secret-password" not in encoded
    assert "secret-cookie" not in encoded and "secret-token" not in encoded
    assert payload["request"]["body"]["enabled"] is False
    assert payload["response"]["status"] == 200

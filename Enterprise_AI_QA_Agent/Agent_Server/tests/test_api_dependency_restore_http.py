"""Resume real HTTP consumers in a new process after a producer checkpoint."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, ExecutionPolicy, InputBinding
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.input_binding import unresolved_path_parameters
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool


@contextmanager
def target():
    requests = []
    created = False

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            nonlocal created
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(raw) if raw else None
            requests.append({"method": "POST", "path": self.path, "body": body})
            if self.path == "/orders":
                created = True
                self.reply(201, {"id": 0, "data": {"public_id": 0, "enabled": False, "count": 0}})
            else:
                query = parse_qs(urlsplit(self.path).query)
                valid = (created and urlsplit(self.path).path == "/confirm"
                         and query == {"id": ["0"]}
                         and body == {"enabled": False, "context": {"count": 0}})
                self.reply(200 if valid else 422, {"ok": valid})

        def do_GET(self):
            requests.append({"method": "GET", "path": self.path})
            valid = created and self.path == "/orders/0"
            self.reply(200 if valid else 404, {"ok": valid})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def child(snapshot, result_path, interrupt):
    campaign = ApiTestCampaign.model_validate_json(snapshot.read_text(encoding="utf-8"))

    def checkpoint(event, task, tasks):
        if event == "task_completed" and task.task_id == "create" and interrupt:
            campaign.tasks = tasks
            snapshot.write_text(campaign.model_dump_json(indent=2), encoding="utf-8")
            # Abrupt exit before the coordinator can retain its in-memory output.
            os._exit(73)

    coordinator = ApiTestCoordinator(pool=ApiTaskPool(campaign.tasks), policy=campaign.execution_policy,
        executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute,
        checkpoint_callback=checkpoint)
    campaign.tasks = asyncio.run(coordinator.run_all())
    frozen = campaign.model_dump(mode="json")
    asyncio.run(coordinator.run_all())
    assert campaign.model_dump(mode="json") == frozen
    result_path.write_text(json.dumps({"campaign": frozen,
        "report": ReportBuilder().build(campaign=campaign).model_dump(mode="json"),
        "pid": os.getpid(), "external_ai_calls": 0}, indent=2), encoding="utf-8")


@pytest.mark.parametrize("variant", ["brace", "colon", "explicit", "body-query", "effective-url"])
def test_new_process_restores_completed_producer_without_repeating_write(tmp_path, variant):
    evidence = Path(os.getenv("QA_API_DEPENDENCY_RESTORE_DIR", str(tmp_path))) / variant
    evidence.mkdir(parents=True, exist_ok=False)
    snapshot, result_path = evidence / "checkpoint.json", evidence / "result.json"
    with target() as (url, requests):
        create = ApiTestTask(task_id="create", method="POST", path="/orders", full_url=url+"/orders",
            execution_mode="write", status="ready")
        path = "/orders/:id" if variant == "colon" else "/orders/{id}"
        read = ApiTestTask(task_id="read", method="GET", path=path, full_url=url+path,
            status="blocked", depends_on=["create"], path_parameter_sources={"id": "create"})
        if variant == "explicit":
            read.input_bindings = [InputBinding(source_task_id="create", source_path="data.public_id", target_path="path.id")]
        if variant == "effective-url":
            read.path = "/orders"
        if variant == "body-query":
            read.method, read.path, read.full_url = "POST", "/confirm", url+"/confirm"
            read.execution_mode = "write"
            read.input_bindings = [InputBinding(source_task_id="create", source_path=source, target_path=dest)
                for source, dest in [("id", "query.id"), ("data.enabled", "body.enabled"),
                                     ("data.count", "body.context.count")]]
        campaign = ApiTestCampaign(campaign_id="restore-"+variant, tasks=[create, read],
            execution_policy=ExecutionPolicy(max_retries=0))
        snapshot.write_text(campaign.model_dump_json(indent=2), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        command = [sys.executable, str(Path(__file__).resolve()), str(snapshot), str(result_path)]
        first = subprocess.run(command+["interrupt"], env=env, capture_output=True, text=True, timeout=30)
        (evidence/"first.stdout").write_text(first.stdout, encoding="utf-8")
        (evidence/"first.stderr").write_text(first.stderr, encoding="utf-8")
        assert first.returncode == 73, first.stderr
        saved = json.loads(snapshot.read_text(encoding="utf-8"))
        assert saved["tasks"][0]["status"] == "completed"
        assert saved["tasks"][0]["response_body"]["id"] == 0
        assert len(requests) == 1
        second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        (evidence/"second.stdout").write_text(second.stdout, encoding="utf-8")
        (evidence/"second.stderr").write_text(second.stderr, encoding="utf-8")
        assert second.returncode == 0, second.stderr
        result = json.loads(result_path.read_text(encoding="utf-8"))
        (evidence/"http.json").write_text(json.dumps({"requests": requests,
            "first_exit": first.returncode, "second_exit": second.returncode}, indent=2), encoding="utf-8")
        assert all(t["status"] == "completed" for t in result["campaign"]["tasks"]), result
        assert result["campaign"]["tasks"][0] == saved["tasks"][0]
        assert len(requests) == 2 and requests[0]["path"] == "/orders"
        assert requests[1]["path"] == ("/confirm?id=0" if variant == "body-query" else "/orders/0")
        assert result["report"]["completed"] == 2 and result["report"]["failed"] == 0


@pytest.mark.parametrize("coordinator_type", [ApiTestCoordinator, ApiSubagentCoordinator])
def test_effective_url_missing_parameter_does_not_send_or_dispatch(tmp_path, coordinator_type):
    with target() as (url, requests):
        item = ApiTestTask(task_id="read", method="GET", path="/orders", full_url=url+"/orders/{id}", status="ready")
        pool, policy = ApiTaskPool([item]), ExecutionPolicy(max_retries=0)
        dispatched = []

        class Dispatcher:
            async def dispatch(self, **kwargs):
                dispatched.append(kwargs)
                raise AssertionError("missing input must not dispatch")

        if coordinator_type == ApiTestCoordinator:
            coordinator = coordinator_type(pool=pool, policy=policy,
                executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute)
        else:
            coordinator = coordinator_type(pool=pool, policy=policy, coordinator_runtime_service=Dispatcher(),
                session_store=None, parent_context={}, credential_manager=CredentialManager())
        result = asyncio.run(coordinator.run_all())[0]
        assert result.status == "failed" and result.attempts == 0
        assert "id" in result.last_error and requests == dispatched == []


@pytest.mark.parametrize("coordinator_type", [ApiTestCoordinator, ApiSubagentCoordinator])
def test_restore_uses_only_completed_outputs_from_its_own_campaign(coordinator_type):
    completed = ApiTestTask(task_id="create", method="POST", path="/orders", status="completed", response_body={"id": 0})
    failed = ApiTestTask(task_id="failed", method="POST", path="/orders", status="failed",
        request_outcome="unknown", response_body={"id": 99})
    pending = ApiTestTask(task_id="pending", method="GET", path="/orders", status="ready", response_body={"id": 88})
    pool, policy = ApiTaskPool([completed, failed, pending]), ExecutionPolicy()
    if coordinator_type == ApiTestCoordinator:
        coordinator = coordinator_type(pool=pool, policy=policy, executor_fn=None)
    else:
        coordinator = coordinator_type(pool=pool, policy=policy, coordinator_runtime_service=None,
            session_store=None, parent_context={}, credential_manager=CredentialManager())
    assert coordinator._task_outputs == {"create": {"id": 0}}
    before = completed.model_dump(mode="json")
    consumer = ApiTestTask(task_id="consumer", method="GET", path="/orders/{id}", depends_on=["create"],
        path_parameter_sources={"id": "create"})
    coordinator._apply_input_bindings(consumer)
    assert consumer.path == "/orders/0" and completed.model_dump(mode="json") == before


@pytest.mark.parametrize("path,url", [
    ("/orders/{id}", "http://127.0.0.1:8000/orders/0"),
    ("/health", "http://127.0.0.1:8000/health?filter={id}#section-{id}"),
    ("/health?filter={id}#section-{id}", ""),
])
def test_path_validation_ignores_display_path_ports_query_and_fragment(path, url):
    item = ApiTestTask(task_id="read", method="GET", path=path, full_url=url)
    assert unresolved_path_parameters(item) == []


if __name__ == "__main__":
    child(Path(sys.argv[1]), Path(sys.argv[2]), len(sys.argv) > 3)

"""API task admission uses the graph, rather than a saved ready label."""
import asyncio
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign,
    ApiTestTask,
    ApiTestingState,
    ApiTestingRequestState,
    EndpointCandidate,
    ExecutionPolicy,
    ProjectCandidate,
)
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore


def task(identity, **fields):
    return ApiTestTask(task_id=identity, method="GET", path="/health", status="ready", **fields)


@pytest.mark.parametrize("defect", ["missing", "self", "cycle", "duplicate_task", "duplicate_dependency"])
def test_invalid_graph_rejected_before_task_status_changes(defect):
    first, second = task("first"), task("second")
    tasks = [first, second]
    if defect == "missing": first.depends_on = ["absent"]
    if defect == "self": first.depends_on = [first.task_id]
    if defect == "cycle":
        first.depends_on, second.depends_on = [second.task_id], [first.task_id]
    if defect == "duplicate_task": second.task_id = first.task_id
    if defect == "duplicate_dependency": first.depends_on = [second.task_id, second.task_id]
    before = [item.model_dump(mode="json") for item in tasks]
    with pytest.raises(ValueError, match="API task"):
        ApiTaskPool(tasks)
    assert [item.model_dump(mode="json") for item in tasks] == before


@pytest.mark.parametrize("coordinator_type", [ApiTestCoordinator, ApiSubagentCoordinator])
def test_saved_ready_label_does_not_bypass_actual_predecessor(coordinator_type):
    parent = task("parent")
    child = task("child", depends_on=[parent.task_id])
    pool = ApiTaskPool([child, parent])
    coordinator = object.__new__(coordinator_type)
    coordinator._pool, coordinator._policy = pool, ExecutionPolicy()
    coordinator._write_running, coordinator._active_resource_locks = False, set()
    assert [item.task_id for item in coordinator._select_batch()] == [parent.task_id]
    assert child.status == "blocked"
    pool.mark_failed(parent.task_id, "predecessor rejected")
    assert coordinator._select_batch() == []
    assert child.status == "skipped" and child.attempts == 0


def test_revalidation_rejects_changed_reference_without_rewriting_terminal_result():
    completed = task("completed", response_status=200, response_body={"id": 0})
    completed.status = "completed"
    child = task("child", depends_on=[completed.task_id])
    pool = ApiTaskPool([completed, child])
    frozen = completed.model_dump(mode="json")
    child.depends_on = ["foreign-task"]
    with pytest.raises(ValueError, match="API task"):
        pool.ready_tasks()
    assert completed.model_dump(mode="json") == frozen


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
            requests.append(["POST", self.path])
            created = True
            self.reply(201, {"id": 0})
        def do_GET(self):
            requests.append(["GET", self.path])
            self.reply(200 if created and self.path == "/orders/0" else 404, {"id": 0})
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_real_http_obeys_predecessor_even_when_every_saved_task_is_ready(tmp_path):
    with target() as (url, requests):
        create = ApiTestTask(task_id="create", method="POST", path="/orders", full_url=url+"/orders",
            execution_mode="write", status="ready")
        read = ApiTestTask(task_id="read", method="GET", path="/orders/{id}", full_url=url+"/orders/{id}",
            depends_on=[create.task_id], path_parameter_sources={"id": create.task_id}, status="ready")
        executor = ApiTaskExecutor(credential_manager=CredentialManager())
        pool = ApiTaskPool([read, create])
        coordinator = ApiTestCoordinator(pool=pool, policy=ExecutionPolicy(), executor_fn=executor.execute)
        result = asyncio.run(coordinator.run_all())
        assert requests == [["POST", "/orders"], ["GET", "/orders/0"]]
        assert all(item.status == "completed" for item in result)
        assert all(item.attempts == 1 for item in result)
        before = [item.model_dump(mode="json") for item in result]
        asyncio.run(coordinator.run_all())
        assert [item.model_dump(mode="json") for item in result] == before
        assert len(requests) == 2
        report = ReportBuilder().build(campaign=ApiTestCampaign(campaign_id="dependency-admission", tasks=result))
        assert report.completed == 2 and report.failed == report.skipped == 0
        folder = Path(os.getenv("QA_API_DEPENDENCY_EVIDENCE_DIR", str(tmp_path)))
        folder.mkdir(parents=True, exist_ok=True)
        (folder/"actual-http.json").write_text(json.dumps({"requests": requests, "report": report.model_dump(mode="json"),
            "tasks": before, "terminal_extra_http": 0, "external_ai_calls": 0}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_unresolved_path_is_reported_without_http_or_worker_dispatch(tmp_path):
    with target() as (url, requests):
        task_item = ApiTestTask(task_id="missing", method="GET", path="/orders/{id}",
            full_url=url + "/orders/{id}", status="ready")
        inline = ApiTestCoordinator(
            pool=ApiTaskPool([task_item]), policy=ExecutionPolicy(),
            executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute,
        )
        result = asyncio.run(inline.run_all())[0]
        assert result.status == "failed"
        assert "id" in result.last_error and result.attempts == 0
        assert requests == []
        report = ReportBuilder().build(campaign=ApiTestCampaign(campaign_id="missing-input", tasks=[result]))
        assert report.failed == 1 and "unresolved" in report.findings[0]

        dispatched = []
        class Dispatcher:
            async def dispatch(self, **kwargs):
                dispatched.append(kwargs)
                raise AssertionError("unresolved path must not dispatch a worker")

        worker_task = ApiTestTask(task_id="missing-worker", method="GET", path="/orders/{id}",
            full_url=url + "/orders/{id}", status="ready")
        worker = ApiSubagentCoordinator(
            pool=ApiTaskPool([worker_task]), policy=ExecutionPolicy(),
            coordinator_runtime_service=Dispatcher(), session_store=InMemorySessionStore(),
            parent_context={"session_id": "parent", "turn_id": "turn"},
            credential_manager=CredentialManager(),
        )
        worker_result = asyncio.run(worker.run_all())[0]
        assert worker_result.status == "failed" and dispatched == []


def test_campaign_freezes_precondition_gap_for_review():
    runtime = ApiTestingModeRuntime(api_docs_service=None)
    state = ApiTestingState(
        request=ApiTestingRequestState(objective="查询订单详情"),
        selected_project=ProjectCandidate(project_id="project-1", project_name="订单项目"),
        selected_endpoints=[EndpointCandidate(endpoint_id="detail", method="GET", path="/orders/{id}")],
    )
    runtime._build_campaign(state)
    assert state.campaign is not None
    assert state.campaign.preconditions[0]["requires_manual_data"] is True
    assert state.campaign.preconditions[0]["path_parameter_dependencies"] == {"detail": ["id"]}

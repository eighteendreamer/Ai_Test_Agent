"""Normal JSON payloads through the real API executor and a local TCP server."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from src.application.test_runs.case_execution import CaseExecutionAdapter
from src.modes.api_testing_mode.campaign_state import ApiTestTask, ExecutionPolicy
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore
from src.schemas.agent import ToolDescriptor
from tests.test_case_execution_adapter import _fixture


@pytest.fixture
def echo_url():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            response = json.dumps({
                "raw_body": raw.decode("utf-8"),
                "content_type": self.headers.get("Content-Type"),
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/echo"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("body_arguments,expected", [
    ({"request_body": {}}, {}),
    ({"request_body": {"active": False, "count": 0}}, {"active": False, "count": 0}),
    ({}, None),
    ({"request_body": None}, None),
])
async def test_executor_sends_the_configured_json_body_after_task_serialization(echo_url, body_arguments, expected):
    task = ApiTestTask(task_id="normal-post", method="POST", path=echo_url, **body_arguments)
    # Workers and persisted campaigns use this same DTO JSON round trip.
    restored = ApiTestTask.model_validate(task.model_dump(mode="json"))
    result = await ApiTaskExecutor(credential_manager=CredentialManager()).execute(restored)
    assert result.status == "completed" and result.response_status == 200
    received = result.response_body
    if expected is None:
        assert received == {"raw_body": "", "content_type": None}
    else:
        assert json.loads(received["raw_body"]) == expected
        assert received["content_type"] == "application/json"


@pytest.mark.asyncio
@pytest.mark.parametrize("body_data,expected", [({"body": {}}, {}), ({}, None)])
async def test_fixed_case_compilation_preserves_body_presence_over_real_tcp(echo_url, body_data, expected):
    case, version, run, item = _fixture()
    version.test_data = {}
    version.steps[0].data = {"endpoint": echo_url, "method": "POST", **body_data}
    adapter = CaseExecutionAdapter(tool_resolver=lambda mode: ToolDescriptor(
        key="api-test-runner", name="API Test Runner", description="test",
        category="execution", owner_mode_key=mode,
    ))
    invocation = adapter.build_invocation(case=case, version=version, run=run, item=item)
    task = ApiTestTask.model_validate(invocation.call.arguments["task"])
    result = await ApiTaskExecutor(credential_manager=CredentialManager()).execute(task)
    assert result.status == "completed"
    raw = result.response_body["raw_body"]
    assert (json.loads(raw) if raw else None) == expected
    assert version.steps[0].data == {"endpoint": echo_url, "method": "POST", **body_data}


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinator_kind", ["local", "subagent"])
async def test_dependency_binding_creates_body_only_when_a_value_is_bound(echo_url, coordinator_kind):
    task = ApiTestTask(task_id="normal-bound-post", method="POST", path=echo_url)
    assert task.request_body is None
    credentials = CredentialManager()
    executor = ApiTaskExecutor(credential_manager=credentials)
    if coordinator_kind == "local":
        coordinator = ApiTestCoordinator(
            pool=ApiTaskPool([task]), policy=ExecutionPolicy(), executor_fn=executor.execute,
        )
    else:
        coordinator = ApiSubagentCoordinator(
            pool=ApiTaskPool([task]), policy=ExecutionPolicy(),
            coordinator_runtime_service=None, session_store=InMemorySessionStore(),
            parent_context={}, credential_manager=credentials,
        )
    coordinator._inject_binding_value(task, "request_body.options.enabled", False)
    result = await executor.execute(ApiTestTask.model_validate(task.model_dump(mode="json")))
    assert result.status == "completed"
    assert json.loads(result.response_body["raw_body"]) == {"options": {"enabled": False}}

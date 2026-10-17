"""Real requests must use actual producer values, never stale examples."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, ExecutionPolicy, InputBinding
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore


@contextmanager
def target(output):
    events = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            address = urlsplit(self.path)
            events.append({"method": self.command, "path": address.path, "query": parse_qs(address.query, keep_blank_values=True),
                "body": json.loads(raw) if raw else None, "correlation": self.headers.get("X-Correlation")})
            response = json.dumps(output if address.path == "/producer" else {"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        do_GET = do_POST = respond

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def coordinator(worker, tasks):
    credentials, pool, policy = CredentialManager(), ApiTaskPool(tasks), ExecutionPolicy(max_retries=2)
    if not worker:
        return ApiTestCoordinator(pool=pool, policy=policy, executor_fn=ApiTaskExecutor(credential_manager=credentials).execute), None
    store = InMemorySessionStore()
    launcher = ControlledWorkerLauncher(store)
    return ApiSubagentCoordinator(pool=pool, policy=policy, credential_manager=credentials,
        coordinator_runtime_service=launcher, session_store=store,
        parent_context={"session_id": "owned-parent", "turn_id": "owned-turn"}), launcher


def evidence(tmp_path, name, facts):
    directory = Path(os.getenv("QA_API_REQUIRED_BINDING_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + ".json")
    assert not path.exists(), "preserve previous evidence"
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("destination", ["body.order_id", "query.order_id", "request_headers.X-Correlation"])
def test_missing_actual_output_blocks_only_its_consumers_before_send(tmp_path, worker, destination):
    with target({"data": {"count": 0}}) as (url, events):
        tasks = [ApiTestTask(task_id="produce", method="GET", path=url+"/producer", status="ready"),
            ApiTestTask(task_id="consume", method="POST", path=url+"/consume", status="blocked", execution_mode="write",
                depends_on=["produce"], request_body={"order_id": "stale-example"}, request_query={"order_id": "stale-example"},
                request_headers={"X-Correlation": "stale-example"},
                input_bindings=[InputBinding(source_task_id="produce", source_path="data.id", target_path=destination)]),
            ApiTestTask(task_id="child", method="GET", path=url+"/child", status="blocked", depends_on=["consume"]),
            ApiTestTask(task_id="independent", method="GET", path=url+"/independent", status="ready")]
        runner, launcher = coordinator(worker, tasks)
        result = asyncio.run(runner.run_all())
        count = len(events)
        asyncio.run(runner.run_all())
        report = ReportBuilder().build(campaign=ApiTestCampaign(campaign_id="binding", tasks=result))
        facts = {"events": events, "statuses": [t.status for t in result], "attempts": [t.attempts for t in result],
            "error": result[1].last_error, "report": report.model_dump(mode="json"),
            "worker_dispatches": launcher.task_ids if launcher else [], "terminal_extra_http": len(events)-count, "external_ai_calls": 0}
        evidence(tmp_path, f"{'worker' if worker else 'inline'}-missing-{destination.split('.')[0]}", facts)
        assert facts["statuses"] == ["completed", "failed", "skipped", "completed"], facts
        assert result[1].attempts == 0 and result[1].request_outcome == "not_started"
        assert "produce" in result[1].last_error and "data.id" in result[1].last_error and destination in result[1].last_error
        assert sorted(e["path"] for e in events) == ["/independent", "/producer"]
        assert report.failed == report.skipped == 1 and facts["terminal_extra_http"] == 0
        if launcher:
            assert "consume" not in launcher.task_ids and "child" not in launcher.task_ids


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("aliases", [False, True])
def test_false_zero_null_array_extraction_and_header_binding_reach_actual_request(tmp_path, worker, aliases):
    with target({"data": {"items": [{"id": 0, "enabled": False, "note": None}], "correlation": "observed-value"}}) as (url, events):
        body = "request_body" if aliases else "body"
        query = "request_query" if aliases else "query"
        mappings = [("data.items[0].id", f"{body}.context.id"), ("data.items[0].enabled", f"{body}.enabled"),
                    ("data.items[0].note", f"{body}.note"), ("data.items[0].id", f"{query}.id"),
                    ("data.correlation", "request_headers.X-Correlation")]
        tasks = [ApiTestTask(task_id="produce", method="GET", path=url+"/producer", status="ready"),
            ApiTestTask(task_id="consume", method="POST", path=url+"/consume", status="blocked", depends_on=["produce"],
                execution_mode="write", request_body={"note": "stale-example"},
                input_bindings=[InputBinding(source_task_id="produce", source_path=s, target_path=t) for s, t in mappings])]
        runner, launcher = coordinator(worker, tasks)
        result = asyncio.run(runner.run_all())
        facts = {"events": events, "statuses": [t.status for t in result], "external_ai_calls": 0}
        evidence(tmp_path, f"{'worker' if worker else 'inline'}-values-{int(aliases)}", facts)
        assert facts["statuses"] == ["completed"]*2, facts
        sent = next(e for e in events if e["path"] == "/consume")
        assert sent["body"] == {"context": {"id": 0}, "enabled": False, "note": None}, facts
        assert sent["query"] == {"id": ["0"]} and sent["correlation"] == "observed-value", facts


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("variant", ["fallback", "literal", "present-null"])
def test_declared_fallback_literal_and_present_null_are_distinct(tmp_path, worker, variant):
    with target({"value": None}) as (url, events):
        binding = InputBinding(source_task_id="produce" if variant != "literal" else "",
            source_path="value" if variant == "present-null" else "missing", target_path="body.value", literal_value=0)
        tasks = [ApiTestTask(task_id="produce", method="GET", path=url+"/producer", status="ready"),
            ApiTestTask(task_id="consume", method="POST", path=url+"/consume", status="blocked", depends_on=["produce"],
                execution_mode="write", input_bindings=[binding])]
        runner, _ = coordinator(worker, tasks)
        result = asyncio.run(runner.run_all())
        facts = {"events": events, "statuses": [t.status for t in result], "external_ai_calls": 0}
        evidence(tmp_path, f"{'worker' if worker else 'inline'}-{variant}", facts)
        assert facts["statuses"] == ["completed"]*2
        assert events[-1]["body"] == {"value": None if variant == "present-null" else 0}


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("destination,value", [
    ("request_headers.X-Correlation", None), ("request_headers.X-Correlation", {"id": 1}),
    ("unrecognized.target", "value"), ("path.id", None),
])
def test_invalid_explicit_binding_does_not_dispatch_and_partial_changes_are_not_committed(tmp_path, worker, destination, value):
    with target({"value": value, "valid": 0}) as (url, events):
        item = ApiTestTask(task_id="consume", method="POST", path=url+"/consume", status="blocked", depends_on=["produce"],
            execution_mode="write", request_body={"valid": "original"}, input_bindings=[
                InputBinding(source_task_id="produce", source_path="valid", target_path="body.valid"),
                InputBinding(source_task_id="produce", source_path="value", target_path=destination)])
        tasks = [ApiTestTask(task_id="produce", method="GET", path=url+"/producer", status="ready"), item]
        runner, launcher = coordinator(worker, tasks)
        result = asyncio.run(runner.run_all())
        assert result[1].status == "failed" and result[1].attempts == 0
        assert events == [{"method": "GET", "path": "/producer", "query": {}, "body": None, "correlation": None}]
        assert result[1].request_body == {"valid": "original"} and "value" in result[1].last_error
        if launcher:
            assert len(launcher.task_ids) == 1


@pytest.mark.parametrize("kind", [ApiTestCoordinator, ApiSubagentCoordinator])
def test_missing_connection_field_is_a_planning_gap_and_never_a_transport_retry(kind):
    task = ApiTestTask(task_id="consume", method="POST", path="/consume", status="failed", response_status=503,
        last_error="Required input binding(s) unresolved: producer:data.connection_id -> body.id (source_output_missing)")
    assert not object.__new__(kind)._is_retryable(task)


def test_missing_binding_after_real_process_exit_uses_fixed_producer_without_resending(tmp_path):
    import subprocess
    import sys
    import test_api_dependency_restore_http as recovery
    directory = Path(os.getenv("QA_API_REQUIRED_BINDING_PROCESS_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    snapshot, result = directory/"checkpoint.json", directory/"result.json"
    with target({"data": {"count": 0}}) as (url, events):
        campaign = ApiTestCampaign(campaign_id="missing-after-interrupt", tasks=[
            ApiTestTask(task_id="create", method="GET", path=url+"/producer", status="ready"),
            ApiTestTask(task_id="consume", method="POST", path=url+"/consume", depends_on=["create"],
                status="blocked", execution_mode="write", request_body={"id": "stale-example"},
                input_bindings=[InputBinding(source_task_id="create", source_path="data.id", target_path="body.id")])])
        snapshot.write_text(campaign.model_dump_json(indent=2), encoding="utf8")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        command = [sys.executable, str(Path(recovery.__file__)), str(snapshot), str(result)]
        first = subprocess.run(command+["interrupt"], capture_output=True, text=True, env=env, timeout=30)
        (directory/"first.stdout").write_text(first.stdout, encoding="utf8")
        (directory/"first.stderr").write_text(first.stderr, encoding="utf8")
        assert first.returncode == 73 and len(events) == 1, first.stderr
        original = json.loads(snapshot.read_text(encoding="utf8"))["tasks"][0]
        second = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
        (directory/"second.stdout").write_text(second.stdout, encoding="utf8")
        (directory/"second.stderr").write_text(second.stderr, encoding="utf8")
        assert second.returncode == 0, second.stderr
        saved = json.loads(result.read_text(encoding="utf8"))
        assert saved["campaign"]["tasks"][0] == original
        assert saved["campaign"]["tasks"][1]["status"] == "failed" and saved["campaign"]["tasks"][1]["attempts"] == 0
        assert len(events) == 1 and saved["report"]["failed"] == 1 and saved["external_ai_calls"] == 0
        (directory/"http.json").write_text(json.dumps({"events": events, "first_exit": first.returncode,
            "second_exit": second.returncode, "producer_unchanged": True}, indent=2), encoding="utf8")

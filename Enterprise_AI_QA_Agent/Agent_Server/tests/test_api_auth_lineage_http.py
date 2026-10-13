"""Credentials follow explicit auth dependencies, including two real accounts."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, AssertionSpec, EndpointCandidate, ExecutionPolicy
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.precondition_resolver import PreconditionAnalysis
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.store import InMemorySessionStore


@contextmanager
def accounts():
    tokens = {f"{account}-{stage}": uuid4().hex for account in ("a", "b") for stage in ("login", "refresh")}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def observe(self):
            credential = self.headers.get("Authorization", "")
            label = next((key for key, value in tokens.items() if credential == "Bearer "+value),
                         "absent" if not credential else "unknown")
            requests.append({"path": self.path, "method": self.command, "credential_source": label})
            return label

        def do_POST(self):
            label = self.observe()
            account, operation = self.path.strip("/").split("/")
            if operation == "login" and label == "absent":
                self.reply(200, {"access_token": tokens[account+"-login"]})
            elif operation == "refresh" and label == account+"-login":
                self.reply(200, {"access_token": tokens[account+"-refresh"]})
            else:
                self.reply(401, {"ok": False})

        def do_GET(self):
            label = self.observe()
            account = self.path.strip("/").split("/")[0]
            self.reply(200 if label == account+"-refresh" else 401, {"account": account})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, tokens
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("worker", [False, True])
def test_two_account_refresh_chains_and_deliberate_unauthenticated_request(tmp_path, worker):
    with accounts() as (url, requests, tokens):
        tasks = []
        for account in ("a", "b"):
            for operation, dependencies in [("login", []), ("refresh", [account+"-login"]),
                                            ("profile", [account+"-login", account+"-refresh"])]:
                path = "/"+account+"/"+operation
                tasks.append(ApiTestTask(task_id=account+"-"+operation, method="GET" if operation == "profile" else "POST",
                    path=path, full_url=url+path, execution_mode="read" if operation == "profile" else "auth",
                    depends_on=dependencies, status="blocked" if dependencies else "ready"))
        tasks.append(ApiTestTask(task_id="negative", method="GET", path="/a/profile", full_url=url+"/a/profile",
            status="ready", assertions=[AssertionSpec(kind="status_code", expected=401)]))
        credentials, policy = CredentialManager(), ExecutionPolicy(max_workers=3, max_retries=2)
        if worker:
            store = InMemorySessionStore()
            launcher = ControlledWorkerLauncher(store)
            coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(tasks), policy=policy,
                coordinator_runtime_service=launcher, session_store=store,
                parent_context={"session_id": "parent", "turn_id": "turn"}, credential_manager=credentials)
        else:
            coordinator = ApiTestCoordinator(pool=ApiTaskPool(tasks), policy=policy,
                executor_fn=ApiTaskExecutor(credential_manager=credentials).execute)
        result = asyncio.run(coordinator.run_all())
        count = len(requests)
        before = [task.model_dump(mode="json") for task in result]
        asyncio.run(coordinator.run_all())
        assert before == [task.model_dump(mode="json") for task in result] and len(requests) == count
        campaign = ApiTestCampaign(campaign_id=str(uuid4()), project_id=str(uuid4()), tasks=result)
        report = ReportBuilder().build(campaign=campaign)
        facts = {"requests": requests, "tasks": [{"task_id": t.task_id, "status": t.status,
            "response_status": t.response_status, "attempts": t.attempts} for t in result],
            "report": report.model_dump(mode="json"), "external_ai_calls": 0,
            "terminal_extra_http": len(requests)-count, "worker": worker}
        directory = Path(os.getenv("QA_API_AUTH_LINEAGE_DIR", str(tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(facts, ensure_ascii=False, indent=2)
        assert all(value not in raw for value in tokens.values())
        (directory/("worker.json" if worker else "inline.json")).write_text(raw, encoding="utf8")
        assert all(t.status == "completed" for t in result), facts
        assert len(requests) == 7, facts
        for account in ("a", "b"):
            assert {item["path"]: item["credential_source"] for item in requests if item["path"].startswith("/"+account)}[
                "/"+account+"/login"] == "absent"
            assert next(item for item in requests if item["path"] == "/"+account+"/refresh")["credential_source"] == account+"-login"
            positive = next(t for t in result if t.task_id == account+"-profile")
            refresh = next(t for t in result if t.task_id == account+"-refresh")
            assert positive.auth_ref == refresh.auth_ref
        assert result[-1].response_status == 401 and result[-1].auth_ref == "" and result[-1].attempts == 1
        assert report.completed == 7 and report.failed == 0


@pytest.mark.parametrize("coordinator_type", [ApiTestCoordinator, ApiSubagentCoordinator])
def test_ambiguous_and_independent_auth_keep_configured_credential(coordinator_type, caplog):
    a = ApiTestTask(task_id="a", method="POST", path="/a/login", execution_mode="auth", status="completed", auth_ref="a-ref")
    b = ApiTestTask(task_id="b", method="POST", path="/b/login", execution_mode="auth", status="ready", auth_ref="b-ref")
    target = ApiTestTask(task_id="target", method="GET", path="/profile", depends_on=["a", "b"], auth_ref="chosen-ref")
    running = ApiTestTask(task_id="running", method="GET", path="/profile", status="running", depends_on=["a"], auth_ref="original-ref")
    pool = ApiTaskPool([a, b, target, running])
    coordinator = object.__new__(coordinator_type)
    coordinator._pool = pool
    before = {t.task_id: t.model_dump(mode="json") for t in pool.all_tasks}
    coordinator._propagate_auth_ref("a-ref", source_task_id="a")
    assert {t.task_id: t.model_dump(mode="json") for t in pool.all_tasks} == before
    assert any(record.message == "api_auth_source_ambiguous" for record in caplog.records)


def test_legacy_single_account_frozen_graph_keeps_original_edges_and_works_over_http(tmp_path, caplog):
    with accounts() as (url, requests, tokens):
        tasks = [ApiTestTask(task_id="login", method="POST", path="/a/login", full_url=url+"/a/login",
                            execution_mode="auth", capability="login", status="ready"),
                 ApiTestTask(task_id="refresh", method="POST", path="/a/refresh", full_url=url+"/a/refresh",
                            execution_mode="auth", capability="refresh_token", status="ready"),
                 ApiTestTask(task_id="profile", method="GET", path="/a/profile", full_url=url+"/a/profile",
                            depends_on=["login", "refresh"], status="blocked")]
        saved = ApiTestCampaign(campaign_id="legacy-auth", tasks=tasks).model_dump_json()
        restored = ApiTestCampaign.model_validate_json(saved)
        edges = {t.task_id: list(t.depends_on) for t in restored.tasks}
        coordinator = ApiTestCoordinator(pool=ApiTaskPool(restored.tasks), policy=ExecutionPolicy(),
            executor_fn=ApiTaskExecutor(credential_manager=CredentialManager()).execute)
        result = asyncio.run(coordinator.run_all())
        facts = {"requests": requests, "statuses": [t.status for t in result],
            "frozen_dependencies_unchanged": edges == {t.task_id: t.depends_on for t in result}, "external_ai_calls": 0}
        assert all(t.status == "completed" for t in result), facts
        assert facts["frozen_dependencies_unchanged"] and len(requests) == 3
        assert [item["credential_source"] for item in requests] == ["absent", "a-login", "a-refresh"]
        raw = json.dumps(facts, indent=2)
        assert all(value not in raw for value in tokens.values())
        directory = Path(os.getenv("QA_API_AUTH_LINEAGE_DIR", str(tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        (directory/"legacy.json").write_text(raw, encoding="utf8")


@pytest.mark.parametrize("variant", ["unique", "ambiguous", "other-origin"])
def test_planner_adds_only_a_unique_same_origin_login_source(variant):
    endpoints = [EndpointCandidate(endpoint_id="refresh", method="POST", path="/auth/refresh",
                    full_url="http://127.0.0.1:8000/auth/refresh"),
                 EndpointCandidate(endpoint_id="login", method="POST", path="/login",
                    full_url="http://127.0.0.1:8000/login")]
    if variant == "ambiguous":
        endpoints.append(EndpointCandidate(endpoint_id="other-login", method="POST", path="/other/login",
                                          full_url="http://127.0.0.1:8000/other/login"))
    if variant == "other-origin":
        endpoints[1].full_url = "http://127.0.0.1:8001/login"
    graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many(endpoints),
        preconditions=PreconditionAnalysis())
    refresh, login = graph.tasks[:2]
    assert refresh.depends_on == ([login.task_id] if variant == "unique" else [])
    assert refresh.status == ("blocked" if variant == "unique" else "ready")

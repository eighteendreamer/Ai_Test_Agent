"""Full production API lifespan and TestRun CLI, with real isolated storage.

A local HTTP server supplies the controlled model provider and stateful business
target. No production AppContainer provider is replaced.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from threading import Thread
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from test_agent_case_assessment import receipt
from test_case_planning import finish, item
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.core.config import get_settings
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection, postgres_engine
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.channel_config import ChannelAdvancedSettings
from src.schemas.model_config import ModelCapabilitiesOverride, ModelTransportOptions
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.settings import GeneralSettingsPayload, ModelConfigUpdateRequest

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_EXECUTION_CLI_TESTS") != "1",
    reason="requires isolated PostgreSQL database, MySQL, RustFS and explicitly configured Redis")
SERVER_ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def child_environment(settings):
    fields = settings.model_dump(mode="json")
    values = {key: value for key, value in os.environ.items() if key.split("__")[0].lower() not in fields}
    for key, value in fields.items():
        if key == "api_auth_token":
            value = settings.api_auth_token.get_secret_value() if settings.api_auth_token else ""
        values[key.upper()] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)
    values["PYTHONPATH"] = str(SERVER_ROOT)
    return values


class Processes:
    def __init__(self, directory, settings):
        self.directory, self.env, self.children = directory, child_environment(settings), []

    def start(self, name, *args):
        out = (self.directory / (name + ".stdout")).open("wb")
        err = (self.directory / (name + ".stderr")).open("wb")
        child = subprocess.Popen([sys.executable, "-X", "utf8", *args], cwd=SERVER_ROOT,
            env=self.env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        self.children.append((child, out, err))
        return child

    async def wait(self, child, timeout=60):
        try:
            return await asyncio.to_thread(child.wait, timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            await asyncio.to_thread(child.wait, timeout=10)
            raise

    async def stop(self, child):
        if child.poll() is None:
            child.terminate()
        await self.wait(child, timeout=10)

    async def close(self):
        for child, out, err in reversed(self.children):
            await self.stop(child)
            out.close()
            err.close()


@asynccontextmanager
async def isolated_runtime(
    tmp_path,
    *,
    redis_url: str | None = None,
    seed_workspace_config: bool = True,
):
    # Require an explicit test service before allocating databases or processes.
    redis_url = (redis_url or os.getenv("QA_ISOLATED_RUNTIME_REDIS_URL") or "").strip()
    if not redis_url:
        pytest.fail("Set QA_ISOLATED_RUNTIME_REDIS_URL or pass redis_url with an isolated test Redis database; "
            "this fixture does not start containers", pytrace=False)
    settings = get_settings().model_copy(deep=True)
    base = settings.model_copy(deep=True)
    suffix = uuid4().hex[:16]
    database = "up_execution_cli_" + suffix
    assert re.fullmatch(r"up_execution_cli_[0-9a-f]{16}", database)
    settings.database.postgres_database = database
    settings.data_dir = str(tmp_path / "data")
    settings.storage.artifact_root_dir = str(tmp_path / "artifacts")
    settings.api_auth_token = None
    settings.app_env = "development"
    settings.langsmith.enabled = False
    settings.orchestration.redis_task_dispatch_enabled = True
    settings.orchestration.redis_vector_enabled = False
    settings.orchestration.intent_semantic_classifier_enabled = False
    settings.orchestration.redis_task_block_ms = 100
    settings.orchestration.redis_task_reclaim_idle_ms = 1000
    settings.orchestration.redis_task_outbox_streams = ""
    settings.database.redis_url = redis_url
    settings.orchestration.redis_task_stream = f"qa:test:{suffix}:runs"
    settings.orchestration.agent_task_stream = f"qa:test:{suffix}:agents"
    settings.orchestration.redis_task_consumer_group = f"qa:test:{suffix}:workers"
    config_tables = []
    for field in ("llm_model_table", "email_config_table", "channel_config_table", "sponsor_config_table", "application_config_table"):
        name = "up_cli_" + field.removesuffix("_table") + "_" + suffix
        setattr(settings.database, field, name)
        config_tables.append(name)
    (tmp_path / "data").mkdir()
    created_database, isolated_configs = False, False
    queue = None
    processes = Processes(tmp_path, settings)
    objects = ArtifactStorageService(settings)
    owned_objects = []
    try:
        # Full lifespan contains hardcoded smoke tables. A unique DB isolates
        # all tables, rather than merely renaming selected settings fields.
        # PostgreSQL CREATE DATABASE requires autocommit; SQLAlchemy documents
        # execution_options(isolation_level="AUTOCOMMIT") on a connection.
        with postgres_engine(base).connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            assert conn.exec_driver_sql("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).first() is None
            conn.exec_driver_sql(f'CREATE DATABASE "{database}" TEMPLATE template0')
            created_database = True
        with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
            for name in config_tables:
                cur.execute("SHOW TABLES LIKE %s", (name,))
                assert cur.fetchone() is None
        isolated_configs = True
        config = MySQLApplicationConfigStore(settings)
        config.initialize()
        if seed_workspace_config:
            config.save("workspace.general", GeneralSettingsPayload().model_dump(mode="json"))
            config.save("channel.advanced", ChannelAdvancedSettings().model_dump(mode="json"))
        processes.env = child_environment(settings)
        probe = Redis.from_url(settings.database.redis_url)
        try:
            for attempt in range(50):
                try:
                    if await probe.ping():
                        break
                except (OSError, RedisConnectionError):
                    if attempt == 49:
                        raise
                    await asyncio.sleep(.1)
        finally:
            await probe.aclose()
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.redis_task_stream,
            group=settings.orchestration.redis_task_consumer_group)
        await queue.connect()
        yield settings, processes, queue, objects, owned_objects
    finally:
        await processes.close()
        if created_database:
            # Preserve actual failed state too, before this owned database is removed.
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                original_tables = {}
                for field in ("postgres_snapshot_table", "postgres_tool_job_table", "postgres_approval_table",
                    "postgres_test_run_table", "postgres_test_run_item_table", "postgres_test_run_attempt_table",
                    "postgres_test_case_result_table"):
                    table = getattr(settings.database, field)
                    cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                    if cur.fetchone()["existing"]:
                        cur.execute(f"SELECT * FROM {table}")
                        original_tables[field] = [dict(row) for row in cur.fetchall()]
            (tmp_path / "postgres-finally.json").write_text(json.dumps(original_tables,
                ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        if queue is not None:
            await queue.close()
        for uri in owned_objects:
            await objects.delete_object_uri(uri)
        await objects.close()
        shared_redis = Redis.from_url(settings.database.redis_url)
        try:
            owned_keys = {
                settings.orchestration.redis_task_stream,
                f"{settings.orchestration.redis_task_stream}:dead_letter",
                settings.orchestration.agent_task_stream,
                f"{settings.orchestration.agent_task_stream}:dead_letter",
            }
            await shared_redis.delete(*owned_keys)
        finally:
            await shared_redis.aclose()
        if isolated_configs:
            with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                for name in reversed(config_tables):
                    cur.execute(f"DROP TABLE IF EXISTS `{name}`")
        if created_database:
            postgres_engine(settings).dispose()
            with postgres_engine(base).connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                # Only this fixture's freshly created database is removable.
                owner = conn.exec_driver_sql("SELECT pg_get_userbyid(datdba)=current_user FROM pg_database WHERE datname=%s", (database,)).scalar()
                assert owner is True
                assert conn.exec_driver_sql("SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (database,)).scalar() == 0
                conn.exec_driver_sql(f'DROP DATABASE "{database}"')
                assert conn.exec_driver_sql("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).first() is None
        output = os.getenv("QA_TEST_GATE_OUTPUT_DIR")
        if output:
            evidence = Path(output) / "p2-execution-worker-cli-probe"
            evidence.mkdir(exist_ok=True)
            for path in tmp_path.iterdir():
                if (path.suffix in {".stdout", ".stderr"} or path.name in
                    {"waiting.json", "requirements.md", "postgres-execution.json", "postgres-finally.json", "completed.json"}
                    or path.name.startswith(("model-", "receipt-"))):
                    shutil.copyfile(path, evidence / path.name)


@asynccontextmanager
async def controlled_http(tmp_path, *, mixed_report=False, correction_gate=None):
    calls, effects, orders, errors = [], [], {}, []
    actual_id = "actual-" + uuid4().hex
    body = {"order.id": actual_id, "flag": False, "count": 0, "nullable": None,
        "message": "订单处于待支付状态，尚未进入出库流程"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path == "/orders":
                effects.append(("POST", self.path))
                orders[actual_id] = body
                self.reply(201, body)
                return
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                messages = request["messages"]
                system = "\n".join(str(m.get("content", "")) for m in messages if m["role"] == "system")
                assert "DO-NOT-INJECT-TEMPLATE" not in json.dumps(request)
                if "Review this completed Agent execution against its complete frozen cases" in system:
                    assert not request.get("tools")
                    data = receipt()
                    for field in ("flag", "count", "nullable"):
                        data["steps"][1]["checks"].append({"name": field, "expected_path_tokens": ["期望", field],
                            "operator": "equals", "observations": [{"call_id": "target",
                                "output_path_tokens": ["task_result", "response_body", field]}]})
                    result, reason, phase = {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}, "stop", "receipt_correction"
                elif "Complete Frozen Case and Preparation Data" in system:
                    match = re.search(r'<untrusted_content[^>]*>\s*(\{.*?\})\s*</untrusted_content>',
                        system[system.index("Complete Frozen Case and Preparation Data"):], re.S)
                    frozen = json.loads(unescape(match.group(1)))
                    previous = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
                    index = len(previous)
                    if index < 3:
                        step = frozen["steps"][index]
                        source = step["content_json"]["steps"][0] if step["phase"] == "case" else step["item"]["instructions"]
                        url = source["url"]
                        if "{id}" in url:
                            url = url.replace("{id}", previous[0]["output"]["task_result"]["response_body"]["order.id"])
                        result = {"role": "assistant", "content": "执行固定用例下一步", "tool_calls": [{"id": step["item"]["key"],
                            "type": "function", "function": {"name": "api-test-runner", "arguments": json.dumps({
                                "worker_action": "execute_task", "task": {"task_id": step["item"]["key"],
                                    "method": source["method"], "path": url, "full_url": url, "timeout_seconds": 5}})}}]}
                        reason, phase = "tool_calls", "agent_operation"
                    else:
                        data = receipt()
                        for field in ("flag", "count", "nullable"):
                            data["steps"][1]["checks"].append({"name": field, "expected_path_tokens": ["期望", field],
                                "operator": "equals", "observations": [{"call_id": "target",
                                    "output_path_tokens": ["task_result", "response_body", field]}]})
                        result, reason, phase = {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}, "stop", "receipt"
                        if mixed_report:
                            old = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-tool-evidence-production-final/trusted-parent/postgres-execution.json"
                            result["content"] = json.loads(old.read_bytes())["tables"]["postgres_snapshot_table"]["records"][-1]["graph_state"]["model_response_text"]
                else:
                    context = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
                    target = item(context, "query", key="target", depends_on=["seed"],
                        input_bindings={"id": {"source_item": "seed", "output_path_tokens": ["body", "order.id"]}})
                    refs = target["evidence_ids"]
                    data = finish({"key": "seed", "kind": "prepare", "reason": "创建查询所需订单", "evidence_ids": refs,
                        "instructions": {"method": "POST", "url": base_url + "/orders", "expected": 201}}, target,
                        {"key": "cleanup", "kind": "prepare", "reason": "清理本次订单", "evidence_ids": refs,
                            "depends_on": ["target"], "input_bindings": target["input_bindings"],
                            "instructions": {"method": "DELETE", "url": base_url + "/orders/{id}", "expected": 200}})
                    result, reason, phase = {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}, "stop", "planning"
                calls.append({"path": self.path, "phase": phase, "tool_message_count": len([m for m in messages if m["role"] == "tool"])})
                (tmp_path / f"model-{len(calls):03d}-request.json").write_text(
                    json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
                # A real HTTP request has arrived. Process-interruption tests
                # can hold its reply without replacing any production service.
                if phase == "receipt_correction" and correction_gate is not None:
                    if not correction_gate(request):
                        self.close_connection = True
                        return
                identity = "response-" + uuid4().hex
                if request.get("stream"):
                    delta = dict(result)
                    for index, call in enumerate(delta.get("tool_calls", [])):
                        call["index"] = index
                    chunk = {"id": identity, "object": "chat.completion.chunk", "created": 1, "model": "controlled-cli",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                    raw = ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                else:
                    self.reply(200, {"id": identity, "object": "chat.completion", "created": 1,
                        "model": "controlled-cli", "choices": [{"index": 0, "message": result, "finish_reason": reason}]})
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                self.reply(500, {"error": {"message": errors[-1], "type": "fixture_error"}})

        def do_GET(self):
            effects.append(("GET", self.path))
            self.reply(200 if self.path == "/orders/" + actual_id and actual_id in orders else 404, body)

        def do_DELETE(self):
            effects.append(("DELETE", self.path))
            valid = self.path == "/orders/" + actual_id and actual_id in orders
            if valid:
                orders.pop(actual_id)
            self.reply(200 if valid else 404, body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base_url, calls, effects, orders, actual_id, errors
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        (tmp_path / "http.json").write_text(json.dumps({"calls": calls, "effects": effects, "errors": errors},
            ensure_ascii=False, indent=2), encoding="utf-8")
        output = os.getenv("QA_TEST_GATE_OUTPUT_DIR")
        if output:
            evidence = Path(output) / "p2-execution-worker-cli-probe"
            evidence.mkdir(exist_ok=True)
            shutil.copyfile(tmp_path / "http.json", evidence / "http.json")


async def wait_api(client, process):
    deadline = monotonic() + 50
    while monotonic() < deadline:
        assert process.poll() is None, "production API exited; see preserved stderr"
        try:
            response = await client.get("/health/live")
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(.1)
    raise AssertionError("production API did not finish lifespan; see preserved stderr")


def controlled_case_content(business):
    """Shared fixed case matching this provider's literal receipt references."""
    return {"objective": "查询已有订单", "期望": {"code": 200, "flag": False, "count": 0, "nullable": None},
        "steps": [{"action": "查询并核对完整响应", "method": "GET", "url": business + "/orders/{id}"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("model_limit,mixed_report", [(8, False), (3, False), (8, True)],
    ids=["complete", "budget-exhausted", "receipt-correction"])
async def test_production_api_and_execution_cli_preserve_approval_then_complete_and_ignore_terminal_replay(tmp_path, model_limit, mixed_report, monkeypatch):
    if os.getenv("QA_APPROVAL_MODEL_BUDGET_CLI_DIR"):
        target = Path(os.environ["QA_APPROVAL_MODEL_BUDGET_CLI_DIR"]) / (str(model_limit) + ("-receipt" if mixed_report else ""))
        target.mkdir(parents=True, exist_ok=False)
        monkeypatch.setenv("QA_TEST_GATE_OUTPUT_DIR", str(target))
    async with controlled_http(tmp_path, mixed_report=mixed_report) as (business, calls, effects, orders, actual_id, errors):
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned):
            settings.orchestration.runtime_max_iterations = model_limit
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(model_name="controlled-cli", provider="openai", transport="openai_chat_completions",
                base_url=business + "/v1", api_key=uuid4().hex, is_active=True, use_provider_defaults=False,
                capability_overrides=ModelCapabilitiesOverride(streaming=False), max_output_tokens=4096))
            processes.env = child_environment(settings)
            port = free_port()
            args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("api-first", *args)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                await wait_api(client, api)

                async def post(path, payload=None, status=200):
                    response = await client.post(path, json=payload)
                    assert response.status_code == status, (response.status_code, response.text, errors)
                    return response.json()

                project = await post("/projects", {"project_key": "cli-" + uuid4().hex, "name": "实际 CLI 生命周期", "base_url": business}, 201)
                if mixed_report:
                    saved_policy = await client.put(f"/projects/{project['id']}/case-plans/policy",
                        json=CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1",
                            receipt_correction_transport_options=ModelTransportOptions(timeout_seconds=60, max_retries=0)).model_dump(mode="json"))
                    assert saved_policy.status_code == 200 and saved_policy.json()["policy"]["receipt_correction_protocol"] == "case-receipt-correction-v1"
                source = tmp_path / "requirements.md"
                source.write_text(f"# 订单查询\nPOST {business}/orders 创建订单返回201和order.id。\n"
                    f"GET {business}/orders/{{id}} 查询已有订单返回200，flag=false、count=0、nullable=null。\n"
                    f"DELETE {business}/orders/{{id}} 清理本次订单返回200。\n", encoding="utf-8")
                document = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": source.name,
                    "content_base64": base64.b64encode(source.read_bytes()).decode()})
                owned.append(document["storage_uri"])
                assert (await objects.read_object_uri(document["storage_uri"]))["content"] == source.read_bytes()
                content = controlled_case_content(business)
                bundle = await post(f"/projects/{project['id']}/test-cases", {"case_key": "query", "title": "查询已有订单",
                    "mode_key": "api_testing", "case_type": "json", "content_json": content,
                    "execution_notes": "执行完整 JSON，创建的订单在查询后清理。",
                    "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}], "model_key": "manual",
                    "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"},
                    "generation_metadata": {"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}}, 201)
                case_id = bundle["case"]["id"]
                await post(f"/test-cases/{case_id}/submit-review")
                await post(f"/test-cases/{case_id}/activate", {})
                plan = await post(f"/projects/{project['id']}/case-plans", {"objective": "验证已有订单详情", "model_key": model.key,
                    "candidate_case_ids": [case_id]}, 201)
                assert plan["status"] == "planned", plan
                path = f"/projects/{project['id']}/case-plans/{plan['id']}/runs"
                payload = {"creation_key": "cli-launch", "mode_key": "api_testing"}
                detail = await post(path, payload, 201)
                run_id, item_id = detail["run"]["id"], detail["items"][0]["id"]
                assert not effects
                assert await PostgresTaskOutbox(settings).relay_once(queue) == 1
                child = processes.start("worker-first", "-m", "src.cli.run_redis_worker", "--once", "--worker-id", "cli-first")
                assert await processes.wait(child) == 0, "actual Worker failed; see preserved stdout/stderr"
                response = await client.get("/runs/" + run_id)
                assert response.status_code == 200
                waiting = response.json()
                (tmp_path / "waiting.json").write_text(json.dumps(waiting, ensure_ascii=False, indent=2), encoding="utf-8")
                assert waiting["items"][0]["status"] == "waiting_approval", waiting
                assert not effects
                assert len(calls) == 2, calls
                await processes.stop(api)
                settings.orchestration.runtime_max_iterations = model_limit + 10
                processes.env = child_environment(settings)
                api = processes.start("api-restarted", *args)
                await wait_api(client, api)
                assert (await client.get("/runs/" + run_id)).json() == waiting
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT payload,published_at FROM {settings.database.postgres_task_outbox_table}")
                    delivery = cur.fetchall()
                assert len(delivery) == 1 and delivery[0]["published_at"] is not None
                # Identity replay after both full-lifespan processes have stopped.
                assert (await post(path, payload, 201))["run"]["id"] == run_id
                for expected in range(3):
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT id FROM {settings.database.postgres_approval_table} WHERE status='pending'")
                        pending = cur.fetchall()
                    assert len(pending) == 1, pending
                    result = await post("/run-items/" + item_id + "/approval", {"approval_id": str(pending[0]["id"]), "decision": "approved"})
                    assert len(effects) == expected + 1, (result, effects, errors)
                    if expected == 0:
                        assert result["status"] == "waiting_approval" and actual_id in orders
                        prepared = (await client.get("/runs/" + run_id)).json()
                        before_restart = len(calls)
                        await processes.stop(api)
                        api = processes.start("api-after-create", *args)
                        await wait_api(client, api)
                        assert (await client.get("/runs/" + run_id)).json() == prepared
                        assert len(calls) == before_restart and len(effects) == 1
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT id FROM {settings.database.postgres_approval_table} WHERE status='pending'")
                            saved_approval = cur.fetchall()
                        assert [str(row["id"]) for row in saved_approval] == [result["approval_id"]]
                assert result["status"] == ("passed" if model_limit == 8 else "error"), result
                if model_limit == 8:
                    assert result["actual"]["case_output"]["body"]["order.id"] == actual_id
                else:
                    assert "budget_exhausted" in result["error_message"]
                assert not orders
                if mixed_report:
                    correction = result["actual"]["receipt_correction"]
                    assert correction["phase"] == "response" and correction["total_model_calls"] == 5
                    assert correction["runtime_parameters"]["transport_options"] == {"timeout_seconds": 60, "max_retries": 0}
                    for key in ("request", "response"):
                        owned.append(correction[key]["uri"])
                        raw = (await objects.read_object_uri(correction[key]["uri"]))["content"]
                        assert len(raw) == correction[key]["size_bytes"]
                        (tmp_path / f"receipt-{key}.json").write_bytes(raw)
                completed = (await client.get("/runs/" + run_id)).json()
                (tmp_path / "completed.json").write_text(json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8")
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    tables = {}
                    for field in ("postgres_snapshot_table", "postgres_tool_job_table", "postgres_approval_table"):
                        cur.execute(f"SELECT * FROM {getattr(settings.database, field)}")
                        tables[field] = [dict(row) for row in cur.fetchall()]
                (tmp_path / "postgres-execution.json").write_text(json.dumps(tables,
                    ensure_ascii=False, default=str, indent=2), encoding="utf-8")
                final_states = [row["graph_state"] for row in tables["postgres_snapshot_table"]
                    if row["stage"] == "completed"]
                assert len(final_states) == 1
                assert final_states[0]["model_call_count"] == min(model_limit, 4)
                assert final_states[0]["max_iterations"] == model_limit
                assert completed["run"]["status"] == "completed" and completed["results"][0]["id"] == result["id"]
                assert [verb for verb, _ in effects] == ["POST", "GET", "DELETE"]
                expected_calls = 1 + min(model_limit, 4) + int(mixed_report)
                assert len(calls) == expected_calls and not errors, (calls, errors)
                await queue.enqueue(delivery[0]["payload"])
                child = processes.start("worker-terminal", "-m", "src.cli.run_redis_worker", "--once", "--worker-id", "cli-terminal")
                assert await processes.wait(child) == 0
                assert not await queue.pending()
                assert (await client.get("/runs/" + run_id)).json() == completed
                assert len(calls) == expected_calls and len(effects) == 3
                evidence = {"scope": "production API lifespan and execution Worker CLI; local controlled model, real MySQL/PostgreSQL/RustFS/Redis",
                    "case_version_id": bundle["version"]["id"], "plan_id": plan["id"], "run_id": run_id, "result_id": result["id"],
                    "approval_survived_api_and_worker_restart": True, "confirmed_create_survived_api_restart": True,
                    "actual_effects": effects,
                    "original_turn_limit": model_limit, "restarted_process_default": model_limit + 10,
                    "execution_model_calls": min(model_limit, 4), "formal_status": result["status"],
                    "receipt_correction_calls": int(mixed_report),
                    "receipt_transport_options": correction["runtime_parameters"].get("transport_options") if mixed_report else None,
                    "terminal_replay_model_calls": 0,
                    "model_calls": calls, "terminal_replay_effects": 0, "business_records_remaining": len(orders)}
                target = Path(os.getenv("QA_TEST_GATE_OUTPUT_DIR", str(tmp_path))) / "p2-execution-worker-cli.json"
                target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

"""Default model, physical recording and existing formal UI Agent execution."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from time import monotonic
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright
import pytest

from test_execution_worker_cli import isolated_runtime, free_port, child_environment, wait_api
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from src.schemas.case_planning import CasePlanRecord
from src.registry.agents import AgentRegistry
from src.domain.models import SessionRecord
from src.runtime.postgres_session_store import PostgresSessionStore
from src.schemas.session import SessionStatus, SessionMode, RuntimeMode

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_AGENT_PRODUCTION") != "1",
    reason="requires default provider, real Chromium, MySQL/PG/RustFS/Memgraph/Redis")


def save(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_diagnostic_artifacts(tmp_path: Path, output: Path) -> list[dict[str, str]]:
    """Copy best-effort diagnostics without turning a primary failure into a copy error."""
    failures: list[dict[str, str]] = []
    try:
        entries = list(tmp_path.iterdir())
    except OSError as exc:
        failures.append({"path": str(tmp_path), "error_type": type(exc).__name__, "message": str(exc)})
        entries = []
    for path in entries:
        if path.suffix not in {".stdout", ".stderr"} and path.name != "docker.json":
            continue
        try:
            if path.exists():
                shutil.copyfile(path, output / path.name)
        except OSError as exc:
            failures.append({"path": str(path), "error_type": type(exc).__name__, "message": str(exc)})
    artifacts = tmp_path / "artifacts"
    try:
        if artifacts.exists():
            shutil.copytree(artifacts, output / "artifacts", dirs_exist_ok=True)
    except OSError as exc:
        failures.append({"path": str(artifacts), "error_type": type(exc).__name__, "message": str(exc)})
    if failures:
        # This file is deliberately separate from failure.json: it must never
        # replace the exception raised by the actual test or worker.
        try:
            save(output, "diagnostic-copy-failures.json", {"failures": failures})
        except OSError:
            pass
    return failures


def verify_owned_approval(approval, *, project_id, run_id, item_id, origin):
    assert approval["tool_key"] in AgentRegistry().get("ui-automation-agent").supported_tools
    metadata = approval["metadata"]
    assert (metadata["project_id"], metadata["run_id"], metadata["run_item_id"]) == (project_id, run_id, item_id)
    target = metadata["arguments"].get("target_url")
    if target:
        assert urlsplit(target).netloc == urlsplit(origin).netloc


@asynccontextmanager
async def evidence_runtime(tmp_path, output):
    async with isolated_runtime(tmp_path) as runtime:
        try:
            yield runtime
        finally:
            settings = runtime[0]
            captured = {"scope": "isolated PostgreSQL before fixture shutdown", "tables": {}}
            with postgres_connect(settings) as conn, conn.cursor() as cursor:
                for field in ("postgres_tool_job_table", "postgres_snapshot_table", "postgres_approval_table", "postgres_event_table"):
                    table = getattr(settings.database, field)
                    cursor.execute("SELECT to_regclass(%s) AS table_name", (table,))
                    if cursor.fetchone()["table_name"] is None:
                        captured["tables"][field] = {"exists": False}
                        continue
                    cursor.execute(f"SELECT to_jsonb(t) AS record FROM {table} t")
                    captured["tables"][field] = {"exists": True,
                        "records": [row["record"] for row in cursor.fetchall()]}
            save(output, "postgres-execution.json", captured)


@asynccontextmanager
async def observed_servers(directory, model):
    sends, effects = [], []
    page = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>录制知识实际执行</title>
<main><h1>详情展示验证</h1><button id="open-details">展开详情</button><section id="details" hidden></section></main>
<script>document.getElementById('open-details').onclick=async()=>{
 const response=await fetch('/activate',{method:'POST'});const data=await response.json();
 const node=document.getElementById('details');node.hidden=false;node.textContent='已展开 '+data.confirmation;
};</script></html>"""
    (directory / "page.html").write_text(page, encoding="utf-8")

    class PageHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            raw = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            assert self.path == "/activate"
            effect = {"path": self.path, "confirmation": "DOM-" + uuid4().hex}
            effects.append(effect)
            save(directory, "page-effects.json", effects)
            raw = json.dumps(effect).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            index = len(sends) + 1
            request = json.loads(raw)
            save(directory, f"model-{index:03d}-request.json", request)
            (directory / f"model-{index:03d}-request.bin").write_bytes(raw)
            entry = {"index": index, "request_sha256": hashlib.sha256(raw).hexdigest(),
                "path": self.path, "status": "sending"}
            sends.append(entry)
            save(directory, "model-sends.json", sends)
            started = monotonic()
            try:
                assert self.path.startswith("/v1/")
                url = model.api_base_url.rstrip("/") + "/" + self.path[len("/v1/"):]
                # Headers remain in memory; never include credentials in saved evidence.
                headers = {"Authorization": self.headers.get("Authorization", ""),
                    "Content-Type": "application/json", **model.extra_headers}
                payload = bytearray()
                with httpx.Client(timeout=120) as upstream, upstream.stream("POST", url, content=raw, headers=headers) as response:
                    entry["status"] = response.status_code
                    self.send_response(response.status_code)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    if response.headers.get("Content-Encoding"):
                        self.send_header("Content-Encoding", response.headers["Content-Encoding"])
                    self.send_header("Connection", "close")
                    self.end_headers()
                    # Forward SSE as it arrives. Buffering until completion
                    # would change the SDK's read-timeout behavior.
                    for chunk in response.iter_raw():
                        payload.extend(chunk)
                        if not entry.get("client_disconnected"):
                            try:
                                self.wfile.write(chunk)
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                entry["client_disconnected"] = True
                (directory / f"model-{index:03d}-response.bin").write_bytes(payload)
                entry.update(response_sha256=hashlib.sha256(payload).hexdigest(),
                    response_bytes=len(payload), seconds=monotonic() - started)
            except Exception as exc:
                entry.update(error_type=type(exc).__name__, seconds=monotonic() - started)
                # Provider/connection failures remain actual failures, not synthetic model replies.
                self.close_connection = True
            finally:
                save(directory, "model-sends.json", sends)

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), handler) for handler in (PageHandler, ModelHandler)]
    for server in servers:
        server.daemon_threads = True
    threads = [Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    try:
        yield f"http://127.0.0.1:{servers[0].server_port}", f"http://127.0.0.1:{servers[1].server_port}/v1", sends, effects
    finally:
        for server, thread in zip(servers, threads):
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            await asyncio.to_thread(thread.join, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted_parent", [True, False], ids=["trusted-parent", "untrusted-target"])
async def test_default_model_uses_actual_recording_in_formal_ui_execution(tmp_path, trusted_parent):
    output = Path(os.environ["QA_RECORDING_AGENT_EVIDENCE_DIR"]) / (
        "trusted-parent" if trusted_parent else "untrusted-target")
    output.mkdir(parents=True, exist_ok=False)
    source_model = MySQLModelConfigStore(get_settings()).get_default_active()
    assert source_model.auth_type == "api_key" and source_model.transport == "openai_chat_completions"
    save(output, "default-model.json", model_invocation_parameters(source_model))
    recording_id = None
    project_id = None
    try:
        async with observed_servers(output, source_model) as (origin, proxy, sends, effects):
            async with evidence_runtime(tmp_path, output) as (settings, processes, queue, objects, owned):
                models = MySQLModelConfigStore(settings)
                models.initialize()
                copied = models.upsert(ModelConfigUpdateRequest(model_name=source_model.model_id,
                    provider=source_model.provider, transport=source_model.transport, base_url=proxy,
                    api_key=source_model.api_key, auth_type=source_model.auth_type, is_active=True,
                    capability_overrides=source_model.capability_overrides,
                    context_window=source_model.context_window, max_output_tokens=source_model.max_output_tokens))
                actual_model = models.get_default_active()
                assert actual_model.model_id == source_model.model_id
                assert actual_model.capabilities == source_model.capabilities
                assert actual_model.max_output_tokens == source_model.max_output_tokens
                processes.env = child_environment(settings)
                api = processes.start("api-recording-agent", "-m", "uvicorn", "src.main:app",
                    "--host", "127.0.0.1", "--port", str(port := free_port()))
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=650) as client:
                    await wait_api(client, api)

                    async def post(path, payload=None, status=200):
                        response = await client.post(path, json=payload)
                        save(output, f"http-{uuid4().hex[:8]}.json", {"path": path,
                            "status": response.status_code, "body": response.json()})
                        assert response.status_code == status, (path, response.status_code, response.text)
                        return response.json()

                    project = await post("/projects", {"project_key": "record-agent-" + uuid4().hex,
                        "name": "录制知识正式执行", "base_url": origin}, 201)
                    project_id = project["id"]
                    async with async_playwright() as playwright:
                        cdp_port = free_port()
                        browser = await playwright.chromium.launch(headless=True,
                            args=[f"--remote-debugging-port={cdp_port}"])
                        operator = None
                        try:
                            recording = await post("/recordings", {"project_id": project["id"],
                                "name": "展开详情完整录制", "entry_url": origin,
                                "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp_port}"}}, 201)
                            recording_id = recording["id"]
                            async with asyncio.timeout(30):
                                while (await client.get("/recordings/" + recording_id)).json()["status"] != "ready":
                                    await asyncio.sleep(.1)
                            await post(f"/recordings/{recording_id}/control", {"action": "start"})
                            # The API created its page through a different CDP
                            # client. Inspect the actual endpoint, not launch's
                            # client-local context inventory.
                            operator = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                            pages = [page for context in operator.contexts for page in context.pages]
                            page = next(page for page in pages if page.url.rstrip("/") == origin)
                            await page.get_by_role("button", name="展开详情").click()
                            await page.get_by_text("已展开 DOM-").wait_for()
                            await asyncio.sleep(.4)
                            screenshot = await page.screenshot()
                            (output / "recorded.png").write_bytes(screenshot)
                            response = await client.post(f"/recordings/{recording_id}/screenshots",
                                files={"file": ("recorded.png", screenshot, "image/png")})
                            response.raise_for_status()
                            shot = response.json()
                            save(output, "screenshot-upload.json", shot)
                            assert shot["backend"] == "rustfs"
                            owned.append(shot["ref"])
                            stored_shot = (await objects.read_object_uri(shot["ref"]))["content"]
                            assert stored_shot == screenshot
                            (output / "recorded-rustfs.png").write_bytes(stored_shot)
                            await post(f"/recordings/{recording_id}/control", {"action": "stop"})
                            saved = (await client.get("/recordings/" + recording_id)).json()
                            save(output, "recording.json", saved)
                            assert saved["status"] == "completed"
                            assert any(event["type"] == "page_response" for event in saved["events"])
                            graph_response = await client.get(f"/recordings/{recording_id}/graph")
                            graph_response.raise_for_status()
                            graph = graph_response.json()
                            save(output, "recording-graph.json", graph)
                            assert graph["status"] == "success"
                            assert len(effects) == 1
                        finally:
                            if operator is not None:
                                await operator.close()
                            await browser.close()
                    content = {"objective": "验证展开详情", "steps": [
                        {"action": "打开项目页面，点击展开详情", "expected": "详情显示已展开及本次服务端DOM确认串"}],
                        "metadata": {"flag": False, "count": 0, "nullable": None}}
                    save(output, "case.json", content)
                    bundle = await post(f"/projects/{project['id']}/test-cases", {"case_key": "details",
                        "title": "验证展开详情", "mode_key": "ui_automation", "case_type": "json", "content_json": content,
                        "execution_notes": "使用本项目完整录制知识了解操作，调用现有浏览器工具真实点击并读取最终状态；不能用JavaScript伪造成功。",
                        "source_refs": [{"source_type": "recording", "source_id": recording_id}],
                        "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"},
                        "generation_metadata": {"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}}, 201)
                    case_id = bundle["case"]["id"]
                    await post(f"/test-cases/{case_id}/submit-review")
                    await post(f"/test-cases/{case_id}/activate", {})
                    plan = await post(f"/projects/{project['id']}/case-plans", {
                        "objective": "验证展开详情，先查询并引用本项目已完成的展开详情完整录制，再按完整正式用例执行。",
                        "required_sources": [{"kind": "recording", "source_id": recording_id}],
                        "model_key": copied.key, "candidate_case_ids": [case_id]}, 201)
                    save(output, "plan.json", plan)
                    assert plan["status"] == "planned", plan
                    knowledge = [entry for entry in plan["evidence"] if entry["kind"] == "recording"]
                    assert len(knowledge) == 1 and knowledge[0]["source_id"] == recording_id
                    frozen_plan = CasePlanRecord.model_validate(plan)
                    assert any(knowledge[0]["id"] in item.evidence_ids for item in frozen_plan.decision.items)
                    assert knowledge[0]["payload"]["graph"] == graph
                    run_request = {"creation_key": "recording-agent-launch", "mode_key": "ui_automation"}
                    if trusted_parent:
                        # Server-owned fixture authority for this explicitly
                        # authorized controlled page. This tests existing parent
                        # scope inheritance, not a public authorization workflow.
                        now = datetime.now(timezone.utc)
                        parent = SessionRecord(id=str(uuid4()), title="受控页面授权来源",
                            status=SessionStatus.idle, session_mode=SessionMode.normal,
                            runtime_mode=RuntimeMode.interactive, mode_key="ui_automation",
                            project_id=project_id, created_at=now, updated_at=now,
                            preferred_model=copied.key,
                            metadata={"source": "owned_test_authority",
                                "resource_scope": {"project_id": project_id, "project_url": origin}})
                        sessions = PostgresSessionStore(settings)
                        await sessions.save_session(parent)
                        saved_parent = await sessions.get_session(parent.id)
                        assert saved_parent.project_id == project_id and saved_parent.metadata == parent.metadata
                        save(output, "trusted-parent.json", {"session_id": parent.id,
                            "project_id": project_id, "metadata": saved_parent.metadata,
                            "authority_source": "server-owned controlled-page fixture; not public product configuration"})
                        run_request["session_id"] = parent.id
                    detail = await post(f"/projects/{project['id']}/case-plans/{plan['id']}/runs", run_request, 201)
                    save(output, "run-created.json", detail)
                    run_id, item_id = detail["run"]["id"], detail["items"][0]["id"]
                    assert await PostgresTaskOutbox(settings).relay_once(queue) == 1
                    worker = processes.start("worker-recording-agent", "-m", "src.cli.run_redis_worker",
                        "--once", "--worker-id", "recording-agent")
                    assert await processes.wait(worker, timeout=650) == 0
                    current = (await client.get("/runs/" + run_id)).json()
                    save(output, "run-after-worker.json", current)
                    index = 0
                    approval_started = monotonic()
                    while current["items"][0]["status"] == "waiting_approval":
                        assert monotonic() - approval_started < 650, "真实审批执行未在测试期限内进入终态"
                        with postgres_connect(settings) as conn, conn.cursor() as cursor:
                            cursor.execute(f"SELECT id,tool_key,metadata FROM {settings.database.postgres_approval_table} WHERE status='pending'")
                            pending = cursor.fetchall()
                        # One model response may request several tools. The
                        # run item exposes the approval to resume next; do not
                        # assume the whole session has exactly one pending job.
                        approval = next(row for row in pending
                            if str(row["id"]) == current["items"][0]["approval_id"])
                        verify_owned_approval(approval, project_id=project_id, run_id=run_id, item_id=item_id, origin=origin)
                        await post(f"/run-items/{item_id}/approval", {"approval_id": str(approval["id"]), "decision": "approved"})
                        current = (await client.get("/runs/" + run_id)).json()
                        save(output, f"run-after-approval-{index}.json", current)
                        index += 1
                    save(output, "run-final.json", current)
                    assert current["items"][0]["status"] == ("passed" if trusted_parent else "blocked"), current
                    assert len(effects) >= 2 if trusted_parent else len(effects) == 1, effects
                    # Native command/snapshot and run_actions are both valid;
                    # do not demand one particular tool's HTML filename.
                    if trusted_parent:
                        assert effects[-1]["confirmation"] in json.dumps(current, ensure_ascii=False)
                    requests = [json.loads(p.read_text(encoding="utf-8")) for p in output.glob("model-*-request.json")]
                    execution_requests = [r for r in requests if "Complete Frozen Case and Preparation Data" in json.dumps(r)]
                    assert execution_requests and any(recording_id in json.dumps(r) and "page_response" in json.dumps(r) for r in execution_requests)
                    assert not any("DO-NOT-INJECT-TEMPLATE" in json.dumps(r) for r in execution_requests)
                    before_sends = len(sends)
                    effect_count = len(effects)
                    with postgres_connect(settings) as conn, conn.cursor() as cursor:
                        cursor.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE payload->>'run_id'=%s", (run_id,))
                        delivery = cursor.fetchone()
                    await queue.enqueue(delivery["payload"])
                    worker = processes.start("worker-recording-agent-replay", "-m", "src.cli.run_redis_worker",
                        "--once", "--worker-id", "recording-agent-replay")
                    assert await processes.wait(worker) == 0
                    assert (await client.get("/runs/" + run_id)).json() == current
                    assert len(sends) == before_sends and len(effects) == effect_count
                    save(output, "verification.json", {"full_default_model_recording_execution": trusted_parent,
                        "expected_policy_block": not trusted_parent,
                        "recording_id": recording_id, "project_id": project["id"], "run_id": run_id,
                        "physical_sends": len(sends), "actual_effects": effects,
                        "terminal_replay_sends": 0, "terminal_replay_effects": 0})
                    deleted = await client.delete("/recordings/" + recording_id)
                    deleted.raise_for_status()
                    save(output, "recording-cleanup.json", deleted.json())
                    recording_id = None
    except BaseException as exc:
        save(output, "failure.json", {"error_type": type(exc).__name__, "message": str(exc),
            "full_default_model_recording_execution": False, "recording_id": recording_id})
        raise
    finally:
        # Graph projections use the shared service. Only this newly created,
        # project-owned recording may be removed, also after a failed run.
        if recording_id and project_id:
            graphs = RecordingGraphStore(get_settings())
            try:
                cleanup = await graphs.delete_recording(recording_id, project_id=project_id)
                save(output, "recording-cleanup.json", cleanup)
                assert cleanup["status"] == "success", cleanup
            finally:
                await graphs.close()
        # Diagnostic collection is deliberately best-effort. A worker or
        # browser cleanup can remove a temporary artifact during copying; the
        # actual run result must remain authoritative in that case.
        copy_diagnostic_artifacts(tmp_path, output)

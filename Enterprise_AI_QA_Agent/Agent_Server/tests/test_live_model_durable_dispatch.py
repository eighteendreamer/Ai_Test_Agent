"""Opt-in formal HTTP -> PG Outbox -> Redis worker -> live model/tool -> SSE.

Only UUID-scoped test tables/Redis keys are written and removed. MySQL model
configuration is read only. No runtime, transport or tool response is replaced.
This is one normal turn in one process, with an optional Chromium probe using
the production Store/ChatTimeline, not full-app GUI/restart/failure acceptance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from redis.asyncio import Redis

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.application.projects.project_store import PostgresProjectStore
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from tests.test_live_model_tool_stream import live_application

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_MODEL_DISPATCH_TESTS") != "1",
    reason="requires configured live model, PostgreSQL and Redis; model-dispatch enables it",
)

WEB_ROOT = Path(__file__).resolve().parents[2] / "agent_web"


@asynccontextmanager
async def browser_probe(api_port, session_id, evidence_dir):
    from playwright.async_api import async_playwright

    node = shutil.which("node")
    assert node, "Installed Node is required; no substitute CLI will be created."
    environment = dict(os.environ, VITE_API_PROXY_TARGET=f"http://127.0.0.1:{api_port}")
    environment.pop("API_AUTH_TOKEN", None)
    # Use Vite's supported ephemeral port; its actual URL is read from stdout.
    log_path = evidence_dir / "vite.log"
    with log_path.open("w", encoding="utf-8") as output:
        vite = await asyncio.create_subprocess_exec(
            node, str(WEB_ROOT / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "0",
            cwd=WEB_ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            async def started():
                while True:
                    text = log_path.read_text(encoding="utf-8")
                    match = re.search(r"http://127\.0\.0\.1:(\d+)/", text)
                    if match:
                        return match.group(0)
                    if vite.returncode is not None:
                        raise RuntimeError(f"Vite exited with {vite.returncode}; stdout/stderr retained in {log_path}")
                    await asyncio.sleep(0.05)
            url = await asyncio.wait_for(started(), 30)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page()
                    errors, failed_http = [], []
                    page.on("pageerror", lambda error: errors.append(type(error).__name__))
                    page.on("response", lambda response: failed_http.append(response.status) if response.status >= 400 else None)
                    async with page.expect_response(lambda response: response.url.endswith(f"/sessions/{session_id}/events")):
                        response = await page.goto(f"{url}e2e/live-model-stream-probe.html?session={session_id}")
                    assert response and response.status == 200
                    await page.wait_for_selector('#probe-status[data-ready="true"]')
                    yield page, browser.version, errors, failed_http
                finally:
                    await browser.close()
        finally:
            if vite.returncode is None:
                vite.terminate()
            await asyncio.wait_for(vite.wait(), 15)


def frontend_manifest():
    paths = [*WEB_ROOT.joinpath("src").rglob("*"), *WEB_ROOT.joinpath("e2e").glob("*")]
    paths += [WEB_ROOT / name for name in ("package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "electron/api-auth-proxy.mjs")]
    entries = [{"path": path.relative_to(WEB_ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(paths) if path.is_file() and path.suffix in {".ts", ".vue", ".css", ".json", ".html", ".mjs"}]
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": entries}


@asynccontextmanager
async def durable_application():
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    prefix = f"modeltest_{suffix}"
    fields = (
        "project", "session", "message", "event", "snapshot", "approval",
        "task_outbox", "agent_task", "agent_attempt", "tool_job", "tool_artifact",
    )
    tables = [f"modeltest_{field}_{suffix}" for field in fields]
    for field, table in zip(fields, tables, strict=True):
        setattr(settings.database, f"postgres_{field}_table", table)
    tables.append(f"{settings.database.postgres_agent_task_table}_runtime")
    tables.extend(f"{settings.database.postgres_agent_task_table}_{kind}" for kind in ("interrupt", "decision"))
    settings.orchestration.agent_task_stream = f"{prefix}:tasks:agent"
    redis = Redis.from_url(settings.database.redis_url, decode_responses=True,
                           socket_timeout=5, socket_connect_timeout=5)
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
    store = PostgresSessionStore(settings)
    jobs = PostgresToolJobStore(settings)
    tasks = PostgresAgentTaskStore(settings, store)
    outbox = PostgresTaskOutbox(settings)
    queue = RedisTaskQueue(settings.database.redis_url,
                           stream=settings.orchestration.agent_task_stream, group=prefix, client=redis)
    try:
        # All stores/FK targets belong to this test, including projects and owners.
        await PostgresProjectStore(settings).initialize()
        await store.initialize()
        await jobs.initialize()
        await outbox.initialize()
        await tasks.initialize()
        await resources.connect()
        await resources.configure_quota(scope="resource_type", identifier="agent", limit=1)
        await queue.connect()
        app, model_key = live_application(settings=settings, session_store=store, job_store=jobs)
        service = app.state.session_service
        dispatcher = AgentTaskDispatcher(settings, tasks, service, resources)
        service.set_agent_task_store(tasks)
        service.set_agent_task_dispatcher(dispatcher)
        app.state.agent_task_dispatcher = dispatcher
        yield app, model_key, tasks, outbox, queue, redis, settings
    finally:
        try:
            # Own exact UUID prefix only; never flush the shared Redis database.
            assert re.fullmatch(r"modeltest_[0-9a-f]{12}", prefix)
            keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
            if keys:
                await redis.delete(*keys)
        finally:
            await redis.aclose()
            with postgres_connect(settings) as connection, connection.cursor() as cursor:
                for table in reversed(tables):
                    assert re.fullmatch(rf"modeltest_[a-z_]+_{suffix}(_runtime)?", table)
                    cursor.execute(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_browser", [False, True], ids=["http", "chromium"])
async def test_formal_message_admission_executes_live_model_via_outbox_and_redis(tmp_path, with_browser):
    if with_browser and os.getenv("RUN_LIVE_MODEL_BROWSER_TESTS") != "1":
        pytest.skip("model-browser enables the real Chromium/frontend acceptance")
    evidence_dir = Path(os.getenv("QA_TEST_GATE_OUTPUT_DIR") or tmp_path)
    browser_context = None
    browser_observations = None
    async with durable_application() as (app, model_key, tasks, outbox, queue, redis, settings):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        receiving = execution = None
        try:
            async def started():
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(started(), 10)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=180) as client:
                response = await client.post("/sessions", json={
                    "title": "Isolated durable live model acceptance", "mode_key": "default",
                    "preferred_model": model_key, "selected_agent": "coordinator",
                })
                response.raise_for_status()
                session_id = response.json()["id"]
                connected = asyncio.Event()

                async def receive():
                    async with (
                        httpx.AsyncClient(timeout=180) as stream_client,
                        stream_client.stream("GET", f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/events") as stream,
                    ):
                        assert stream.status_code == 200
                        connected.set()
                        events = []
                        async for line in stream.aiter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
                                events.append(event)
                                if event["type"] in {"turn.completed", "turn.interrupted", "turn.failed"}:
                                    return events

                receiving = asyncio.create_task(receive())
                await asyncio.wait_for(connected.wait(), 10)
                if with_browser:
                    manifest = frontend_manifest()
                    (evidence_dir / "frontend-sources.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                    browser_context = browser_probe(port, session_id, evidence_dir)
                    page, browser_version, page_errors, failed_http = await browser_context.__aenter__()
                payload = {
                    "content": "Call session-history once to read the recent messages of THIS session. Use scope current_session and action history_summary. Then answer briefly with LIVE_MODEL_TOOL_OK. Do not call other tools.",
                    "agent_key": "coordinator", "model_key": model_key,
                    "context": {"requested_tool_keys": ["session-history"]},
                    "metadata": {"client_message_id": str(uuid4())},
                }
                accepted = await client.post(f"/sessions/{session_id}/messages", json=payload)
                assert accepted.status_code == 202
                task_id = accepted.json()["task"]["id"]
                assert accepted.json()["task"]["status"] == "queued"
                repeated = await client.post(f"/sessions/{session_id}/messages", json=payload)
                assert repeated.status_code == 202 and repeated.json()["task"]["id"] == task_id
                # Actual Outbox publishing and Redis delivery, not hand-made commands.
                assert await outbox.relay_once(queue) == 1
                async def handle(delivery):
                    await app.state.agent_task_dispatcher.handle(delivery, worker_id="live-model-worker")
                worker = RedisTaskWorker.from_settings(queue, settings=settings,
                                                       consumer="live-model-worker", handler=handle)
                execution = asyncio.create_task(worker.run_once())
                events = await asyncio.wait_for(receiving, 180)
                assert await asyncio.wait_for(execution, 30) == 1
                persisted = await client.get(f"/sessions/{session_id}")
                persisted.raise_for_status()
                detail = persisted.json()
                assert detail["status"] == "completed"
                task_response = await client.get(f"/sessions/{session_id}/tasks")
                task_response.raise_for_status()
                assert len(task_response.json()) == 1
                task = task_response.json()[0]
                assert task["id"] == task_id and task["status"] == "completed" and task["attempt_no"] == 1
                # A new store instance reads the persisted result, not a cached response.
                reread = await PostgresSessionStore(settings).get_session(session_id)
                assert [message.model_dump(mode="json") for message in reread.messages] == detail["messages"]
                jobs_response = await client.get(f"/sessions/{session_id}/tool-jobs")
                jobs_response.raise_for_status()
                jobs = jobs_response.json()
                assert jobs and all(job["tool_key"] == "session-history" and job["status"] == "completed" for job in jobs)
                final = [message for message in detail["messages"]
                         if message["role"] == "assistant" and message["content"]][-1]
                deltas = [event["payload"]["delta"] for event in events
                          if event["type"] == "assistant.stream.delta" and event["payload"].get("message_id") == final["id"]]
                assert deltas and "".join(deltas) == final["content"]
                assert any(event["type"] == "assistant.stream.completed" for event in events)
                assert (await redis.xpending(queue.stream, queue.group))["pending"] == 0
                assert await outbox.pending_counts(streams=[queue.stream]) == {queue.stream: 0}
                before = await tasks.get(task_id)
                terminal = await tasks.claim(task_id, worker_id="another-worker")
                assert terminal == before and terminal.status == "completed"
                if with_browser:
                    await page.wait_for_function("() => { const result = window.liveModelProbe.observations(); return result.turnCompleted && result.streamMatchesFinal && result.toolCompleted; }", timeout=30000)
                    assert await page.evaluate("([id, content]) => window.liveModelProbe.compareFinal(id, content)", [final["id"], final["content"]])
                    assert await page.locator(".chat-assistant-message .assistant-markdown").last.is_visible()
                    assert await page.locator(".chat-assistant-message .assistant-markdown").last.inner_text()
                    browser_observations = await page.evaluate("window.liveModelProbe.observations()")
                    assert browser_observations["finalMessageUnique"] and not browser_observations["error"]
                    assert not page_errors and not failed_http, {"page_error_types": page_errors, "http_statuses": failed_http}
                    assert frontend_manifest()["sha256"] == manifest["sha256"], "Frontend files changed during acceptance."
                observations = {
                    "status": "passed", "model_key": model_key, "completed_tools": len(jobs),
                    "sse_events": len(events), "delta_count": len(deltas), "final_chars": len(final["content"]),
                    "session_storage": "isolated_postgres_tables", "execution_entry": "formal_http_messages",
                    "durable_dispatcher_verified": True, "redis_worker_ack_verified": True,
                    "idempotent_admission_verified": True, "terminal_attempt_unchanged": True,
                    "browser_verified": with_browser, "restart_verified": False, "release_ready": False,
                }
                if with_browser:
                    observations.update({"browser": browser_version, "frontend_source_sha256": manifest["sha256"],
                                         "browser_observations": browser_observations,
                                         "browser_scope": "production_store_and_chat_timeline_vite_probe"})
                (evidence_dir / "acceptance.json").write_text(
                    json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8",
                )
        finally:
            try:
                if browser_context is not None:
                    await browser_context.__aexit__(None, None, None)
            finally:
                for task in (receiving, execution):
                    if task is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                server.should_exit = True
                try:
                    await asyncio.wait_for(serving, 15)
                finally:
                    listener.close()

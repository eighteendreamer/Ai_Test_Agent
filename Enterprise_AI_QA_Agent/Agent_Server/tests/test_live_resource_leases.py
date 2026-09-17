from __future__ import annotations

import asyncio
import os
import json
import multiprocessing
import shutil
import subprocess
import tempfile
import threading
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
from dataclasses import replace

import pytest
import httpx
import pytest_asyncio
from psycopg import sql
from redis.exceptions import ResponseError

from src.core.config import get_settings
from src.core.request_context import bind_request_context
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.performance.http_probe_runner import HttpProbeRunner
from src.application.performance.mock_target_runner import MockTargetRunner
from src.application.security.tool_bootstrap_service import ToolBootstrapService
from src.application.security.execution_environment_service import SecurityExecutionEnvironmentService
from src.modes.security_testing_mode.attack_session import SecurityShellSession
from src.application.resources.session_resource_service import SessionResourceService
from src.runtime.session_resource_store import InMemorySessionResourceStore
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.runtime.tool_job_service import ToolJobService
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall
from src.application.performance.engine_adapter import EngineCommand, ScriptArtifact
from src.runtime.docker_resource_manager import DockerResourceManager
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.cli.run_resource_cleanup_worker import build_cleanup_callbacks
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from src.runtime.postgres_resource_allocation_store import PostgresResourceAllocationStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_cleanup_worker import (
    ResourceCleanupTaskHandler,
    ResourceCleanupWorker,
)
from src.runtime.resource_lease_manager import ExpiredResourceLease, RedisResourceLeaseManager, ResourceUnavailable
from tests.test_resource_cleanup_worker import ReceiptStore


def _run_performance_until_killed(prefix, stream, temp_root, before_bind=None):
    async def run():
        tempfile.tempdir = temp_root
        settings = get_settings().model_copy(deep=True)
        settings.orchestration.resource_default_lease_seconds = 2
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, cleanup_stream=stream)
        allocations = PostgresResourceAllocationStore(settings)
        if before_bind is not None:
            async def wait_before_binding(*args):
                before_bind.set()
                await asyncio.Event().wait()
            allocations.bind = wait_before_binding
        runner = PerfRunnerService(settings, docker_resources=DockerResourceManager(
            settings, lease_manager=manager, allocation_store=allocations))
        with bind_request_context(project_id="p-live-perf", session_id="s-live-perf", run_id="r-live-perf",
                                  run_item_id="i-live-perf", attempt_id="a-live-perf", worker_id="perf-child"):
            await runner._run_in_docker(
                EngineCommand(image="busybox", command=["sleep", "300"]),
                ScriptArtifact(engine="k6", filename="test.js", script_content=""), "qa-perf-kill", 310,
            )
    asyncio.run(run())


async def _wait_for_running_container(manager):
    async def wait():
        while True:
            for lease in await manager.list_active():
                container_id = lease.get("external_resource_id")
                if not container_id:
                    continue
                inspected = await asyncio.to_thread(subprocess.run,
                    ["docker", "inspect", "--format", "{{.State.Running}}", container_id],
                    capture_output=True, text=True, timeout=15)
                if inspected.returncode == 0 and inspected.stdout.strip() == "true":
                    return container_id
            await asyncio.sleep(.05)
    return await asyncio.wait_for(wait(), timeout=30)


async def _complete_real_cleanup(manager, *, retain_receipt=False):
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    queue = RedisTaskQueue(settings.database.redis_url, stream=manager._cleanup_stream, group="live-perf-cleanup")
    task_ids = []
    try:
        await store.initialize()
        await queue.connect()
        payloads = [json.loads(fields["payload"]) for _, fields in await manager._client.xrange(manager._cleanup_stream)]
        task_ids = [payload["task_id"] for payload in payloads]
        assert len(task_ids) == 1
        worker = RedisTaskWorker(queue, consumer="live-perf-cleanup", block_ms=0,
            handler=ResourceCleanupTaskHandler(store, build_cleanup_callbacks(settings, manager=manager), manager=manager))
        assert await worker.run_once() == 1
        assert not await queue.pending()
        assert not await manager.usage_snapshot()
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT status, record FROM {} WHERE task_id = %s").format(
                sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_ids[0],))
            row = cur.fetchone()
            assert row["status"] == "completed"
            assert row["record"]["attempt_id"] == "a-live-perf"
    finally:
        await queue.close()
        if task_ids and not retain_receipt:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE task_id = ANY(%s)").format(
                    sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_ids,))


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Docker")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "nonzero", "timeout", "cancel", "lost_lease"])
async def test_performance_container_lifecycle_with_real_resources(manager, scenario):
    m, _ = manager
    settings = get_settings().model_copy(deep=True)
    settings.orchestration.resource_default_lease_seconds = 2
    runner = PerfRunnerService(settings, docker_resources=DockerResourceManager(settings, lease_manager=m))
    command = ["sleep", "300"] if scenario in {"timeout", "cancel", "lost_lease"} else ["sh", "-c", "echo result; echo detail >&2; exit " + ("7" if scenario == "nonzero" else "0")]
    if scenario == "timeout":
        command = ["sh", "-c", "echo result; echo detail >&2; sleep 300"]
    execution = None
    container_id = ""
    try:
        with bind_request_context(project_id="p-live-perf", session_id="s-live-perf", run_id="r-live-perf",
                                  run_item_id="i-live-perf", attempt_id="a-live-perf", worker_id="perf-live"):
            execution = asyncio.create_task(runner._run_in_docker(
                EngineCommand(image="busybox", command=command),
                ScriptArtifact(engine="k6", filename="test.js", script_content=""), "qa-perf-live",
                1.5 if scenario == "timeout" else 60,
            ))
            if scenario in {"cancel", "lost_lease"}:
                container_id = await _wait_for_running_container(m)
                assert (await m.usage_snapshot())["project:p-live-perf"] == 1
                if scenario == "cancel":
                    execution.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await execution
                else:
                    async def reject_renewal(*args, **kwargs): return False
                    m.renew = reject_renewal
                    with pytest.raises(RuntimeError, match="heartbeat rejected"):
                        await execution
            else:
                result = await execution
                container_id = result.container_id
                assert result.exit_code == ({"success": 0, "nonzero": 7, "timeout": -1}[scenario])
                assert result.timed_out == (scenario == "timeout")
                assert "result" in result.stdout and "detail" in result.stderr
        assert (await m.usage_snapshot())["global:all"] == 1
        pending = await m.list_cleanup_pending()
        assert pending[0]["external_resource_id"] == container_id
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Docker")
@pytest.mark.asyncio
@pytest.mark.parametrize("before_binding", [False, True])
async def test_performance_worker_kill_recovers_container(manager, tmp_path, before_binding):
    m, _ = manager
    context = multiprocessing.get_context("spawn")
    created = context.Event() if before_binding else None
    process = context.Process(target=_run_performance_until_killed,
        args=(m._prefix, m._cleanup_stream, str(tmp_path), created))
    container_id = ""
    try:
        process.start()
        if before_binding:
            async def wait_for_create():
                while not created.is_set():
                    assert process.is_alive()
                    await asyncio.sleep(.05)
            await asyncio.wait_for(wait_for_create(), timeout=30)
            active = (await m.list_active())[0]
            assert not active.get("external_resource_id")
            found = subprocess.run(["docker", "ps", "--all", "--no-trunc", "--filter",
                f"label=qa.allocation_id={active['allocation_id']}", "--format", "{{.ID}}"],
                capture_output=True, text=True, check=True, timeout=15)
            container_id = found.stdout.strip()
            assert len(container_id) == 64
            inspected = subprocess.run(["docker", "inspect", "--format", "{{.State.Status}}", container_id],
                capture_output=True, text=True, check=True, timeout=15)
            assert inspected.stdout.strip() == "created"
            records = await PostgresResourceAllocationStore(get_settings()).get_many(
                [active["allocation_id"]], namespace=m.namespace)
            assert records[active["allocation_id"]]["container_id"] is None
        else:
            container_id = await _wait_for_running_container(m)
        assert process.is_alive()
        bound = (await m.list_active())[0]
        assert bound["run_item_id"] == "i-live-perf" and bound["attempt_id"] == "a-live-perf"
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive()
        await asyncio.sleep(2.2)
        reaper = ResourceCleanupWorker(m, interval_seconds=1)
        assert await reaper.run_once() == 1
        assert (await m.usage_snapshot())["global:all"] == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 10)
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Docker")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["redis_loss", "late_create"])
async def test_durable_inventory_recovers_late_or_lost_container(manager, scenario):
    m, resource_id = manager
    settings = get_settings()
    allocation_id = uuid4().hex
    container_name = f"qa-intent-{allocation_id[:12]}"
    container_id = ""
    store = PostgresResourceAllocationStore(settings)
    receipts = PostgresResourceCleanupStore(settings)
    await receipts.initialize()
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
        worker_id="creator", lease_seconds=60, attempt_id="a-live-perf")
    await store.prepare(allocation_id, lease, namespace=m.namespace, container_name=container_name)
    assert await m.prepare_allocation(lease, allocation_id)
    cleanup = build_cleanup_callbacks(settings, manager=m, allocations=store)["docker"]
    new_owner = None
    task_id = f"task_cleanup_orphan_{allocation_id}_"
    queue = RedisTaskQueue(settings.database.redis_url, stream=m._cleanup_stream, group="inventory-test")
    try:
        if scenario == "late_create":
            await _expire(m, lease)
            await m.reap_expired()
            await _complete_real_cleanup(m, retain_receipt=True)  # Keep the successful empty-scan receipt too.
            new_owner = await m.acquire(resource_type="docker", resource_id=resource_id,
                worker_id="new-owner", lease_seconds=60)
        args = ["docker", "create", "--name", container_name]
        labels = {"enterprise-ai-qa-agent": "managed", "qa.allocation_id": allocation_id,
                  "qa.lease_namespace": m.namespace, "qa.resource_id": resource_id,
                  "qa.fencing_token": str(lease.fencing_token)}
        for key, value in labels.items():
            args.extend(["--label", f"{key}={value}"])
        created = subprocess.run([*args, "busybox", "sleep", "300"],
            capture_output=True, text=True, check=True, timeout=30)
        container_id = created.stdout.strip()
        task_id += container_id
        if scenario == "redis_loss":
            assert await cleanup.reconcile(m) == 0  # A valid owner is never cleaned.
            await store.bind(allocation_id, container_id)
            assert await m.bind(lease, container_id)
            await _clear_test_namespace(m, m._cleanup_stream)  # Only this test's Redis data.
            # A broken Stream must not leave a dedup marker that prevents recovery.
            await m._client.set(m._cleanup_stream, "simulate_wrong_stream_type")
            with pytest.raises(ResponseError, match="WRONGTYPE"):
                await cleanup.reconcile(m)
            assert not await m._client.hgetall(m._key("cleanup:inventory_deliveries"))
            await m._client.delete(m._cleanup_stream)
        await queue.connect()
        assert await cleanup.reconcile(m) == 1
        assert await cleanup.reconcile(m) == 0  # Stable task id / pending inventory dedup.
        # A different consumer group can see an old ordinary cleanup delivery too.
        handler = ResourceCleanupTaskHandler(receipts, {"docker": cleanup}, manager=m)
        worker = RedisTaskWorker(queue, consumer="inventory-worker", block_ms=0, handler=handler)
        while await worker.run_once():
            pass
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT status FROM {} WHERE task_id = %s").format(
                sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_id,))
            assert cur.fetchone()["status"] == "completed"
        assert not await queue.pending()
        assert not await m._client.hgetall(m._key("cleanup:inventory_deliveries"))
        assert await cleanup.reconcile(m) == 0
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
        assert allocation_id in await store.get_many([allocation_id], namespace=m.namespace)
        if new_owner:
            assert (await m.get(resource_type="docker", resource_id=resource_id))["lease_token"] == new_owner.lease_token
            assert (await m.usage_snapshot())["global:all"] == 1
            assert await m.retire(new_owner)
        assert not await m.usage_snapshot()
    finally:
        await queue.close()
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id = ANY(%s)").format(
                sql.Identifier(settings.database.postgres_resource_cleanup_table)),
                ([task_id, f"task_cleanup_{lease.lease_token}"],))


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live allocation database")
@pytest.mark.asyncio
async def test_durable_allocation_cannot_be_reassigned(manager):
    m, resource_id = manager
    store = PostgresResourceAllocationStore(get_settings())
    allocation_id = uuid4().hex
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
        worker_id="creator", lease_seconds=60)
    await store.prepare(allocation_id, lease, namespace=m.namespace, container_name="intent-only")
    await store.prepare(allocation_id, lease, namespace=m.namespace, container_name="intent-only")
    with pytest.raises(ValueError, match="cannot be reassigned"):
        await store.prepare(allocation_id, lease, namespace=m.namespace, container_name="changed-name")
    with pytest.raises(ValueError, match="cannot be reassigned"):
        await store.prepare(allocation_id, replace(lease, worker_id="different-owner"),
                            namespace=m.namespace, container_name="intent-only")
    await store.bind(allocation_id, "a" * 64)
    await store.bind(allocation_id, "a" * 64)
    with pytest.raises(ValueError, match="already assigned"):
        await store.bind(allocation_id, "b" * 64)
    assert not await store.get_many([allocation_id], namespace=m.namespace + ":other")
    rows = await store.get_many([allocation_id], namespace=m.namespace)
    assert rows[allocation_id]["container_name"] == "intent-only"
    assert rows[allocation_id]["container_id"] == "a" * 64
    assert rows[allocation_id]["lease_record"]["worker_id"] == "creator"


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Docker curl image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "http_error", "timeout", "cancel", "capacity"])
async def test_http_probe_tool_with_real_resources(manager, monkeypatch, scenario):
    m, _ = manager
    settings = get_settings().model_copy(deep=True)
    settings.orchestration.resource_default_lease_seconds = 2
    received, release = threading.Event(), threading.Event()

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            if scenario in {"timeout", "cancel"}:
                release.wait(15)
            try:
                self.send_response(503 if scenario == "http_error" else 200)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass  # The test deliberately closes the client on timeout/cancel.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = HttpProbeRunner(settings, docker_resources=DockerResourceManager(settings, lease_manager=m))
    monkeypatch.setattr("src.application.performance.http_probe_runner.HttpProbeRunner", lambda _settings: runner)
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = ToolRuntimeService(settings=settings, tool_job_service=jobs)
    tool = ToolDescriptor(key="http-probe-runner", name="probe", description="probe", category="test")
    context = ToolExecutionContext(session_id="s-live-probe", turn_id="t-live-probe", trace_id="trace-probe",
        user_message="probe", normalized_input="probe", context_bundle={"project_id": "p-live-probe"})
    call = ModelToolCall(id="probe-call", name=tool.key, arguments={
        "target_url": f"http://host.docker.internal:{server.server_port}/health",
        "timeout_seconds": 1 if scenario == "timeout" else 10,
    })
    execution = None
    try:
        if scenario == "capacity":
            blocker = await m.acquire(resource_type="docker", resource_id="docker-slot-0",
                worker_id="capacity-owner", lease_seconds=60)
        with bind_request_context(run_id="r-live-probe", run_item_id="i-live-probe", attempt_id="a-live-perf"):
            execution = asyncio.create_task(runtime.execute(tool, call, context))
            if scenario == "cancel":
                assert await asyncio.to_thread(received.wait, 10)
                execution.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await execution
            else:
                record = await execution
                expected = {"success": "completed", "http_error": "failed", "timeout": "failed",
                            "capacity": "waiting_resource"}[scenario]
                assert record.status == expected
                assert (await jobs.get_job(record.job_id)).status.value == expected
                if scenario == "success":
                    assert record.output["status_code"] == 200
                if scenario == "http_error":
                    assert record.output["status_code"] == 503
                if scenario == "timeout":
                    assert record.output["reason"].startswith("timeout:")
        if scenario == "capacity":
            assert not received.is_set()
            assert not await m.list_cleanup_pending()
            assert await m.retire(blocker)
        else:
            pending = await m.list_cleanup_pending()
            assert len(pending) == 1
            assert pending[0]["session_id"] == context.session_id
            assert pending[0]["project_id"] == "p-live-probe"
            assert (await m.usage_snapshot())["global:all"] == 1
            await _complete_real_cleanup(m)
            inspected = subprocess.run(["docker", "inspect", pending[0]["external_resource_id"]],
                capture_output=True, timeout=15)
            assert inspected.returncode != 0
    finally:
        release.set()
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join, 5)


def _unused_mock_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _run_mock_until_killed(prefix, stream, port, ready):
    async def run():
        settings = get_settings().model_copy(deep=True)
        settings.orchestration.resource_default_lease_seconds = 2
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, cleanup_stream=stream)
        runner = MockTargetRunner(settings, lease_manager=manager)
        with bind_request_context(session_id="s-live-mock", attempt_id="a-live-perf"):
            await runner.start(port=port)
        ready.set()
        await asyncio.Event().wait()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Docker mock image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["remote_stop", "session_end", "shutdown", "lifetime", "registration_failure"])
async def test_mock_target_tool_session_lifecycle(manager, scenario):
    m, _ = manager
    settings = get_settings().model_copy(deep=True)
    settings.orchestration.resource_default_lease_seconds = 2
    settings.orchestration.resource_mock_target_max_lifetime_seconds = 2 if scenario == "lifetime" else 60
    resources = SessionResourceService(InMemorySessionResourceStore())
    runtime = ToolRuntimeService(settings=settings, session_resource_service=resources)
    runner = MockTargetRunner(settings, lease_manager=m)
    runtime._mock_target_runner = runner
    tool = ToolDescriptor(key="mock-target-runner", name="mock", description="mock", category="test")
    context = ToolExecutionContext(session_id="s-live-mock", turn_id="t-live-mock", trace_id="trace-mock",
        user_message="mock", normalized_input="mock", context_bundle={"project_id": "p-live-mock"})
    port = _unused_mock_port()
    if scenario == "registration_failure":
        async def unavailable(**kwargs): raise ConnectionError("test resource registry unavailable")
        resources.register = unavailable
    try:
        with bind_request_context(attempt_id="a-live-perf"):
            record = await runtime.execute(tool, ModelToolCall(id="mock-start", name=tool.key,
                arguments={"action": "start", "port": port, "response_text": "mock-owned"}), context)
        if scenario == "registration_failure":
            assert record.status == "failed"
        else:
            assert record.status == "completed"
            name = record.output["container_name"]
            active = await m.list_active()
            assert active[0]["session_id"] == context.session_id
            assert active[0]["project_id"] == "p-live-mock"
            assert (await resources.list_active(context.session_id))[0].resource_key == name
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/", timeout=5)
                assert response.status_code == 200 and response.text.strip() == "mock-owned"
            if scenario == "remote_stop":
                waiting = await runtime.execute(tool, ModelToolCall(id="mock-capacity", name=tool.key,
                    arguments={"action": "start", "port": _unused_mock_port()}), context)
                assert waiting.status == "waiting_resource"
                assert len(await m.list_active()) == 1
                # A second runner models a request routed to another API process.
                remote = MockTargetRunner(settings, lease_manager=m)
                with pytest.raises(ValueError, match="does not belong"):
                    await remote.stop(container_name=name, session_id="other-session")
                assert len(await m.list_active()) == 1
                # Lease must survive beyond its initial TTL while idle between tools.
                await asyncio.sleep(2.2)
                assert len(await m.list_active()) == 1
                assert (await remote.stop(container_name=name, session_id=context.session_id)).ok
                assert (await remote.stop(container_name=name, session_id=context.session_id)).ok
            elif scenario == "session_end":
                cleaned = await resources.cleanup_session(context.session_id, reason="session_complete")
                assert len(cleaned) == 1 and cleaned[0].status.value == "released"
            elif scenario == "shutdown":
                await runtime.close()
                with pytest.raises(RuntimeError, match="closed"):
                    await runner.start(port=_unused_mock_port())
            await asyncio.wait_for(asyncio.gather(*list(runner._tasks), return_exceptions=True), timeout=15)
        pending = await m.list_cleanup_pending()
        assert len(pending) == 1
        assert (await m.usage_snapshot())["global:all"] == 1
        await _complete_real_cleanup(m)
        assert not runner._tasks and not runner._by_name
        inspected = subprocess.run(["docker", "inspect", pending[0]["external_resource_id"]],
            capture_output=True, timeout=15)
        assert inspected.returncode != 0
    finally:
        await runtime.close()


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Docker mock image")
@pytest.mark.asyncio
async def test_mock_target_worker_kill_recovered_by_cleanup(manager):
    m, _ = manager
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_run_mock_until_killed,
        args=(m.namespace, m._cleanup_stream, _unused_mock_port(), ready))
    container_id = ""
    try:
        process.start()
        async def wait_ready():
            while not ready.is_set():
                assert process.is_alive()
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait_ready(), timeout=30)
        container_id = await _wait_for_running_container(m)
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive()
        await asyncio.sleep(2.2)
        assert await ResourceCleanupWorker(m).run_once() == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if process.is_alive(): process.terminate()
        await asyncio.to_thread(process.join, 10)
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)


def _bootstrap_settings():
    settings = get_settings().model_copy(deep=True)
    settings.orchestration.resource_default_lease_seconds = 2
    settings.security.security_tool_bootstrap_enabled = True
    settings.security.security_tool_bootstrap_package_allowlist = "tcpdump"
    settings.security.security_tool_bootstrap_image_allowlist = "vxcontrol/kali-linux:latest"
    settings.security.security_tool_bootstrap_repository_allowlist = "kali-rolling"
    settings.security.security_tool_bootstrap_cleanup_required = True
    settings.security.security_runner_docker_image = "vxcontrol/kali-linux:latest"
    settings.security.security_runner_docker_network = "none"
    return settings


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["ready", "capacity", "unapproved", "timeout", "cancel", "cleanup_failure"])
async def test_tool_bootstrap_managed_container(manager, tmp_path, scenario):
    m, _ = manager
    settings = _bootstrap_settings()
    docker = DockerResourceManager(settings, lease_manager=m)
    service = ToolBootstrapService(settings=settings, docker_resources=docker)
    # Real readiness uses the installed image. Never install packages in this test.
    service._install_command = lambda *_: "echo fixture_install_refused >&2; exit 100"
    if scenario in {"timeout", "cancel"}:
        service._readiness_command = lambda *_: "echo readiness_started; sleep 300"
    original_command = docker._command
    if scenario == "cleanup_failure":
        async def failed_remove(args, *, timeout):
            if args[0] == "rm": return 1, "", "injected Docker removal failure"
            return await original_command(args, timeout=timeout)
        docker._command = failed_remove
    operation = None
    try:
        if scenario == "capacity":
            blocker = await m.acquire(resource_type="docker", resource_id="docker-slot-0",
                worker_id="busy", lease_seconds=60)
        with bind_request_context(session_id="s-live-bootstrap", project_id="p-live-bootstrap", attempt_id="a-live-perf"):
            operation = asyncio.create_task(service.run(campaign_id="test-bootstrap", target_allowlist=["127.0.0.1"],
                profile_key="tcpdump_timed_capture", tool_name="tcpdump", approval_scope_hash="test-scope",
                approval_granted=scenario != "unapproved", artifact_dir=tmp_path, timeout_seconds=1))
            if scenario == "cancel":
                await _wait_for_running_container(m)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError): await operation
                manifest = json.loads(next(tmp_path.glob("bootstrap_*.json")).read_text(encoding="utf-8"))
                assert manifest["status"] == "cancelled" and manifest["cleanup_complete"]
            else:
                manifest = await operation
                if scenario == "ready":
                    assert manifest.status == "already_available", manifest.failure_reason
                    assert manifest.readiness_exit_code == 0 and manifest.resolved_version
                    assert manifest.cleanup_complete
                elif scenario == "capacity":
                    assert manifest.status == "waiting_resource" and not manifest.container_name
                    assert await m.retire(blocker)
                elif scenario == "unapproved":
                    assert manifest.status == "waiting_approval" and not manifest.container_name
                elif scenario == "timeout":
                    assert manifest.status == "failed" and manifest.cleanup_complete
                    assert "readiness_started" in manifest.stdout
                else:
                    assert manifest.status == "failed" and not manifest.cleanup_complete
                    assert manifest.failure_category == "cleanup_failed"
        if scenario in {"capacity", "unapproved"}:
            assert not await m.list_cleanup_pending()
            assert not await m.usage_snapshot()
        else:
            pending = await m.list_cleanup_pending()
            assert len(pending) == 1 and pending[0]["project_id"] == "p-live-bootstrap"
            assert (await m.usage_snapshot())["global:all"] == 1
            docker._command = original_command
            await _complete_real_cleanup(m)
            inspected = subprocess.run(["docker", "inspect", pending[0]["external_resource_id"]],
                capture_output=True, timeout=15)
            assert inspected.returncode != 0
    finally:
        docker._command = original_command
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        # This exact namespace belongs to this test; leave no injected cleanup failure behind.
        for pending in await m.list_cleanup_pending():
            if pending.get("external_resource_id"):
                await original_command(["rm", "--force", pending["external_resource_id"]], timeout=15)


def _run_bootstrap_until_killed(prefix, stream, artifact_dir):
    async def run():
        settings = _bootstrap_settings()
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, cleanup_stream=stream)
        service = ToolBootstrapService(settings=settings,
            docker_resources=DockerResourceManager(settings, lease_manager=manager))
        service._readiness_command = lambda *_: "touch /work/readiness-started; sleep 300"
        service._install_command = lambda *_: "exit 100"
        with bind_request_context(session_id="s-live-bootstrap-kill", attempt_id="a-live-perf"):
            await service.run(campaign_id="bootstrap-kill", target_allowlist=["127.0.0.1"],
                profile_key="tcpdump_timed_capture", tool_name="tcpdump", approval_scope_hash="test-scope",
                approval_granted=True, artifact_dir=artifact_dir, timeout_seconds=310)
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
async def test_tool_bootstrap_worker_kill_recovered_by_cleanup(manager, tmp_path):
    m, _ = manager
    process = multiprocessing.get_context("spawn").Process(target=_run_bootstrap_until_killed,
        args=(m.namespace, m._cleanup_stream, tmp_path))
    container_id = ""
    try:
        process.start()
        container_id = await _wait_for_running_container(m)
        async def wait_for_readiness():
            while True:
                assert process.is_alive()
                checked = await asyncio.to_thread(subprocess.run,
                    ["docker", "exec", container_id, "test", "-f", "/work/readiness-started"],
                    capture_output=True, timeout=15)
                if checked.returncode == 0:
                    return
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait_for_readiness(), timeout=15)
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive()
        assert (await m.usage_snapshot())["global:all"] == 1
        await asyncio.sleep(2.2)
        assert await ResourceCleanupWorker(m).run_once() == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if process.is_alive(): process.terminate()
        await asyncio.to_thread(process.join, 10)
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)


def _security_foreground_settings():
    settings = _bootstrap_settings()
    settings.security.security_runner_backend = "docker"
    settings.security.security_runner_container_reuse = False
    settings.security.security_runner_docker_cleanup_after_run = True
    settings.security.security_runner_docker_pull_policy = "never"
    settings.security.security_runner_docker_net_raw = False
    settings.security.security_runner_docker_net_admin = False
    settings.security.security_runner_wrap_timeout = False
    return settings


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "nonzero", "timeout", "cancel", "lost_lease"])
async def test_security_foreground_managed_container(manager, tmp_path, scenario):
    m, _ = manager
    settings = _security_foreground_settings()
    service = SecurityExecutionEnvironmentService(settings=settings,
        docker_resources=DockerResourceManager(settings, lease_manager=m))
    context = ToolExecutionContext(session_id="s-live-security", turn_id="t-live-security", trace_id="trace-security",
        user_message="test", normalized_input="test", context_bundle={"project_id": "p-live-security"})
    command = "echo stdout-result; echo stderr-detail >&2; echo evidence > evidence.txt; "
    command += "sleep 300" if scenario in {"timeout", "cancel", "lost_lease"} else "exit " + ("7" if scenario == "nonzero" else "0")
    operation = None
    try:
        with bind_request_context(run_id="r-live-security", run_item_id="i-live-security", attempt_id="a-live-perf"):
            operation = asyncio.create_task(service.execute(command=command, command_args=["sh", "-lc", command],
                timeout_seconds=1 if scenario == "timeout" else 310, artifact_dir=tmp_path, context=context))
            if scenario in {"cancel", "lost_lease"}:
                await _wait_for_running_container(m)
                if scenario == "cancel":
                    operation.cancel()
                    with pytest.raises(asyncio.CancelledError): await operation
                else:
                    async def rejected(*args, **kwargs): return False
                    m.renew = rejected
                    with pytest.raises(RuntimeError, match="heartbeat rejected"): await operation
            else:
                result = await operation
                assert result.exit_code == {"success": 0, "nonzero": 7, "timeout": -1}[scenario]
                assert result.timed_out == (scenario == "timeout")
                assert "stdout-result" in result.stdout and "stderr-detail" in result.stderr
                assert (tmp_path / "evidence.txt").read_text().strip() == "evidence"
                assert any(artifact["label"] == "evidence.txt" for artifact in result.output_artifacts)
        pending = await m.list_cleanup_pending()
        assert len(pending) == 1
        assert pending[0]["session_id"] == context.session_id and pending[0]["run_item_id"] == "i-live-security"
        assert pending[0]["project_id"] == "p-live-security"
        assert (await m.usage_snapshot())["global:all"] == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", pending[0]["external_resource_id"]], capture_output=True, timeout=15)
        assert inspected.returncode != 0
    finally:
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [False, True])
async def test_security_foreground_tool_real_profile(manager, tmp_path, monkeypatch, capacity):
    m, _ = manager
    settings = _security_foreground_settings()
    settings.security.security_runner_docker_network = ""
    settings.security.security_target_allowlist = "host.docker.internal"
    received = threading.Event()

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            self.send_response(200)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = ToolRuntimeService(settings=settings, tool_job_service=jobs)
    runtime._security_execution_environment = SecurityExecutionEnvironmentService(settings=settings,
        docker_resources=DockerResourceManager(settings, lease_manager=m))
    monkeypatch.setattr(runtime, "_prepare_local_artifact_dir", lambda *_: tmp_path)
    tool = ToolDescriptor(key="web-scan-runner", name="headers", description="test", category="test")
    context = ToolExecutionContext(session_id="s-live-security-tool", turn_id="t-live-security-tool", trace_id="trace-security",
        user_message="headers", normalized_input="headers", context_bundle={"project_id": "p-live-security"})
    try:
        if capacity:
            blocker = await m.acquire(resource_type="docker", resource_id="docker-slot-0", worker_id="busy", lease_seconds=60)
        with bind_request_context(attempt_id="a-live-perf"):
            record = await runtime.execute(tool, ModelToolCall(id="security-headers", name=tool.key, arguments={
                "command_profile": "http_headers_probe", "target": f"http://host.docker.internal:{server.server_port}/",
            }), context)
        expected = "waiting_resource" if capacity else "completed"
        assert record.status == expected, record.summary
        assert (await jobs.get_job(record.job_id)).status.value == expected
        if capacity:
            assert record.output["waiting_reason"] == "global:all"
            assert not received.is_set() and not await m.list_cleanup_pending()
            assert await m.retire(blocker)
        else:
            assert received.is_set()
            assert record.output["exit_code"] == 0
            pending = await m.list_cleanup_pending()
            assert len(pending) == 1 and pending[0]["session_id"] == context.session_id
            await _complete_real_cleanup(m)
    finally:
        await runtime.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join, 5)


def _run_security_foreground_until_killed(prefix, stream, artifact_dir):
    async def run():
        settings = _security_foreground_settings()
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, cleanup_stream=stream)
        service = SecurityExecutionEnvironmentService(settings=settings,
            docker_resources=DockerResourceManager(settings, lease_manager=manager))
        with bind_request_context(session_id="s-live-security-kill", attempt_id="a-live-perf"):
            command = "echo ready > started.txt; sleep 300"
            await service.execute(command=command, command_args=["sh", "-lc", command],
                timeout_seconds=310, artifact_dir=artifact_dir)
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
async def test_security_foreground_worker_kill_recovered_by_cleanup(manager, tmp_path):
    m, _ = manager
    process = multiprocessing.get_context("spawn").Process(target=_run_security_foreground_until_killed,
        args=(m.namespace, m._cleanup_stream, tmp_path))
    container_id = ""
    try:
        process.start()
        container_id = await _wait_for_running_container(m)
        async def wait_for_command():
            while not (tmp_path / "started.txt").exists():
                assert process.is_alive()
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait_for_command(), timeout=15)
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive()
        assert (await m.usage_snapshot())["global:all"] == 1
        await asyncio.sleep(2.2)
        assert await ResourceCleanupWorker(m).run_once() == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if process.is_alive(): process.terminate()
        await asyncio.to_thread(process.join, 10)
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["stateful", "remote_close", "lifetime", "cancel", "timeout", "lost_lease", "shutdown"])
async def test_security_persistent_container_lifecycle(manager, tmp_path, scenario):
    m, _ = manager
    settings = _security_foreground_settings()
    settings.security.security_attack_session_timeout_seconds = 2 if scenario == "lifetime" else 60
    docker = DockerResourceManager(settings, lease_manager=m)
    service = SecurityExecutionEnvironmentService(settings=settings, docker_resources=docker)
    owner = ToolExecutionContext(session_id="s-live-security-session", turn_id="turn-security-session", trace_id="trace-session",
        user_message="test", normalized_input="test", context_bundle={"project_id": "p-live-security"})
    foreign = ToolExecutionContext(session_id="s-foreign", turn_id="turn-foreign", trace_id="trace-foreign",
        user_message="test", normalized_input="test", context_bundle={})
    shell = SecurityShellSession(environment=service, campaign_id="same-campaign", context=owner,
        approval_scope_hash="approved-fixture", target_allowlist=["127.0.0.1"], artifact_dir=tmp_path)
    operation = None
    try:
        with bind_request_context(attempt_id="a-live-perf"):
            await shell.create()
        container_id = (await m.list_active())[0]["external_resource_id"]
        assert await shell.heartbeat()
        with pytest.raises(ValueError, match="does not belong"):
            await service.execute_in_container(container_name=shell.container_name, command="echo foreign",
                command_args=["echo", "foreign"], timeout_seconds=10, artifact_dir=tmp_path, context=foreign)
        with pytest.raises(ValueError, match="does not belong"):
            await service.cleanup_container(shell.container_name, context=foreign)
        if scenario == "stateful":
            first = await shell.exec(command="printf saved > /work/state.txt", target="127.0.0.1")
            assert first.exit_code == 0
            await asyncio.sleep(2.2)
            assert len(await m.list_active()) == 1
            assert (await shell.exec(command="cat /work/state.txt", target="127.0.0.1")).stdout == "saved"
            source = tmp_path / "input.txt"
            source.write_text("round-trip", encoding="utf-8")
            await shell.put_file(local_path=source, container_path="inputs/input.txt")
            downloaded = await shell.get_file(container_path="inputs/input.txt", local_path="outputs/copied.txt")
            assert downloaded.read_text(encoding="utf-8") == "round-trip"
            with pytest.raises(ResourceUnavailable):
                await service.create_persistent_container(campaign_id="other", artifact_dir=tmp_path / "other", context=owner)
            await shell.close()
            await shell.close()
        elif scenario == "remote_close":
            remote = SecurityExecutionEnvironmentService(settings=settings,
                docker_resources=DockerResourceManager(settings, lease_manager=m))
            await remote.cleanup_container(shell.container_name, context=owner)
            await remote.cleanup_container(shell.container_name, context=owner)
        elif scenario == "shutdown":
            await service.close()
            with pytest.raises(RuntimeError, match="closed"):
                await service.create_persistent_container(campaign_id="new", artifact_dir=tmp_path, context=owner)
        elif scenario in {"cancel", "timeout", "lost_lease"}:
            command = "echo ready > /work/command-started; echo partial; sleep 300"
            operation = asyncio.create_task(shell.exec(command=command, target="127.0.0.1",
                timeout_seconds=1 if scenario == "timeout" else 30))
            async def wait_command():
                while not (tmp_path / "_security_attack_session_work" / "command-started").exists():
                    assert not operation.done()
                    await asyncio.sleep(.05)
            await asyncio.wait_for(wait_command(), timeout=15)
            if scenario == "cancel":
                operation.cancel()
                with pytest.raises(asyncio.CancelledError): await operation
            elif scenario == "lost_lease":
                async def rejected(*args, **kwargs): return False
                m.renew = rejected
                with pytest.raises(RuntimeError, match="session ended"): await operation
            else:
                result = await operation
                assert result.timed_out and "partial" in result.stdout
        async def wait_retired():
            while not await m.list_cleanup_pending():
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait_retired(), timeout=15)
        assert (await m.usage_snapshot())["global:all"] == 1
        await service.close()
        assert not service._managed_containers and not service._managed_sessions
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
    finally:
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        await service.close()


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["reuse", "concurrent", "cancel_waiter", "timeout", "cancel", "lost_lease", "no_reuse", "cleanup"])
async def test_security_reusable_foreground_lifecycle(manager, tmp_path, scenario):
    m, _ = manager
    settings = _security_foreground_settings()
    settings.security.security_runner_container_reuse = scenario != "no_reuse"
    settings.security.security_runner_docker_cleanup_after_run = scenario == "cleanup"
    service = SecurityExecutionEnvironmentService(settings=settings,
        docker_resources=DockerResourceManager(settings, lease_manager=m))
    owner = ToolExecutionContext(session_id="s-reusable", turn_id="t-reusable", trace_id="trace-reusable",
        user_message="test", normalized_input="test", context_bundle={"project_id": "p-live-security"})
    foreign = replace(owner, session_id="s-reusable-other")
    operations = []

    async def execute(command, context=owner, timeout=20):
        return await service.execute(command=command, command_args=["sh", "-lc", command],
            timeout_seconds=timeout, artifact_dir=tmp_path, context=context)

    async def wait_marker(operation):
        while not list(tmp_path.rglob("command-started")):
            assert not operation.done()
            await asyncio.sleep(.05)

    try:
        with bind_request_context(run_id="r-reusable", run_item_id="i-reusable", attempt_id="a-live-perf"):
            if scenario in {"concurrent", "cancel_waiter"}:
                first = asyncio.create_task(execute("echo ready > command-started; sleep 1; echo first > state.txt; cat state.txt"))
                operations.append(first)
                await asyncio.wait_for(wait_marker(first), timeout=15)
                second = asyncio.create_task(execute("cat state.txt; echo second > state.txt"))
                operations.append(second)
                if scenario == "cancel_waiter":
                    await asyncio.sleep(.05)
                    assert not second.done()
                    second.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await second
                    result = await first
                    assert (await execute("cat state.txt")).stdout.strip() == "first"
                else:
                    result, next_result = await asyncio.gather(first, second)
                    assert result.container_name == next_result.container_name
                    assert next_result.stdout.strip() == "first"
                    assert (await execute("cat state.txt")).stdout.strip() == "second"
            elif scenario in {"timeout", "cancel", "lost_lease"}:
                operation = asyncio.create_task(execute("echo ready > command-started; echo partial; sleep 300",
                    timeout=1 if scenario == "timeout" else 310))
                operations.append(operation)
                await asyncio.wait_for(wait_marker(operation), timeout=15)
                if scenario == "cancel":
                    operation.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await operation
                elif scenario == "lost_lease":
                    async def rejected(*args, **kwargs): return False
                    m.renew = rejected
                    with pytest.raises(RuntimeError, match="session ended"):
                        await operation
                else:
                    result = await operation
                    assert result.timed_out and "partial" in result.stdout
            else:
                result = await execute("echo saved > state.txt; echo stdout-result; echo stderr-detail >&2")
                assert result.exit_code == 0 and "stdout-result" in result.stdout and "stderr-detail" in result.stderr
                assert any(artifact["label"] == "state.txt" for artifact in result.output_artifacts)
                if scenario == "reuse":
                    await asyncio.sleep(2.2)
                    assert len(await m.list_active()) == 1
                    assert (await execute("cat state.txt")).stdout.strip() == "saved"
                    settings.security.security_target_allowlist = "127.0.0.1"
                    rewritten = await service.execute(command="printf http://127.0.0.1/",
                        command_args=["printf", "http://127.0.0.1/"], timeout_seconds=10,
                        artifact_dir=tmp_path, context=owner)
                    assert rewritten.stdout == "http://host.docker.internal/"
                    # One slot is occupied: each changed identity must request a
                    # new lease rather than execute in the existing container.
                    with pytest.raises(ResourceUnavailable):
                        await execute("echo forbidden", foreign)
                    with bind_request_context(attempt_id="a-different"):
                        with pytest.raises(ResourceUnavailable):
                            await execute("echo forbidden")
                elif scenario == "no_reuse":
                    with pytest.raises(ResourceUnavailable):
                        await execute("echo new-container")
            if scenario in {"reuse", "concurrent", "cancel_waiter", "no_reuse"}:
                assert len(await m.list_active()) == 1
                await service.cleanup_container(result.container_name, context=owner)
            await service.close()
            assert not service._managed_containers and not service._managed_sessions and not service._reusable_scopes
            pending = await m.list_cleanup_pending()
            assert len(pending) == 1
            assert (await m.usage_snapshot())["global:all"] == 1
            await _complete_real_cleanup(m)
            inspected = subprocess.run(["docker", "inspect", pending[0]["external_resource_id"]], capture_output=True, timeout=15)
            assert inspected.returncode != 0
    finally:
        for operation in operations:
            if not operation.done():
                operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
        await service.close()


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["unpolled", "partial", "nonzero", "timeout", "lost_lease", "cancel", "settlement_cancel", "shutdown", "immediate_shutdown", "retained", "database_start_failure", "database_result_failure"])
async def test_security_background_lifecycle_and_durable_result(manager, tmp_path, monkeypatch, scenario):
    m, _ = manager
    settings = _security_foreground_settings()
    settings.security.security_runner_detach_poll_interval_seconds = .1
    settings.security.security_runner_docker_cleanup_after_run = scenario != "retained"
    store = PostgresToolJobStore(settings)
    await store.initialize()
    jobs = ToolJobService(store)
    service = SecurityExecutionEnvironmentService(settings=settings, tool_jobs=jobs,
        docker_resources=DockerResourceManager(settings, lease_manager=m))
    owner = ToolExecutionContext(session_id=f"s-background-{uuid4().hex}", turn_id="t-background", trace_id="trace-background",
        user_message="test", normalized_input="test", context_bundle={"project_id": "p-live-security"})
    job_id = ""
    operation = None
    command = "echo ready > command-started; echo partial; echo diagnostic >&2; "
    command += "sleep 1; echo done" if scenario in {"unpolled", "partial", "retained", "database_result_failure", "settlement_cancel"} else "sleep 300"
    if scenario == "nonzero":
        command = "echo ready > command-started; echo partial; exit 7 # preserve shell comments"
    try:
        if scenario == "database_start_failure":
            async def unavailable(*args, **kwargs):
                raise RuntimeError("injected_database_unavailable")
            monkeypatch.setattr(jobs, "create_job", unavailable)
        if scenario == "database_result_failure":
            async def unavailable(*args, **kwargs):
                raise RuntimeError("injected_result_store_unavailable")
            monkeypatch.setattr(jobs, "finish_execution", unavailable)
        if scenario == "settlement_cancel":
            finish = jobs.finish_execution
            async def cancel_before_finish(job_id, **kwargs):
                if kwargs["status"].value == "completed":
                    await jobs.cancel_job(job_id, reason="cancel at settlement boundary")
                return await finish(job_id, **kwargs)
            monkeypatch.setattr(jobs, "finish_execution", cancel_before_finish)
        with bind_request_context(attempt_id="a-live-perf"):
            if scenario == "database_start_failure":
                with pytest.raises(RuntimeError, match="injected_database_unavailable"):
                    await service.execute_detached(command=command, command_args=["sh", "-lc", command],
                        timeout_seconds=30, artifact_dir=tmp_path, context=owner)
                assert not list(tmp_path.rglob("command-started"))
            else:
                started = await service.execute_detached(command=command, command_args=["sh", "-lc", command],
                    timeout_seconds=1 if scenario == "timeout" else 310, artifact_dir=tmp_path, context=owner)
                job_id = started.job_id
                operation = service._detached_jobs[job_id].task
                assert started.is_running
                if scenario == "immediate_shutdown":
                    await service.close()
                else:
                    assert (await jobs.get_job(job_id)).status.value == "running"
                    async def wait_marker():
                        while not list(tmp_path.rglob("command-started")):
                            assert not operation.done()
                            await asyncio.sleep(.05)
                    await asyncio.wait_for(wait_marker(), 15)
                    if scenario == "partial":
                        partial = await service.poll_execution(job_id, context=owner)
                        assert partial.is_running and "partial" in partial.stdout
                        with pytest.raises(ValueError, match="does not belong"):
                            await service.poll_execution(job_id, context=replace(owner, session_id="foreign"))
                        with pytest.raises(ResourceUnavailable):
                            await service.execute_detached(command="echo blocked", command_args=["echo", "blocked"],
                                timeout_seconds=10, artifact_dir=tmp_path / "other", context=owner)
                    elif scenario == "lost_lease":
                        async def rejected(*args, **kwargs): return False
                        m.renew = rejected
                    elif scenario == "cancel":
                        await jobs.cancel_job(job_id, reason="test cancellation")
                    elif scenario == "shutdown":
                        await service.close()
                    if scenario == "database_result_failure":
                        with pytest.raises(RuntimeError, match="injected_result_store_unavailable"):
                            await asyncio.wait_for(operation, 15)
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(sql.SQL("UPDATE {} SET heartbeat_at = now() - interval '10 seconds' WHERE id = %s").format(
                                sql.Identifier(settings.database.postgres_tool_job_table)), (job_id,))
                        assert len(await store.mark_stale_running_jobs(1, job_ids=[job_id])) == 1
                    else:
                        await asyncio.wait_for(operation, 15)
                remote = SecurityExecutionEnvironmentService(settings=settings, tool_jobs=ToolJobService(PostgresToolJobStore(settings)))
                result = await remote.poll_execution(job_id, context=owner)
                assert not result.is_running
                saved = await jobs.get_job(job_id)
                if scenario in {"unpolled", "partial", "retained"}:
                    assert saved.status.value == "completed" and result.exit_code == 0
                    assert "done" in result.stdout and "diagnostic" in result.stdout
                elif scenario in {"cancel", "settlement_cancel", "shutdown", "immediate_shutdown"}:
                    assert saved.status.value == "cancelled" and result.exit_code == 130
                elif scenario == "database_result_failure":
                    assert saved.status.value == "resume_requested" and result.exit_code == 255
                else:
                    assert saved.status.value == "failed" and result.exit_code != 0
                    assert result.timed_out == (scenario == "timeout")
                if scenario == "retained":
                    assert len(await m.list_active()) == 1
                    assert not await m.list_cleanup_pending()
                    await service.cleanup_container(started.container_name, context=owner)
        await service.close()
        assert not service._detached_jobs and not service._managed_sessions
        pending = await m.list_cleanup_pending()
        assert len(pending) == 1
        assert (await m.usage_snapshot())["global:all"] == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", pending[0]["external_resource_id"]], capture_output=True, timeout=15)
        assert inspected.returncode != 0
    finally:
        await service.close()
        if job_id:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(
                    sql.Identifier(settings.database.postgres_tool_job_table)), (job_id,))


def _run_security_background_until_killed(prefix, stream, artifact_dir, identity):
    async def run():
        settings = _security_foreground_settings()
        settings.security.security_runner_detach_poll_interval_seconds = .1
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, cleanup_stream=stream)
        service = SecurityExecutionEnvironmentService(settings=settings,
            docker_resources=DockerResourceManager(settings, lease_manager=manager))
        with bind_request_context(session_id="s-background-kill", attempt_id="a-live-perf"):
            result = await service.execute_detached(command="echo ready > command-started; sleep 300",
                command_args=["sh", "-lc", "sleep 300"], timeout_seconds=310, artifact_dir=artifact_dir)
        identity.put(result.job_id)
        await asyncio.Event().wait()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
async def test_security_background_worker_kill_preserves_recovery_and_releases_container(manager, tmp_path):
    m, _ = manager
    settings = _security_foreground_settings()
    store = PostgresToolJobStore(settings)
    await store.initialize()
    context = multiprocessing.get_context("spawn")
    identity = context.Queue()
    process = context.Process(target=_run_security_background_until_killed,
        args=(m.namespace, m._cleanup_stream, tmp_path, identity))
    job_id = container_id = ""
    try:
        process.start()
        job_id = await asyncio.to_thread(identity.get, True, 30)
        container_id = await _wait_for_running_container(m)
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive()
        await asyncio.sleep(2.2)
        assert await ResourceCleanupWorker(m).run_once() == 1
        await _complete_real_cleanup(m)
        assert (await store.get_job(job_id)).status.value == "running"
        recovered = await store.mark_stale_running_jobs(1, job_ids=[job_id])
        assert [job.id for job in recovered] == [job_id]
        remote = SecurityExecutionEnvironmentService(settings=settings)
        with bind_request_context(session_id="s-background-kill"):
            result = await remote.poll_execution(job_id)
        assert not result.is_running and result.exit_code == 255
        assert "recovery" in result.stderr
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if process.is_alive(): process.terminate()
        await asyncio.to_thread(process.join, 10)
        identity.close()
        identity.join_thread()
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)
        if job_id:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(
                    sql.Identifier(settings.database.postgres_tool_job_table)), (job_id,))


def _run_security_persistent_until_killed(prefix, stream, artifact_dir, ready):
    async def run():
        settings = _security_foreground_settings()
        manager = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, cleanup_stream=stream)
        service = SecurityExecutionEnvironmentService(settings=settings,
            docker_resources=DockerResourceManager(settings, lease_manager=manager))
        with bind_request_context(session_id="s-persistent-kill", attempt_id="a-live-perf"):
            await service.create_persistent_container(campaign_id="persistent-kill", artifact_dir=artifact_dir)
        ready.set()
        await asyncio.Event().wait()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires live Kali Docker image")
@pytest.mark.asyncio
async def test_security_persistent_worker_kill_recovered_by_cleanup(manager, tmp_path):
    m, _ = manager
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_run_security_persistent_until_killed,
        args=(m.namespace, m._cleanup_stream, tmp_path, ready))
    container_id = ""
    try:
        process.start()
        async def wait_ready():
            while not ready.is_set():
                assert process.is_alive()
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait_ready(), timeout=30)
        container_id = await _wait_for_running_container(m)
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive()
        await asyncio.sleep(2.2)
        assert await ResourceCleanupWorker(m).run_once() == 1
        await _complete_real_cleanup(m)
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=15)
        assert inspected.returncode != 0
        container_id = ""
    finally:
        if process.is_alive(): process.terminate()
        await asyncio.to_thread(process.join, 10)
        if container_id:
            subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=15)


def _cleanup_then_wait_after_pg_commit(stream, group, prefix, committed):
    """Real worker paused at the PG/Redis boundary; parent terminates this process."""
    async def run():
        settings = get_settings()
        manager = RedisResourceLeaseManager(settings.database.redis_url,
                                            cleanup_stream=stream, key_prefix=prefix)
        queue = RedisTaskQueue(settings.database.redis_url, stream=stream, group=group)
        store = PostgresResourceCleanupStore(settings)
        await manager.connect()
        await queue.connect()
        finish = store.finish
        async def finish_and_wait(*args, **kwargs):
            await finish(*args, **kwargs)
            if kwargs["status"] == "completed":
                committed.set()
                await asyncio.Event().wait()
        store.finish = finish_and_wait
        worker = RedisTaskWorker(queue, consumer="terminated-cleanup-worker", block_ms=0,
            handler=ResourceCleanupTaskHandler(store, build_cleanup_callbacks(settings), manager=manager))
        try:
            await worker.run_once()
        finally:
            await queue.close()
            await manager.close()
    asyncio.run(run())


@pytest_asyncio.fixture
async def manager():
    suffix = uuid4().hex
    cleanup_stream = f"qa:test:cleanup:{suffix}"
    manager = RedisResourceLeaseManager(
        get_settings().database.redis_url,
        cleanup_stream=cleanup_stream,
        key_prefix=f"qa:test:lease:{suffix}",
    )
    await manager.connect()
    if os.environ.get("RUN_LIVE_DOCKER_TESTS") == "1":
        await PostgresResourceAllocationStore(get_settings()).initialize()
    resource_id = f"browser-{suffix}"
    await manager.configure_quota(scope="global", identifier="all", limit=1)
    await manager.configure_quota(scope="resource_type", identifier="browser", limit=1)
    try:
        yield manager, resource_id
    finally:
        await _clear_test_namespace(manager, cleanup_stream)
        await manager.close()
        if os.environ.get("RUN_LIVE_DOCKER_TESTS") == "1":
            settings = get_settings()
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE lease_namespace = %s").format(
                    sql.Identifier(settings.database.postgres_resource_allocation_table)), (manager.namespace,))


async def _clear_test_namespace(manager, stream):
    assert manager._prefix.startswith("qa:test:lease:")
    keys = [key async for key in manager._client.scan_iter(match=manager._key("*"))]
    await manager._client.delete(stream, f"{stream}:dead_letter", *keys)


@pytest.mark.asyncio
async def test_only_one_concurrent_worker_claims_resource(manager):
    lease_manager, resource_id = manager
    async def claim(worker):
        try:
            return await lease_manager.acquire(resource_type="browser", resource_id=resource_id,
                                               worker_id=worker, lease_seconds=10, project_id="p1", run_id="r1")
        except ResourceUnavailable as exc:
            return exc.reason
    results = await asyncio.gather(*(claim(f"worker-{i}") for i in range(12)))
    leases = [result for result in results if not isinstance(result, str)]
    assert len(leases) == 1 and sum(result == "resource_busy" for result in results) == 11
    assert await lease_manager.release(leases[0])


@pytest.mark.asyncio
async def test_fencing_and_owner_release_are_monotonic(manager):
    lease_manager, resource_id = manager
    first = await lease_manager.acquire(resource_type="docker", resource_id=resource_id,
                                        worker_id="w1", lease_seconds=10)
    assert not await lease_manager.release(first.__class__(**{**first.__dict__, "lease_token": "wrong"}))
    assert await lease_manager.release(first)
    second = await lease_manager.acquire(resource_type="docker", resource_id=resource_id,
                                         worker_id="w2", lease_seconds=10)
    assert second.fencing_token > first.fencing_token
    assert await lease_manager.release(second)


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(manager):
    lease_manager, resource_id = manager
    first = await lease_manager.acquire(resource_type="browser", resource_id=resource_id,
                                        worker_id="w1", lease_seconds=1)
    await asyncio.sleep(1.1)
    second = await lease_manager.acquire(resource_type="browser", resource_id=resource_id,
                                         worker_id="w2", lease_seconds=10)
    assert second.fencing_token > first.fencing_token
    assert await lease_manager.release(second)


@pytest.mark.asyncio
async def test_renew_extends_live_resource_ttl_and_rejects_stale_token(manager):
    lease_manager, resource_id = manager
    lease = await lease_manager.acquire(
        resource_type="browser",
        resource_id=resource_id,
        worker_id="w1",
        lease_seconds=1,
    )
    assert await lease_manager.renew(lease, lease_seconds=3)
    assert await lease_manager._client.pttl(
        lease_manager._lease_key("browser", resource_id)
    ) > 1500

    stale = lease.__class__(**{**lease.__dict__, "lease_token": "stale-token"})
    assert not await lease_manager.renew(stale, lease_seconds=3)
    assert await lease_manager.release(lease)


async def _expire(manager, lease):
    # Deterministic fault injection: reproduce Redis TTL removal and a due registry entry.
    key = manager._lease_key(lease.resource_type, lease.resource_id)
    await manager._client.delete(key)
    await manager._client.zadd(manager._key("lease:registry"), {key: 0})


@pytest.mark.asyncio
async def test_expired_bound_resource_holds_slot_and_quota_until_cleanup(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="old", lease_seconds=10)
    await m.bind(lease, "a" * 64)
    await _expire(m, lease)
    await m.reap_expired()
    assert (await m.usage_snapshot()).get("global:all") == 1
    with pytest.raises(ResourceUnavailable):
        await m.acquire(resource_type="docker", resource_id=resource_id,
                        worker_id="new", lease_seconds=10)
    with pytest.raises(ResourceUnavailable):
        await m.acquire(resource_type="docker", resource_id="another-slot",
                        worker_id="new", lease_seconds=10)


@pytest.mark.asyncio
async def test_execution_finalizer_hands_bound_resource_to_cleanup(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10,
                            project_id="p1", run_id="r1", attempt_id="a1")
    assert await m.bind(lease, "a" * 64)
    service = ExecutionService(run_service=object(), test_case_service=object(), adapter=object())
    service.set_resource_lease_manager(m)
    await service._release_execution_resources([lease])

    assert (await m.usage_snapshot()).get("global:all") == 1
    assert await m.get(resource_type="docker", resource_id=resource_id) is None
    assert len(await m.list_cleanup_pending()) == 1
    messages = await m._client.xrange(m._cleanup_stream)
    assert len(messages) == 1
    retired = ExpiredResourceLease.from_payload(json.loads(messages[0][1]["payload"]))
    assert retired.external_resource_id == "a" * 64
    assert retired.attempt_id == "a1"
    with pytest.raises(ResourceUnavailable):
        await m.acquire(resource_type="docker", resource_id=resource_id,
                        worker_id="next", lease_seconds=10)
    assert await m.complete_cleanup(retired)
    assert not await m.usage_snapshot()


@pytest.mark.asyncio
async def test_live_binding_cannot_overwrite_another_external_resource(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10)
    assert await m.bind(lease, "a" * 64)
    assert await m.bind(lease, "a" * 64)
    assert not await m.bind(replace(lease, fencing_token=lease.fencing_token + 1), "a" * 64)
    assert not await m.bind(lease, "b" * 64)
    assert (await m.get(resource_type="docker", resource_id=resource_id))["external_resource_id"] == "a" * 64


@pytest.mark.asyncio
async def test_prepared_allocation_keeps_quota_without_a_container_id(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id, worker_id="w", lease_seconds=10)
    allocation_id = uuid4().hex
    assert not await m.prepare_allocation(replace(lease, fencing_token=999), allocation_id)
    assert await m.prepare_allocation(lease, allocation_id)
    assert not await m.prepare_allocation(lease, uuid4().hex)
    assert await m.retire(lease)
    assert (await m.usage_snapshot())["global:all"] == 1
    messages = await m._client.xrange(m._cleanup_stream)
    retired = ExpiredResourceLease.from_payload(json.loads(messages[0][1]["payload"]))
    assert retired.allocation_id == allocation_id and retired.external_resource_id is None
    assert not await m.complete_cleanup(replace(retired, allocation_id=uuid4().hex))
    assert await m.complete_cleanup(retired)
    assert not await m.usage_snapshot()


@pytest.mark.asyncio
async def test_retire_publish_failure_keeps_owner_then_retries_idempotently(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10, project_id="p1", run_id="r1")
    assert await m.bind(lease, "a" * 64)
    before = await m.usage_snapshot()
    await m._client.set(m._cleanup_stream, "wrong-type")
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await m.retire(lease)
    assert (await m.get(resource_type="docker", resource_id=resource_id))["external_resource_id"] == "a" * 64
    assert await m.usage_snapshot() == before
    assert not await m.list_cleanup_pending()
    await m._client.delete(m._cleanup_stream)

    assert not await m.retire(replace(lease, lease_token="stale"))
    assert not await m.retire(replace(lease, fencing_token=lease.fencing_token + 1))
    assert await m._client.xlen(m._cleanup_stream) == 0
    assert all(await asyncio.gather(*(m.retire(lease) for _ in range(12))))
    assert not await m.renew(lease, lease_seconds=30)
    assert not await m.reap_expired()
    assert await m._client.xlen(m._cleanup_stream) == 1
    assert await m.usage_snapshot() == before
    pending = await m.list_cleanup_pending()
    assert pending[0]["cleanup_reason"] == "execution_finished"


@pytest.mark.asyncio
async def test_retire_logical_resources_uses_persisted_quota_scope(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="test_account", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10, project_id="p1", run_id="r1")
    assert await m.retire(replace(lease, project_id="not-the-owner-project", run_id=None))
    assert not await m.usage_snapshot()
    assert not await m.retire(lease)
    assert not await m.list_cleanup_pending()
    assert await m._client.xlen(m._cleanup_stream) == 0


@pytest.mark.asyncio
async def test_retire_after_expiry_preserves_cleanup_before_slot_reuse(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10)
    assert await m.bind(lease, "a" * 64)
    await _expire(m, lease)
    assert await m.retire(lease)
    assert not await m.reap_expired()
    assert await m._client.xlen(m._cleanup_stream) == 1
    assert (await m.usage_snapshot())["global:all"] == 1


@pytest.mark.asyncio
async def test_reap_publish_failure_preserves_binding_and_quota(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="worker-1", lease_seconds=10)
    assert await m.bind(lease, "a" * 64)
    await _expire(m, lease)
    client = m._client
    await client.set(m._cleanup_stream, "wrong-type")
    before = await m.usage_snapshot()
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await m.reap_expired()
    key = m._lease_key("docker", resource_id)
    assert await client.hget(m._key("lease:metadata"), key)
    assert await m.usage_snapshot() == before
    await client.delete(m._cleanup_stream)
    expired = await m.reap_expired()
    assert expired[0].external_resource_id == "a" * 64
    assert expired[0].fencing_token == lease.fencing_token
    assert not await m.reap_expired()
    assert await client.xlen(m._cleanup_stream) == 1
    assert await m.usage_snapshot() == before
    assert await m.complete_cleanup(expired[0])
    assert not await m.complete_cleanup(expired[0])
    assert not await m.usage_snapshot()


@pytest.mark.asyncio
async def test_claim_reap_preserves_old_binding_and_per_lease_fencing(manager):
    m, resource_id = manager
    first = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="old", lease_seconds=10)
    await m.bind(first, "a" * 64)
    await _expire(m, first)
    with pytest.raises(ResourceUnavailable, match="resource_pending_reap"):
        await m.acquire(resource_type="docker", resource_id=resource_id,
                        worker_id="new", lease_seconds=10)
    messages = await m._client.xrange(m._cleanup_stream)
    payload = json.loads(messages[0][1]["payload"])
    assert payload["external_resource_id"] == "a" * 64
    assert payload["fencing_token"] == first.fencing_token
    assert not await m.bind(first, "b" * 64)
    expired = ExpiredResourceLease.from_payload(payload)
    assert not await m.complete_cleanup(replace(expired, fencing_token=999))
    assert not await m.complete_cleanup(replace(expired, lease_token="wrong"))
    assert not await m.complete_cleanup(replace(expired, external_resource_id="b" * 64))
    assert (await m.usage_snapshot())["global:all"] == 1
    pending = await m.list_cleanup_pending()
    assert len(pending) == 1 and "lease_token" not in pending[0]
    await m.reconcile_quota_usage()
    assert (await m.usage_snapshot())["global:all"] == 1
    assert await m.complete_cleanup(expired)
    second = await m.acquire(resource_type="docker", resource_id=resource_id,
                             worker_id="new", lease_seconds=10)
    assert not await m.complete_cleanup(expired)
    assert (await m.usage_snapshot())["global:all"] == 1
    assert (await m.list_active())[0]["fencing_token"] == second.fencing_token
    assert await m.release(second)


@pytest.mark.asyncio
async def test_quota_reconciliation_counts_active_and_unreaped_owners(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="browser", resource_id=resource_id,
                            worker_id="owner", lease_seconds=10, project_id="p1")
    await m._client.hset(m._key("quota:usage"), mapping={"global:all": 99, "project:orphan": 9})
    await m.reconcile_quota_usage()
    usage = await m.usage_snapshot()
    assert usage["global:all"] == 1 and "project:orphan" not in usage
    await _expire(m, lease)
    await m.reconcile_quota_usage()
    assert (await m.usage_snapshot())["global:all"] == 1
    await m.reap_expired()
    await m.reconcile_quota_usage()
    assert not await m.usage_snapshot()


@pytest.mark.asyncio
async def test_cleanup_pending_recovery_retry_and_dead_letter(manager):
    m, resource_id = manager
    lease = await m.acquire(resource_type="docker", resource_id=resource_id,
                            worker_id="creator", lease_seconds=10)
    await m.bind(lease, "a" * 64)
    await _expire(m, lease)
    await m.reap_expired()
    queue = RedisTaskQueue(get_settings().database.redis_url,
                           stream=m._cleanup_stream, group="cleanup-test")
    await queue.connect()
    try:
        # Delivery disappears with its consumer before ACK; another worker must reclaim it.
        assert len(await queue.consume(consumer="crashed", block_ms=0)) == 1
        store = ReceiptStore()
        async def unavailable(_lease):
            raise ConnectionError("Docker daemon unavailable")
        worker = RedisTaskWorker(queue, consumer="replacement", block_ms=0,
            handler=ResourceCleanupTaskHandler(store, {"docker": unavailable}, manager=m),
            reclaim_idle_ms=10, retry_base_ms=1, retry_max_ms=10, max_retries=1)
        await asyncio.sleep(.03)
        await worker.run_once()
        for _ in range(10):
            await asyncio.sleep(.03)
            await worker.run_once()
            if await queue._client.xlen(f"{queue.stream}:dead_letter"):
                break
        assert await queue._client.xlen(f"{queue.stream}:dead_letter") == 1
        assert not await queue.pending()
        assert store.statuses == ["failed", "failed"]
        assert (await m.usage_snapshot())["global:all"] == 1
        assert len(await m.list_cleanup_pending()) == 1
    finally:
        await queue.close()


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1",
    reason="set RUN_LIVE_DOCKER_TESTS=1 to exercise real Redis and Docker cleanup",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("kill_after_pg_commit", [False, True])
@pytest.mark.parametrize("retirement", ["expiry", "execution_finished"])
async def test_docker_slot_removes_bound_real_container(kill_after_pg_commit, retirement) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    settings = get_settings()
    suffix = uuid4().hex[:12]
    container_name = f"qa-cleanup-{suffix}"
    cleanup_stream = f"qa:test:cleanup:{suffix}"
    group = f"qa:test:cleanup-workers:{suffix}"
    container_id = ""
    task_id = None
    process = None
    store = PostgresResourceCleanupStore(settings)
    lease_manager = RedisResourceLeaseManager(
        settings.database.redis_url,
        cleanup_stream=cleanup_stream,
        key_prefix=f"qa:test:lease:{suffix}",
    )
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=cleanup_stream,
        group=group,
    )
    try:
        await store.initialize()
        created = subprocess.run(
            [
                "docker", "run", "--detach", "--name", container_name,
                "--label", "enterprise-ai-qa-agent=managed",
                "busybox", "sleep", "300",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=60,
        )
        container_id = created.stdout.strip()
        assert container_id

        await lease_manager.connect()
        await queue.connect()
        lease = await lease_manager.acquire(
            resource_type="docker",
            resource_id=f"docker-slot-{suffix}",
            worker_id=f"worker-{suffix}",
            lease_seconds=1 if retirement == "expiry" else 60,
            project_id=f"project-{suffix}",
            session_id=f"session-{suffix}",
            run_id=f"run-{suffix}",
            run_item_id=f"item-{suffix}",
            attempt_id=f"attempt-{suffix}",
        )
        assert await lease_manager.bind(lease, container_id)
        task_id = f"task_cleanup_{lease.lease_token}"
        if retirement == "expiry":
            await asyncio.sleep(1.2)
            reaper = ResourceCleanupWorker(lease_manager, interval_seconds=1)
            assert await reaper.run_once() == 1
        else:
            service = ExecutionService(run_service=object(), test_case_service=object(), adapter=object())
            service.set_resource_lease_manager(lease_manager)
            await service._release_execution_resources([lease])
            assert len(await lease_manager.list_cleanup_pending()) == 1
        assert (await lease_manager.usage_snapshot())["global:all"] == 1
        if kill_after_pg_commit:
            context = multiprocessing.get_context("spawn")
            committed = context.Event()
            process = context.Process(target=_cleanup_then_wait_after_pg_commit,
                args=(cleanup_stream, group, lease_manager._prefix, committed))
            process.start()
            async def wait_for_commit():
                while not committed.is_set():
                    assert process.is_alive(), f"child exited: {process.exitcode}"
                    await asyncio.sleep(.05)
            await asyncio.wait_for(wait_for_commit(), timeout=40)
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive()
            assert (await lease_manager.usage_snapshot())["global:all"] == 1
            assert len(await queue.pending()) == 1
            await asyncio.sleep(.05)
        callbacks = build_cleanup_callbacks(settings)
        if kill_after_pg_commit:
            async def unexpected_cleanup(_lease):
                raise AssertionError("Committed cleanup must not invoke Docker again")
            callbacks = {"docker": unexpected_cleanup}
        delivery = RedisTaskWorker(
            queue,
            consumer=f"cleanup-{suffix}",
            handler=ResourceCleanupTaskHandler(store, callbacks, manager=lease_manager),
            block_ms=0,
            reclaim_idle_ms=10,
        )
        assert await delivery.run_once() == 1
        assert not await queue.pending()
        assert not await lease_manager.list_cleanup_pending()
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT status FROM {} WHERE task_id = %s").format(
                sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_id,))
            assert cur.fetchone()["status"] == "completed"

        inspected = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        assert inspected.returncode != 0
        container_id = ""
        usage = await lease_manager.usage_snapshot()
        assert not any(suffix in key for key in usage)
    finally:
        if process is not None:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 10)
        if container_id:
            subprocess.run(
                ["docker", "rm", "--force", container_id],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
        if lease_manager._client is not None:
            await _clear_test_namespace(lease_manager, cleanup_stream)
        await queue.close()
        await lease_manager.close()
        if task_id:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE task_id = %s").format(
                    sql.Identifier(settings.database.postgres_resource_cleanup_table)), (task_id,))

"""Real source archives and Worker ports; not independent dependency installs."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.framework_release_archive import archive_release, restore_release
from src.schemas.runtime_binding import RuntimeBinding
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_model_reader_admission import database_state
from tests.test_framework_release_binding import isolate_recovery_stores


BASELINE = "b6646d89c83bd4cc9e45fc2b8bdca4499a407039"
READER = "d3b6ad24eb17187bb293bf35e6a99740b8ab1bd0"


@pytest.fixture(scope="module")
def releases(tmp_path_factory):
    project = Path(__file__).resolve().parents[2]
    store = tmp_path_factory.mktemp("source-releases")
    versions = {}
    # These exact committed sources are intentional regression inputs. A
    # shallow checkout must fetch these objects rather than skip the scenario.
    for name, revision in (("old", BASELINE), ("reader", READER)):
        reference = archive_release(project, store, revision=revision)
        destination = store / (name + "-source")
        manifest = restore_release(store, reference, destination)
        versions[name] = (destination, reference, manifest)
    return store, versions


WORKER = r'''
import asyncio, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "Agent_Server"))

async def work():
    from types import SimpleNamespace
    from src.core.config import Settings
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.task_deferred import TaskDeferred
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher

    data = json.loads(sys.stdin.buffer.read())
    settings = Settings.model_validate(data["settings"])
    assert settings.deep_agents.checkpoint_schema == data["prefix"] + "_checkpoints"
    assert settings.database.postgres_agent_task_table.startswith("agent_test_")
    assert settings.orchestration.agent_task_v2_stream.startswith(data["prefix"] + ":")
    available = settings.deep_agents.enabled and settings.deep_agents.checkpoint_enabled
    caps = (FrameworkWorkerCapabilities.from_artifact(deepagents_available=available) if data["old"]
        else FrameworkWorkerCapabilities.from_release(pathlib.Path(data["store"]), data["reference"],
            deepagents_available=available))
    sessions = PostgresSessionStore(settings)
    tasks = PostgresAgentTaskStore(settings, sessions)
    result = {"executions": 0, "seen": 0, "reasons": []}

    def require_source():
        loaded = [pathlib.Path(module.__file__).resolve() for name, module in sys.modules.items()
            if name.startswith("src.") and getattr(module, "__file__", None)]
        assert loaded and all(path.is_relative_to(root) for path in loaded)
        result["source_modules_inside_release"] = True

    require_source()
    if data["initialize"]:
        try:
            await tasks.initialize()
        except Exception as exc:
            result["initialization_error_type"] = type(exc).__name__
            return result
        result["initialized"] = True
        if data["initialize_only"]:
            return result

    async def require_session(session_id):
        session = await sessions.get_session(session_id)
        assert session is not None
        return session

    dispatcher = AgentTaskDispatcher(settings, tasks, SimpleNamespace(_require_session=require_session), None,
        framework_capabilities=caps)

    async def execute(task):
        # Explicit controlled terminal, not a fake application result or graph.
        # Real claim/owner/finish remain enforced by the archived task store.
        result["executions"] += 1
        await tasks.finish(task, status="cancelled", error_code="source_release_contract_probe")

    dispatcher._execute = execute
    queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
        group="source-releases")
    await queue.connect()

    async def handle(item):
        result["seen"] += 1
        result["message_id"] = item.message_id
        result["retry_count"] = item.retry_count
        try:
            await dispatcher.handle_v2(item, worker_id=data["consumer"])
        except TaskDeferred as exc:
            result["reasons"].append(str(exc))
            raise

    try:
        result["processed"] = await RedisTaskWorker(queue, consumer=data["consumer"], handler=handle,
            block_ms=1, reclaim_idle_ms=1, max_retries=0).run_once()
    finally:
        await queue.close()
    require_source()
    return result

try:
    print(json.dumps(asyncio.run(work()), sort_keys=True))
except BaseException as exc:
    # Settings travel only through stdin. Never emit private validation input,
    # command arguments, credentials or tracebacks from a failed child.
    import traceback
    frames = [{"file": pathlib.Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(exc.__traceback__)]
    known_errors = {"Event loop is closed", "aclose(): asynchronous generator is already running",
        "cannot reuse already awaited coroutine", "Pending delivery belongs to another consumer"}
    print("source_release_worker_failed " + json.dumps({"error_type": type(exc).__name__, "frames": frames,
        "known_error": str(exc) if str(exc) in known_errors else None}), file=sys.stderr)
    raise SystemExit(1) from None
'''


async def run_worker(h, releases, name, *, consumer, initialize=False, initialize_only=False):
    store, versions = releases
    source, reference, _ = versions[name]
    data = {"settings": h.settings.model_dump(mode="json"), "prefix": h.prefix, "store": str(store),
        "reference": reference.model_dump(mode="json"), "old": name == "old", "consumer": consumer,
        "initialize": initialize, "initialize_only": initialize_only}
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-I", "-B", "-c", WORKER, str(source)], input=json.dumps(data).encode("utf-8"),
        cwd=source, capture_output=True, timeout=180)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    observed = json.loads(result.stdout)
    assert observed["source_modules_inside_release"]
    return observed


def pin(h, releases, name="reader"):
    _, versions = releases
    _, reference, manifest = versions[name]
    h.settings.deep_agents.enabled = True
    h.settings.deep_agents.checkpoint_enabled = True
    h.binding = RuntimeBinding.model_validate({**h.binding.model_dump(mode="json", exclude={"fingerprint"}),
        "release_reference": reference.model_dump(mode="json"),
        "dependency_lock_sha256": manifest.environment["worker_capabilities"]["dependency_lock_sha256"]})
    h.command = h.command.model_copy(update={"runtime_binding": h.binding})


@pytest.mark.asyncio
async def test_archived_old_consumer_defers_and_verified_reader_finishes_same_delivery_once(
        admission, isolate_recovery_stores, releases):
    h = admission
    pin(h, releases)
    task = await submit(h)
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="source-releases")
    await queue.connect()
    try:
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        before, entries = database_state(h), await h.redis.xrange(queue.stream)
        old = await run_worker(h, releases, "old", consumer="archived-old")
        assert old["processed"] == old["executions"] == 0 and old["seen"] == 1
        assert old["reasons"] == ["framework_command_format_unavailable"] and old["retry_count"] == 0
        assert old["message_id"] == entries[0][0] and database_state(h) == before
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xrange(queue.stream) == entries and await h.redis.xlen(queue.stream + ":dead_letter") == 0

        reader = await run_worker(h, releases, "reader", consumer="archived-reader", initialize=True)
        assert reader.get("initialized") is True and "initialization_error_type" not in reader
        assert reader["processed"] == reader["executions"] == reader["seen"] == 1 and reader["reasons"] == []
        assert reader["message_id"] == old["message_id"] and reader["retry_count"] == 0
        loaded = await h.tasks.get(task.id)
        assert loaded.status == "cancelled" and loaded.attempt_no == 1 and loaded.worker_id == "archived-reader"
        assert loaded.runtime_binding == h.binding and not await queue.pending()
        settled = database_state(h)
        await queue.enqueue(h.command.model_dump(mode="json"))
        duplicate = await run_worker(h, releases, "reader", consumer="archived-reader-duplicate")
        assert duplicate["processed"] == 1 and duplicate["executions"] == 0 and duplicate["seen"] == 1
        assert database_state(h) == settled and not await queue.pending()
        ledger = await h.calls.snapshot(h.binding)
        assert ledger.calls == [] and ledger.reserved_calls == 0
        assert h.runtime.calls == [] and await h.redis.xlen(queue.stream + ":dead_letter") == 0
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_archived_current_reader_cannot_claim_another_artifact_with_identical_runtime_and_lock(
        admission, isolate_recovery_stores, releases):
    h = admission
    pin(h, releases, "old")
    _, versions = releases
    old_caps = versions["old"][2].environment["worker_capabilities"]
    reader_caps = versions["reader"][2].environment["worker_capabilities"]
    assert old_caps["runtime_version"] == reader_caps["runtime_version"] == h.binding.runtime_version
    assert old_caps["dependency_lock_sha256"] == reader_caps["dependency_lock_sha256"] == h.binding.dependency_lock_sha256
    assert versions["old"][1] != versions["reader"][1]
    await submit(h)
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="source-releases")
    await queue.connect()
    try:
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        before, entries = database_state(h), await h.redis.xrange(queue.stream)
        actual = await run_worker(h, releases, "reader", consumer="different-archive")
        assert actual["processed"] == actual["executions"] == 0 and actual["seen"] == 1
        assert actual["reasons"] == ["framework_release_artifact_unavailable"]
        assert actual["message_id"] == entries[0][0] and actual["retry_count"] == 0
        assert database_state(h) == before and h.runtime.calls == []
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xrange(queue.stream) == entries and await h.redis.xlen(queue.stream + ":dead_letter") == 0
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_old_startup_rejects_pinned_binding_but_upgraded_reader_initializes_without_rewrite(
        admission, isolate_recovery_stores, releases):
    h = admission
    pin(h, releases)
    await submit(h)
    before = database_state(h)
    old = await run_worker(h, releases, "old", consumer="old-startup", initialize=True, initialize_only=True)
    assert old.get("initialization_error_type") == "ValidationError" and "initialized" not in old
    assert database_state(h) == before
    new = await run_worker(h, releases, "reader", consumer="upgraded-startup", initialize=True, initialize_only=True)
    assert new.get("initialized") is True and "initialization_error_type" not in new
    assert database_state(h) == before and h.runtime.calls == []


@pytest.mark.asyncio
async def test_unpinned_historical_binding_initializes_and_completes_with_old_source_then_acks_with_reader(
        admission, isolate_recovery_stores, releases):
    h = admission
    _, versions = releases
    lock = versions["old"][2].environment["worker_capabilities"]["dependency_lock_sha256"]
    h.settings.deep_agents.enabled = True
    h.settings.deep_agents.checkpoint_enabled = True
    h.binding = RuntimeBinding.model_validate({**h.binding.model_dump(mode="json", exclude={"fingerprint"}),
        "dependency_lock_sha256": lock})
    assert h.binding.release_reference is None and "release_reference" not in h.binding.model_dump(mode="json")
    h.command = h.command.model_copy(update={"runtime_binding": h.binding})
    task = await submit(h)
    before = database_state(h)
    initialized = await run_worker(h, releases, "old", consumer="historical-startup",
        initialize=True, initialize_only=True)
    assert initialized.get("initialized") is True and "initialization_error_type" not in initialized
    assert database_state(h) == before
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="source-releases")
    await queue.connect()
    try:
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        old = await run_worker(h, releases, "old", consumer="historical-old-source")
        assert old["processed"] == old["executions"] == old["seen"] == 1 and old["reasons"] == []
        saved = await h.tasks.get(task.id)
        assert saved.status == "cancelled" and saved.attempt_no == 1 and saved.runtime_binding == h.binding
        assert not await queue.pending()
        settled = database_state(h)
        await queue.enqueue(h.command.model_dump(mode="json"))
        reader = await run_worker(h, releases, "reader", consumer="historical-upgraded-source")
        assert reader["processed"] == reader["seen"] == 1 and reader["executions"] == 0
        assert database_state(h) == settled and not await queue.pending()
        assert h.runtime.calls == [] and await h.redis.xlen(queue.stream + ":dead_letter") == 0
    finally:
        await queue.close()

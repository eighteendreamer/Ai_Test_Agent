"""Existing Worker -> original formal graph/result transaction, no production writer."""
import asyncio
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.redis_task_worker import RedisTaskWorker
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_execution_lease import delivery, capabilities
from tests.test_controlled_case_settlement_dispatch import queue_for, worker
from tests.test_framework_chat_models import TRANSPORTS


DISPATCH = {"dispatch": True, "auto": True, "settle": True}
dispatch_case = pytest.mark.parametrize("assessment_case", [DISPATCH], indirect=True)


def compose(p, *, caps=None, port=True):
    return AgentTaskDispatcher(p.s.h.settings, p.s.h.tasks, p.s.h.sessions, p.s.h.resources,
        framework_capabilities=caps or capabilities(), controlled_case_runtime=p.runtime if port else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{**DISPATCH, "transport": t} for t in TRANSPORTS], indirect=True)
async def test_existing_worker_executes_formal_original_once_and_duplicate_ack_needs_no_writer(assessment_case, monkeypatch):
    p = assessment_case
    dispatcher = compose(p)
    raw = delivery(p.s)
    queue = await queue_for(p, raw)
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("formal dispatch cannot use generic claim/finish or replay a settled graph")
    monkeypatch.setattr(p.s.h.tasks, "claim", forbidden)
    monkeypatch.setattr(p.s.h.tasks, "finish", forbidden)
    assert await worker(p, dispatcher, queue, "formal-worker").run_once() == 1
    saved = await p.s.d.runs.get_run(p.s.detail.run.id)
    assert len(saved.results) == 1 and saved.results[0].status == "passed"
    item = next(i for i in saved.items if i.id == p.s.item.id)
    attempt = next(a for a in saved.attempts if a.id == p.s.attempt.id)
    assert item.status == attempt.status == "passed" and attempt.id == p.s.attempt.id
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "completed"
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 0
    assert await p.s.h.resources.list_active() == []
    assert len(p.requests) == 2 and len(p.effects) == 1 and forbidden_calls == []
    before = database_digest(p.s)
    dispatcher.set_framework_capabilities(replace(capabilities(), controlled_case_available=False))
    for name in ("claim_controlled_case", "begin"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    monkeypatch.setattr(p.executor, "execute", forbidden)
    await queue.enqueue(raw)
    assert await worker(p, dispatcher, queue, "read-only-formal-worker").run_once() == 1
    assert database_digest(p.s) == before and forbidden_calls == []
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@dispatch_case
async def test_resource_wait_creates_new_agent_attempt_not_new_formal_execution(assessment_case):
    p = assessment_case
    original_attempts = [a.id for a in (await p.s.d.runs.get_run(p.s.detail.run.id)).attempts]
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    occupied = await p.s.h.resources.acquire(resource_type="agent", resource_id="isolated-formal-occupied",
        worker_id="isolated-occupant", lease_seconds=120)
    try:
        assert await worker(p, dispatcher, queue).run_once() == 0
        current = await p.s.h.tasks.get(p.h.task.id)
        assert current.status == "waiting_resource" and current.attempt_no == 1
        assert p.requests == p.effects == []
        assert await p.s.d.runs.get_item(p.s.item.id) == p.s.item
        session = await p.s.h.store.get_session(p.s.session.id)
        assert "turn_lease_token" not in session.metadata
    finally:
        await p.s.h.resources.retire(occupied)
    await asyncio.sleep(.35)
    assert await worker(p, dispatcher, queue, "formal-resource-replacement").run_once() == 1
    assert (await p.s.h.tasks.get(p.h.task.id)).attempt_no == 2
    saved = await p.s.d.runs.get_run(p.s.detail.run.id)
    assert [a.id for a in saved.attempts] == original_attempts
    assert next(a for a in saved.attempts if a.id == p.s.attempt.id).attempt_no == p.s.attempt.attempt_no
    assert len(saved.results) == 1 and len(p.requests) == 2 and len(p.effects) == 1
    assert await p.s.h.resources.list_active() == []


@pytest.mark.asyncio
@dispatch_case
async def test_monitor_renews_both_original_owners_before_begin_without_native_interrupts(assessment_case, monkeypatch):
    p = assessment_case
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    renewed = asyncio.Event()
    counts = {"resource": 0, "dual_owner": 0}
    resource_renew, owner_renew, execute = p.s.h.resources.renew, p.s.h.tasks.renew_framework_execution, p.executor.execute
    captured = []
    async def renew_resource(*args, **kwargs):
        result = await resource_renew(*args, **kwargs)
        counts["resource"] += int(result)
        return result
    async def renew_owner(task, *args, **kwargs):
        result = await owner_renew(task, *args, **kwargs)
        counts["dual_owner"] += int(result)
        if counts["dual_owner"] >= 3:
            renewed.set()
        return result
    async def wait_for_monitor(task, **kwargs):
        captured.append(task)
        await asyncio.wait_for(renewed.wait(), 10)
        return await execute(task, **kwargs)
    controls = []
    def forbidden(*args, **kwargs):
        controls.append(True)
        raise AssertionError("formal ownership monitor cannot invoke the legacy graph interrupt")
    monkeypatch.setattr(p.s.h.resources, "renew", renew_resource)
    monkeypatch.setattr(p.s.h.tasks, "renew_framework_execution", renew_owner)
    monkeypatch.setattr(p.executor, "execute", wait_for_monitor)
    monkeypatch.setattr(p.s.h.sessions._runtime_service, "request_interrupt", forbidden)
    assert await worker(p, dispatcher, queue).run_once() == 1
    assert counts["resource"] >= 3 and counts["dual_owner"] >= 3
    assert captured[0].runtime_owner.controlled_case_lease.run_item_execution_attempt_id == p.s.attempt.id
    assert controls == [] and await p.s.h.resources.list_active() == []


@pytest.mark.asyncio
@dispatch_case
@pytest.mark.parametrize("loss", ["resource", "agent", "case", "cancel"])
async def test_owner_monitor_loss_stops_unstarted_graph_without_generic_failure_or_transfer(assessment_case, loss, monkeypatch):
    p = assessment_case
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    entered, stopped = asyncio.Event(), asyncio.Event()
    leases = []
    acquire = p.s.h.resources.acquire
    async def capture_resource(*args, **kwargs):
        lease = await acquire(*args, **kwargs)
        leases.append(lease)
        return lease
    async def unstarted(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("lost formal owner cannot begin, fail or transfer")
    monkeypatch.setattr(p.executor, "execute", unstarted)
    monkeypatch.setattr(p.s.h.resources, "acquire", capture_resource)
    for name in ("begin", "finish"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(queue, "transfer", forbidden)
    running = asyncio.create_task(worker(p, dispatcher, queue).run_once())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if loss == "resource":
            await p.s.h.resources.retire(leases[0])
        elif loss == "cancel":
            assert await p.s.h.tasks.interrupt(p.s.session.id, reason="isolated formal stop", source="test")
        else:
            with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
                if loss == "agent":
                    cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s",
                        (p.s.binding.binding_id,))
                else:
                    cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                        "'\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        assert await asyncio.wait_for(running, 10) == 0
        assert stopped.is_set() and forbidden_calls == []
        task = await p.s.h.tasks.get(p.h.task.id)
        assert task.status == ("interrupted" if loss == "cancel" else "claimed")
        assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await p.s.h.redis.xlen(queue.stream + ":dead_letter") == 0
        assert p.requests == p.effects == [] and await p.s.h.resources.list_active() == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@dispatch_case
async def test_committed_execution_reply_loss_keeps_pending_then_read_only_ack(assessment_case, monkeypatch):
    p = assessment_case
    dispatcher = compose(p)
    raw = delivery(p.s)
    queue = await queue_for(p, raw)
    execute = p.runtime.execute
    async def lose_reply(*args, **kwargs):
        result = await execute(*args, **kwargs)
        assert result.settlement_receipt is not None
        raise ConnectionError("isolated committed reply loss")
    monkeypatch.setattr(p.runtime, "execute", lose_reply)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "completed"
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert await p.s.h.redis.xlen(queue.stream + ":dead_letter") == 0
    before = database_digest(p.s)
    dispatcher.set_framework_capabilities(replace(capabilities(), controlled_case_available=False))
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("committed original must not execute again")
    for name in ("claim_controlled_case", "begin", "finish"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    await asyncio.sleep(.35)
    assert await worker(p, dispatcher, queue, "formal-committed-reader").run_once() == 1
    assert database_digest(p.s) == before and forbidden_calls == []
    assert len(p.requests) == 2 and len(p.effects) == 1 and await p.s.h.resources.list_active() == []


@pytest.mark.asyncio
@dispatch_case
@pytest.mark.parametrize("fault", ["deadline", "owner", "cancel"])
async def test_delivery_loss_or_cancellation_drains_unstarted_formal_execution(assessment_case, monkeypatch, caplog, fault):
    p = assessment_case
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def unstarted(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("unknown delivery cannot begin, fail or transfer")
    monkeypatch.setattr(p.executor, "execute", unstarted)
    monkeypatch.setattr(queue, "transfer", forbidden)
    for name in ("begin", "finish"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    async def handle(task):
        await dispatcher.handle_v2(task, worker_id="formal-fault-worker")
    current = RedisTaskWorker(queue, consumer="formal-fault-worker", handler=handle, block_ms=0,
        reclaim_idle_ms=300, max_retries=0, task_timeout_seconds=5 if fault == "deadline" else 30)
    entries = await p.s.h.redis.xrange(queue.stream)
    running = asyncio.create_task(current.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if fault == "owner":
            await p.s.h.redis.xclaim(queue.stream, queue.group, "isolated-formal-rival", 0, [entries[0][0]], justid=True)
        elif fault == "cancel":
            running.cancel()
        if fault == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await running
        else:
            assert await asyncio.wait_for(running, 10) == 0
        assert stopped.is_set() and forbidden_calls == []
        assert (await p.s.h.tasks.get(p.h.task.id)).status == "claimed"
        assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await p.s.h.redis.xrange(queue.stream) == entries
        assert await p.s.h.redis.xlen(queue.stream + ":dead_letter") == 0
        assert p.requests == p.effects == [] and await p.s.h.resources.list_active() == []
        if fault == "deadline":
            assert "redis_task_execution_deadline_unconfirmed" in caplog.text
        assert "agent_case_execution_receipt_unknown" in caplog.text
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{**DISPATCH, "approval": True}], indirect=True)
async def test_native_approval_wait_is_not_acknowledged_failed_or_automatically_replayed(assessment_case, monkeypatch, caplog):
    p = assessment_case
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert any(getattr(record, "defer_reason", None) == "framework_case_approval_projection_unavailable"
        for record in caplog.records)
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
    assert len(p.requests) == 1 and p.effects == []
    assert await p.bridge.jobs.list_jobs(session_id=p.s.session.id) == []
    assert await p.s.h.store.list_approvals(p.s.session.id) == []
    assert await p.s.h.resources.list_active() == []
    before = database_digest(p.s)
    await asyncio.sleep(.35)
    assert await worker(p, dispatcher, queue, "formal-must-not-resume").run_once() == 0
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert database_digest(p.s) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@dispatch_case
@pytest.mark.parametrize("change", ["writer", "port", "executor", "assessment", "finalizer", "settlement",
    "input_reader", "dependency", "runtime", "checkpoint_reader", "saver", "model_policy", "governance"])
async def test_unavailable_formal_delivery_does_not_claim_allocate_or_transfer(assessment_case, change, monkeypatch):
    p = assessment_case
    changes = {"writer": {"controlled_case_available": False}, "input_reader": {"controlled_case_input_max_version": 3},
        "dependency": {"dependency_lock_sha256": "b"*64}, "runtime": {"runtime_version": "future"},
        "checkpoint_reader": {"controlled_case_checkpoint_max_version": 0}}
    dispatcher = compose(p, caps=replace(capabilities(), **changes.get(change, {})), port=change != "port")
    field = {"executor": "_executor", "assessment": "_assessments", "finalizer": "_sessions", "settlement": "_settlements"}.get(change)
    if field:
        setattr(p.runtime, field, None)
    if change in {"saver", "model_policy"}:
        setattr(p.executor._adapter, "_checkpointer_factory" if change == "saver" else "_model_call_policy", None)
    if change == "governance":
        p.runtime._runtime._tool_governance_service = None
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("incompatible delivery cannot claim or allocate")
    monkeypatch.setattr(p.s.h.tasks, "_claim_task_attempt", forbidden)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    queue = await queue_for(p, delivery(p.s))
    monkeypatch.setattr(queue, "transfer", forbidden)
    before = database_digest(p.s)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert database_digest(p.s) == before and forbidden_calls == [] and p.requests == p.effects == []


def test_main_composes_existing_controlled_ports_without_enabling_writer():
    """Composition audit only, not an application lifespan/E4 deployment test."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(encoding="utf-8"))
    def keywords(name):
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == name]
        assert len(calls) == 1
        return {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
    dispatcher = keywords("AgentTaskDispatcher")
    runtime = keywords("ControlledCaseRuntime")
    runner = keywords("ControlledCaseExecutor")
    adapter = keywords("ControlledCaseGraphAdapter")
    assert dispatcher["controlled_case_runtime"] == "controlled_case_runtime"
    assert keywords("RuntimeService")["framework_approval_projection"] == "framework_approval_projection"
    assert keywords("FrameworkApprovalProjection") == {"sessions": "store", "jobs": "tool_job_service"}
    assert keywords("ControlledCaseApprovalWaits") == {"projection": "framework_approval_projection"}
    assert runtime == {"runtime_service": "runtime_service", "session_service": "session_service",
        "assessment_service": "ControlledCaseAssessments(agent_task_store)",
        "settlement_service": "ControlledCaseSettlements(agent_task_store)", "executor": "controlled_case_executor",
        "approval_wait_service": "ControlledCaseApprovalWaits(agent_task_store, projection=framework_approval_projection)"}
    assert runner == {"task_store": "agent_task_store", "graph_adapter": "controlled_case_graph_adapter"}
    assert adapter == {"model_resolver": "model_runtime_service.resolve_langchain_chat_model",
        "checkpointer_factory": "deep_agent_checkpoint_provider.open if deep_agent_checkpoint_provider is not None else None",
        "model_call_policy": "framework_model_call_policy"}
    assert FrameworkWorkerCapabilities("a"*64, True).controlled_case_available is False

"""Monitor failure provenance must survive without logging capabilities/payloads."""
from __future__ import annotations

import asyncio
import io
import json
import logging
from types import SimpleNamespace

import pytest

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.runtime.store import ContinuationLeaseLostError


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,error_type,event", [
    ("resource_rejected", ContinuationLeaseLostError, "agent_task_resource_renew_failed"),
    ("resource_error", TimeoutError, "agent_task_resource_renew_failed"),
    ("interrupt_poll", LookupError, "agent_task_interrupt_poll_failed"),
    ("interrupt_apply", RuntimeError, "agent_task_interrupt_apply_failed"),
])
async def test_monitor_failure_logs_its_original_operation_without_exposing_inputs(caplog, operation, error_type, event):
    private = "fixture-private-diagnostic-input"
    failure = error_type(private)
    calls = []

    async def renew(lease, *, lease_seconds):
        calls.append("renew")
        if operation == "resource_error":
            raise failure
        return operation != "resource_rejected"

    async def interrupt_requested(task_id):
        calls.append("interrupt_poll")
        if operation == "interrupt_poll":
            raise failure
        return True

    def request_interrupt(*args):
        calls.append("interrupt_apply")
        raise failure

    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_default_lease_seconds=3, agent_task_control_poll_seconds=.001))
    dispatcher = AgentTaskDispatcher(settings, SimpleNamespace(interrupt_requested=interrupt_requested),
        SimpleNamespace(_runtime_service=SimpleNamespace(request_interrupt=request_interrupt)),
        SimpleNamespace(renew=renew))
    task = SimpleNamespace(id="diagnostic-task", attempt_id="diagnostic-attempt", worker_id="diagnostic-worker",
        session_id="diagnostic-session", input={"private": private}, lease_token=private)
    lease = SimpleNamespace(resource_id="diagnostic-slot", lease_token=private)
    with pytest.raises(error_type) as captured:
        await dispatcher._monitor(task, lease, asyncio.Event())
    if operation != "resource_rejected":
        assert captured.value is failure
    records = [record for record in caplog.records if record.getMessage() == event]
    assert len(records) == 1
    record, = records
    assert record.task_id == task.id and record.attempt_id == task.attempt_id
    assert record.worker_id == task.worker_id and record.resource_id == lease.resource_id
    assert record.monitor_operation == ("resource_renew" if operation.startswith("resource") else operation)
    assert record.error_type == error_type.__name__
    assert record.operation_seconds >= 0 and record.monitor_seconds >= record.operation_seconds
    assert record.exc_info is None and private not in repr(record.__dict__)
    assert calls == ["renew", *([] if operation.startswith("resource") else ["interrupt_poll"]),
        *(["interrupt_apply"] if operation == "interrupt_apply" else [])]


@pytest.mark.asyncio
async def test_monitor_success_keeps_renewing_but_applies_an_interrupt_once(caplog):
    caplog.set_level("INFO", logger="src.application.orchestration.agent_task_dispatcher")
    stop = asyncio.Event()
    renewed, polls, interrupts = [], [], []

    async def renew(lease, *, lease_seconds):
        renewed.append(lease_seconds)
        if len(renewed) == 3:
            stop.set()
        return True

    async def interrupt_requested(task_id):
        polls.append(task_id)
        return True

    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_default_lease_seconds=3, agent_task_control_poll_seconds=.001))
    task = SimpleNamespace(id="task", session_id="session", worker_id="worker", attempt_id="attempt")
    dispatcher = AgentTaskDispatcher(settings, SimpleNamespace(interrupt_requested=interrupt_requested),
        SimpleNamespace(_runtime_service=SimpleNamespace(request_interrupt=lambda *args: interrupts.append(args))),
        SimpleNamespace(renew=renew))
    await dispatcher._monitor(task, SimpleNamespace(resource_id="slot"), stop)
    assert renewed == [3, 3, 3] and polls == [task.id]
    assert interrupts == [(task.session_id, "User requested task interruption")]
    assert [record.getMessage() for record in caplog.records] == ["agent_task_interrupt_applied"]


@pytest.mark.asyncio
async def test_already_stopped_monitor_does_not_renew_or_report_a_failure(caplog):
    stop = asyncio.Event()
    stop.set()
    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_default_lease_seconds=3, agent_task_control_poll_seconds=.001))
    await AgentTaskDispatcher(settings, None, None, None)._monitor(None, None, stop)
    assert not caplog.records


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resource_renew", "interrupt_poll"])
async def test_monitor_cancellation_remains_cancellation_without_a_failure_log(caplog, operation):
    entered = asyncio.Event()

    async def block():
        entered.set()
        await asyncio.Event().wait()

    async def renew(lease, *, lease_seconds):
        if operation == "resource_renew":
            await block()
        return True

    async def interrupt_requested(task_id):
        await block()

    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_default_lease_seconds=3, agent_task_control_poll_seconds=.001))
    dispatcher = AgentTaskDispatcher(settings, SimpleNamespace(interrupt_requested=interrupt_requested), None,
        SimpleNamespace(renew=renew))
    monitor = asyncio.create_task(dispatcher._monitor(SimpleNamespace(id="task"), None, asyncio.Event()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert not caplog.records


def test_configured_log_handler_renders_monitor_diagnostics_but_not_arbitrary_extra(monkeypatch):
    from src.core.log_format import configure_logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [handler])
    configure_logging(root.level)
    logger = logging.getLogger("src.application.orchestration.agent_task_dispatcher")
    expected = {"task_id": "diagnostic-task", "monitor_operation": "resource_renew", "operation_seconds": 3.25,
        "monitor_seconds": 4.75, "error_type": "TimeoutError", "resource_lease_seconds": 3,
        "since_resource_renew_seconds": 4.0, "last_interrupt_poll_seconds": 3.5}
    private = "fixture-private-diagnostic-input"
    logger.warning("agent_task_resource_renew_failed", extra={**expected,
        "owner_token": private, "payload": {"private": private}})
    logger.warning("ordinary_event", extra={"owner_token": private})
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2 and private not in stream.getvalue()
    assert "agent_task_resource_renew_failed diagnostics=" in lines[0]
    assert json.loads(lines[0].split(" diagnostics=", 1)[1]) == expected
    assert lines[1].endswith(": ordinary_event")


@pytest.mark.asyncio
async def test_monitor_failure_reports_previous_success_and_poll_duration(monkeypatch, caplog):
    import src.application.orchestration.agent_task_dispatcher as module

    now, renewals = 0.0, 0

    async def renew(lease, *, lease_seconds):
        nonlocal now, renewals
        now += .25
        renewals += 1
        return renewals == 1

    async def interrupt_requested(task_id):
        nonlocal now
        now += 4
        return False

    monkeypatch.setattr(module, "monotonic", lambda: now)
    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_default_lease_seconds=3, agent_task_control_poll_seconds=.001))
    dispatcher = AgentTaskDispatcher(settings, SimpleNamespace(interrupt_requested=interrupt_requested), None,
        SimpleNamespace(renew=renew))
    task = SimpleNamespace(id="task", attempt_id="attempt", worker_id="worker")
    with pytest.raises(ContinuationLeaseLostError):
        await dispatcher._monitor(task, SimpleNamespace(resource_id="slot"), asyncio.Event())
    record, = caplog.records
    assert record.monitor_operation == "resource_renew" and record.operation_seconds == .25
    assert record.resource_lease_seconds == 3
    assert record.last_interrupt_poll_seconds == 4
    assert record.since_resource_renew_seconds == 4.25
    assert record.monitor_seconds == 4.5


def test_diagnostic_formatter_never_formats_non_scalar_values():
    from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT

    class PrivateValue:
        def __str__(self):
            raise AssertionError("must not inspect a private value")

        __repr__ = __str__

    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "controlled_event", (), None)
    record.error_type = PrivateValue()
    record.payload = PrivateValue()
    ContextFilter().filter(record)
    rendered = ContextFormatter(LOG_FORMAT).format(record)
    assert json.loads(rendered.split(" diagnostics=", 1)[1]) == {"error_type": "<unsupported>"}


@pytest.mark.asyncio
@pytest.mark.parametrize("alive,exitcode", [(True, None), (False, 3)])
async def test_worker_gate_timeout_keeps_exception_and_reports_only_process_state(alive, exitcode):
    from tests.test_framework_task_control import wait_gate

    failure = TimeoutError("original Redis wait failure")

    async def blpop(key, *, timeout):
        raise failure

    process = SimpleNamespace(pid=123, exitcode=exitcode, is_alive=lambda: alive)
    with pytest.raises(TimeoutError) as captured:
        await wait_gate(SimpleNamespace(blpop=blpop), "private-gate-key", 20, process=process)
    assert captured.value is failure
    assert captured.value.__notes__ == [
        f"Worker gate timed out after 20s: pid=123, alive={alive}, exitcode={exitcode}"]
    assert "private" not in str(captured.value.__notes__)


@pytest.mark.asyncio
async def test_worker_gate_deadline_is_not_extended_by_diagnostics():
    from tests.test_framework_task_control import wait_gate

    cancelled = asyncio.Event()

    async def blpop(key, *, timeout):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    process = SimpleNamespace(pid=123, exitcode=None, is_alive=lambda: True)
    with pytest.raises(TimeoutError) as captured:
        await wait_gate(SimpleNamespace(blpop=blpop), "private-gate-key", .001, process=process)
    assert cancelled.is_set()
    assert captured.value.__notes__ == [
        "Worker gate timed out after 0.001s: pid=123, alive=True, exitcode=None"]

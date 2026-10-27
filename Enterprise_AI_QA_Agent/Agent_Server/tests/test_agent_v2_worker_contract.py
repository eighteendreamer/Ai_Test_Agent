from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.redis_task_queue import QueuedTask
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ChatMessage, MessageRole, SendMessageRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred


def binding(*, session_id: str = "session-1", turn_id: str = "turn-1", **changes):
    value = {
        "binding_id": "binding-1",
        "session_id": session_id,
        "turn_id": turn_id,
        "runtime_kind": "deepagents",
        "runtime_version": "v2.0.0",
        "state_schema_version": 2,
        "thread_id": turn_id,
        "checkpoint_namespace": "qa:v2",
        "prompt_version": "prompt-1",
        "skill_version": "skill-1",
        "tool_schema_version": "tools-1",
        "model_config_fingerprint": "model-1",
        "dependency_lock_sha256": "a" * 64,
    }
    value.update(changes)
    return RuntimeBinding(**value)


def command(runtime_binding: RuntimeBinding) -> AgentTaskCommandV2:
    return AgentTaskCommandV2(
        task_id="task-1",
        session_id=runtime_binding.session_id,
        turn_id=runtime_binding.turn_id,
        request_id="request-1",
        trace_id="trace-1",
        runtime_binding=runtime_binding,
    )


class _Settings:
    class orchestration:
        agent_task_stream = "qa:tasks:agent"
        agent_task_v2_stream = "qa:tasks:agent:v2"


class _Tasks:
    def __init__(self, stored):
        self.stored = stored
        self.claimed = []

    async def get(self, task_id):
        assert task_id == self.stored.id
        return self.stored

    async def get_framework_message_stage_version(self, task_id, *, session_id):
        assert task_id == self.stored.id and session_id == self.stored.session_id
        return None

    async def claim(self, task_id, *, worker_id, expected_binding=None):
        assert task_id == self.stored.id
        assert expected_binding == self.stored.runtime_binding
        self.claimed.append(worker_id)
        return self.stored

    async def ensure_framework_message_recovery(self, task_id):
        assert task_id == self.stored.id
        return None

    async def ensure_framework_reconciliation(self, task_id):
        assert task_id == self.stored.id
        return None


def stored_task(runtime_binding):
    return SimpleNamespace(
        id="task-1",
        operation="message",
        session_id=runtime_binding.session_id,
        turn_id=runtime_binding.turn_id,
        project_id=None,
        request_id="request-1",
        trace_id="trace-1",
        run_id=None,
        run_item_id=None,
        reference_id=None,
        runtime_binding=runtime_binding,
        runtime_owner=None,
        status="claimed",
        created_at=datetime.now(timezone.utc),
        attempt_no=1,
        attempt_id="attempt-1",
        worker_id="v2-worker",
    )


@pytest.mark.asyncio
async def test_legacy_dispatcher_rejects_v2_before_task_lookup():
    runtime_binding = binding()
    tasks = _Tasks(stored_task(runtime_binding))
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None)
    delivery = QueuedTask("message-1", command(runtime_binding).model_dump(mode="json"))

    with pytest.raises(ValueError, match="v2 agent command requires the v2 worker stream"):
        await dispatcher.handle(delivery, worker_id="legacy-worker")

    assert tasks.claimed == []


@pytest.mark.asyncio
async def test_v2_dispatcher_requires_persisted_binding_and_executes_once():
    runtime_binding = binding()
    task = stored_task(runtime_binding)
    tasks = _Tasks(task)
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    executions = []

    async def execute(received):
        executions.append((received.id, received.runtime_binding))

    dispatcher._execute = execute
    delivery = QueuedTask("message-1", command(runtime_binding).model_dump(mode="json"))

    await dispatcher.handle_v2(delivery, worker_id="v2-worker")

    assert tasks.claimed == ["v2-worker"]
    assert executions == [("task-1", runtime_binding)]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["message", "approval_resume", "approval_recover", "framework_reconcile"])
@pytest.mark.parametrize("reader,format_version", [(15, 16), (16, 17), (16, 16)])
async def test_approval_preparation_format_is_checked_before_claim(operation, reader, format_version):
    selected = binding()
    task = stored_task(selected)
    task.operation = operation
    task.reference_id = "approval-1" if operation != "message" else None
    tasks = _Tasks(task)

    async def version(task_id, *, session_id):
        assert task_id == task.id and session_id == task.session_id
        return format_version

    tasks.get_framework_message_stage_version = version
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=reader))
    executions = []

    async def execute(received):
        executions.append(received.id)

    dispatcher._execute = execute
    envelope = command(selected).model_copy(update={"operation": operation, "reference_id": task.reference_id})
    delivery = QueuedTask("message-1", envelope.model_dump(mode="json"))
    if format_version > reader:
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            await dispatcher.handle_v2(delivery, worker_id="v2-worker")
        assert tasks.claimed == executions == []
    else:
        await dispatcher.handle_v2(delivery, worker_id="v2-worker")
        assert tasks.claimed == ["v2-worker"] and executions == [task.id]


@pytest.mark.asyncio
async def test_v2_dispatcher_rejects_binding_drift_before_claim():
    command_binding = binding()
    stored_binding = binding(runtime_version="langgraph-1.2")
    tasks = _Tasks(stored_task(stored_binding))
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None)
    delivery = QueuedTask("message-1", command(command_binding).model_dump(mode="json"))

    with pytest.raises(ValueError, match="runtime binding"):
        await dispatcher.handle_v2(delivery, worker_id="v2-worker")

    assert tasks.claimed == []


@pytest.mark.asyncio
async def test_v2_worker_entrypoint_uses_only_v2_stream_and_dispatcher(monkeypatch):
    from src.cli import run_agent_v2_worker as module

    runtime_binding = binding()
    wire = command(runtime_binding).model_dump(mode="json")
    received = []

    class Dispatcher:
        def set_framework_capabilities(self, capabilities):
            assert capabilities.deepagents_available

        async def handle_v2(self, task, *, worker_id):
            received.append((task.payload, worker_id))

    class Queue:
        instances = []

        def __init__(self, _url, **kwargs):
            self.stream = kwargs["stream"]
            self.group = kwargs["group"]
            Queue.instances.append(self)

        async def connect(self):
            pass

        async def close(self):
            pass

    class Worker:
        last = None

        def __init__(self, handler):
            self.handler = handler
            Worker.last = self

        @classmethod
        def from_settings(cls, queue, *, settings, consumer, handler):
            assert queue.stream == settings.orchestration.agent_task_v2_stream
            assert consumer == "v2-test-worker"
            return cls(handler)

        async def run_once(self):
            await self.handler(
                QueuedTask(
                    "message-1",
                    wire,
                )
            )

        def stop(self):
            pass

        async def run(self):
            await self.run_once()

    @asynccontextmanager
    async def lifespan(_app):
        yield

    settings = SimpleNamespace(
        database=SimpleNamespace(redis_url="redis://unused"),
        deep_agents=SimpleNamespace(enabled=True, checkpoint_enabled=True),
        orchestration=SimpleNamespace(
            agent_task_v2_stream="qa:tasks:agent:v2",
            agent_task_v2_consumer_group="qa:workers:agent-v2",
            redis_task_socket_timeout_seconds=1,
            redis_task_retention_seconds=60,
            redis_task_retention_interval_seconds=1,
            redis_task_retention_batch_size=10,
        ),
    )
    fake_app = SimpleNamespace(
        state=SimpleNamespace(agent_task_dispatcher=Dispatcher()),
        router=SimpleNamespace(lifespan_context=lifespan),
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "app", fake_app)
    monkeypatch.setattr(module, "RedisTaskQueue", Queue)
    monkeypatch.setattr(module, "RedisTaskWorker", Worker)

    await module._run(once=True, worker_id="v2-test-worker")

    assert [(queue.stream, queue.group) for queue in Queue.instances] == [
        ("qa:tasks:agent:v2", "qa:workers:agent-v2")
    ]
    assert received == [(wire, "v2-test-worker")]


@pytest.mark.asyncio
async def test_v2_worker_executes_persisted_task_with_binding_context(admission):
    """Real PG/outbox/Redis/dispatcher/SessionService; controlled runtime response."""
    from uuid import uuid4

    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.redis_task_worker import RedisTaskWorker

    harness = admission
    runtime_binding = binding(
        session_id=harness.session_id,
        turn_id=f"turn-{uuid4().hex}",
        binding_id=f"binding-{uuid4().hex}",
    )
    harness.settings.orchestration.agent_task_v2_stream = f"{harness.prefix}:tasks:agent:v2"
    harness.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    task_command = AgentTaskCommandV2(
        task_id=f"task-{uuid4().hex}",
        session_id=harness.session_id,
        turn_id=runtime_binding.turn_id,
        request_id=f"request-{uuid4().hex}",
        trace_id=f"trace-{uuid4().hex}",
        runtime_binding=runtime_binding,
    )
    payload = SendMessageRequest(
        content="execute through the v2 worker",
        submit_mode="background",
        metadata={"client_message_id": f"client-{uuid4().hex}"},
    )
    message = ChatMessage(
        id=f"message-{uuid4().hex}",
        role=MessageRole.user,
        content=payload.content,
        created_at=datetime.now(timezone.utc),
        metadata={"turn_id": runtime_binding.turn_id, "context_eligible": False},
    )
    task = await harness.tasks.submit_v2(
        task_command,
        payload,
        message,
        idempotency_key=f"client-{uuid4().hex}",
        model_budget=harness.budget.model_copy(update={"model_config_fingerprints": {
            "primary": runtime_binding.model_config_fingerprint}}),
    )
    with pytest.raises(ValueError, match="claim runtime binding"):
        await harness.tasks.claim(task.id, worker_id="legacy-cannot-claim")
    with pytest.raises(ValueError, match="claim runtime binding"):
        await harness.tasks.claim(task.id, worker_id="wrong-artifact", expected_binding=binding(
            session_id=harness.session_id, turn_id=runtime_binding.turn_id,
            runtime_version="future-version"))
    assert (await harness.tasks.get(task.id)).attempt_no == 0

    v2_queue = RedisTaskQueue(
        harness.settings.database.redis_url,
        stream=harness.settings.orchestration.agent_task_v2_stream,
        group=f"v2-test-{uuid4().hex}",
        client=harness.redis,
    )
    await v2_queue.connect()
    contexts = []
    bindings = []
    errors = []
    original_execute = harness.runtime.execute_turn

    async def tracked_execute(session, request, **kwargs):
        from src.runtime.execution_context import current_framework_owner

        captured = current_framework_owner()
        assert captured is not None and captured.binding == request.runtime_binding
        contexts.append(dict(request.context))
        bindings.append(request.runtime_binding)
        return await original_execute(session, request, **kwargs)

    harness.runtime.execute_turn = tracked_execute
    async def handle(item):
        try:
            await harness.dispatcher.handle_v2(item, worker_id="v2-live-worker")
        except Exception as exc:
            errors.append(exc)
            raise
    try:
        assert await harness.outbox.relay_once(
            queue_for_stream=lambda stream: v2_queue
            if stream == harness.settings.orchestration.agent_task_v2_stream
            else None,
        ) == 1
        worker = RedisTaskWorker(
            v2_queue,
            consumer="v2-live-worker",
            handler=handle,
            block_ms=0,
        )
        processed = await worker.run_once()
        if errors:
            raise errors[0]
        assert processed == 1
        completed = await harness.tasks.get(task.id)
        assert completed.status == "completed"
        assert completed.runtime_binding == runtime_binding
        assert contexts
        assert bindings == [runtime_binding]
        await harness.dispatcher.handle_v2(
            QueuedTask("duplicate", task_command.model_dump(mode="json")),
            worker_id="duplicate-worker",
        )
        assert bindings == [runtime_binding]
        assert (await harness.tasks.get(task.id)).attempt_no == 1
        assert not await v2_queue.pending()
    finally:
        await harness.redis.delete(v2_queue.stream, f"{v2_queue.stream}:dead_letter")


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,available,reason", [
    ({"runtime_version": "future-version"}, True, "runtime_version"),
    ({"state_schema_version": 3}, True, "state_schema"),
    ({"dependency_lock_sha256": "b" * 64}, True, "dependency_artifact"),
    ({"runtime_kind": "langgraph"}, True, "runtime_kind"),
    ({}, False, "deepagents"),
])
async def test_unavailable_runtime_is_deferred_without_claim(changes, available, reason):
    selected = binding(**changes)
    tasks = _Tasks(stored_task(selected))
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, available))
    with pytest.raises(TaskDeferred, match=reason):
        await dispatcher.handle_v2(
            QueuedTask("message-1", command(selected).model_dump(mode="json")),
            worker_id="incompatible-worker",
        )
    assert tasks.claimed == []


@pytest.mark.asyncio
async def test_legacy_wire_cannot_claim_bound_v2_record():
    selected = binding()
    tasks = _Tasks(stored_task(selected))
    wire = command(selected).model_dump(mode="json", exclude={"runtime_binding"})
    wire["schema_version"] = 1
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None)
    with pytest.raises(ValueError, match="requires a v2"):
        await dispatcher.handle(QueuedTask("forged-wire", wire), worker_id="legacy-worker")
    assert tasks.claimed == []


@pytest.mark.parametrize("operation", ["manual_resume", "coordinator_child", "coordinator_resume"])
def test_unimplemented_framework_operations_remain_unavailable(operation):
    with pytest.raises(TaskDeferred, match="framework_operation_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True).require(binding(), operation=operation)


def test_binding_controls_runtime_and_checkpoint_namespace():
    from src.application.runtime.runtime_service import RuntimeService
    from src.runtime.control import RuntimeControlRegistry
    from src.registry.tools import ToolRegistry
    from tests.test_deep_agent_runtime_adapter import _ModelRuntimeStub, _runtime_session, _runtime_request

    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(), tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(), deep_agent_enabled=True,
        deep_agent_runtime_adapter=object(), deep_agent_pilot_mode_keys=["other-mode"])
    request = _runtime_request("session-1", "turn-1")
    assert not runtime._should_use_deep_agent(request)
    request.runtime_binding = binding()
    # Existing turns use their binding, independently of the route for new turns.
    assert runtime._should_use_deep_agent(request)
    state = runtime._build_initial_state(_runtime_session("session-1"), request)
    assert state["context_bundle"]["runtime_binding"] == request.runtime_binding.model_dump(mode="json")
    assert state["context_bundle"]["checkpoint_namespace"] == "qa:v2"
    request.runtime_binding = binding(runtime_kind="langgraph")
    with pytest.raises(ValueError, match="no implemented v2"):
        runtime._should_use_deep_agent(request)


def test_public_input_cannot_supply_runtime_binding_or_namespace():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from tests.test_deep_agent_runtime_adapter import _runtime_session

    request = InputOrchestratorService(ModeRegistry()).orchestrate(
        _runtime_session("session-1"), SendMessageRequest(content="review", context={
            "runtime_binding": binding().model_dump(mode="json"),
            "runtime_kind": "deepagents", "runtime_version": "future-version",
            "checkpoint_namespace": "other-thread",
        }))
    assert request.runtime_binding is None
    assert not {"runtime_binding", "runtime_kind", "runtime_version", "checkpoint_namespace"} & request.context.keys()


@pytest.mark.asyncio
async def test_bound_runtime_cannot_execute_without_a_captured_owner():
    from src.application.runtime.runtime_service import RuntimeService
    from src.runtime.control import RuntimeControlRegistry
    from src.registry.tools import ToolRegistry
    from tests.test_deep_agent_runtime_adapter import _ModelRuntimeStub, _runtime_session, _runtime_request

    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(), tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(), deep_agent_enabled=True,
        deep_agent_runtime_adapter=object())
    request = _runtime_request("session-1", "turn-1")
    request.runtime_binding = binding()
    with pytest.raises(RuntimeError, match="captured execution owner"):
        await runtime._execute_deep_agent_turn(_runtime_session("session-1"), request,
            state={}, pilot_stage="DA-E1", on_model_chunk=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_v2_expired_attempt_reuses_admission_only_before_execution(admission, started):
    from src.runtime.store import ContinuationLeaseLostError
    from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload
    from uuid import uuid4

    harness = admission
    selected = make_binding(harness.session_id, f"turn-{uuid4().hex}")
    task_command = make_command(harness, selected)
    task = await harness.tasks.submit_v2(task_command, make_payload(), make_message(harness, selected),
        idempotency_key=f"client-{uuid4().hex}", model_budget=harness.budget.model_copy(update={
            "model_config_fingerprints": {"primary": selected.model_config_fingerprint}}))
    owner = await harness.tasks.claim(task.id, worker_id="expired-worker", expected_binding=selected)
    if started:
        await harness.tasks.begin(owner, resource_id=f"turn:{task.id}", fencing_token=1)
    assert await harness.store.renew_turn_execution(harness.session_id, owner.lease_token, 0)
    harness.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await harness.dispatcher.handle_v2(
        QueuedTask("redelivery", task_command.model_dump(mode="json")), worker_id="replacement")
    current = await harness.tasks.get(task.id)
    assert current.status == ("recovery_required" if started else "completed")
    assert current.attempt_no == (1 if started else 2)
    assert len(harness.runtime.calls) == (0 if started else 1)
    with pytest.raises(ContinuationLeaseLostError):
        await harness.tasks.finish(owner, status="completed")

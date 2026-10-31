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
        self.message_recoveries = []
        self.framework_capabilities = []
        self.model_ledger_checks = []

    async def get(self, task_id):
        assert task_id == self.stored.id
        return self.stored

    async def get_framework_message_stage_version(self, task_id, *, session_id):
        assert task_id == self.stored.id and session_id == self.stored.session_id
        return None

    async def claim(self, task_id, *, worker_id, expected_binding=None, framework_capabilities=None, check_model_ledger=False):
        assert task_id == self.stored.id
        assert expected_binding == self.stored.runtime_binding
        self.claimed.append(worker_id)
        self.framework_capabilities.append(framework_capabilities)
        self.model_ledger_checks.append(check_model_ledger)
        return self.stored

    async def ensure_framework_message_recovery(self, task_id, *, native_reader=None):
        assert task_id == self.stored.id
        self.message_recoveries.append((task_id, native_reader))
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
@pytest.mark.parametrize("operation", ["message", "message_recover", "approval_resume", "approval_recover", "framework_reconcile"])
async def test_model_reader_capabilities_reach_claim_for_all_bound_operations(operation):
    selected = binding()
    task = stored_task(selected)
    task.operation = operation
    task.reference_id = "approval-1" if operation != "message" else None
    tasks = _Tasks(task)
    caps = FrameworkWorkerCapabilities("a" * 64, True)
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=caps, framework_model_calls=object())
    executions = []

    async def execute(received):
        executions.append(received.id)

    dispatcher._execute = execute
    envelope = command(selected).model_copy(update={"operation": operation, "reference_id": task.reference_id})
    await dispatcher.handle_v2(QueuedTask("reader-admission", envelope.model_dump(mode="json")), worker_id="v2-worker")
    assert tasks.claimed == ["v2-worker"] and executions == [task.id]
    assert tasks.framework_capabilities == [caps] and tasks.model_ledger_checks == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["claimed", "recovery_required"])
@pytest.mark.parametrize("native_reader", [None, object()])
async def test_v2_dispatcher_forwards_native_reader_at_existing_recovery_entries(status, native_reader):
    selected = binding()
    task = stored_task(selected)
    task.status = status
    tasks = _Tasks(task)
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True),
        framework_native_reader=native_reader)
    executions = []

    async def execute(received):
        executions.append(received.id)

    dispatcher._execute = execute
    delivery = QueuedTask("message-1", command(selected).model_dump(mode="json"))
    await dispatcher.handle_v2(delivery, worker_id="v2-worker")

    assert tasks.message_recoveries == [(task.id, native_reader)]
    assert executions == ([task.id] if status == "claimed" else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["message", "message_recover", "approval_resume", "approval_recover", "framework_reconcile"])
@pytest.mark.parametrize("reader,format_version", [(reader, version)
    for reader in range(15, FrameworkWorkerCapabilities("a" * 64, True).message_stage_max_version + 1)
    for version in (reader, reader + 1)])
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


def stage_record(**changes):
    from src.schemas.framework_message_stage import FrameworkMessageStage

    now = datetime.now(timezone.utc)
    value = FrameworkMessageStage(task_id="task-1", binding_id="binding-1", input_sha256="input",
        user_message_id="message-1", phase="submitted", created_at=now, updated_at=now).model_dump(mode="json")
    value.update(changes)
    return value


STAGE_MATERIAL_KEYS = (
    "post_commit_memory_intent", "post_commit_memory_plan", "post_commit_memory_execution",
    "approval_graph_inputs", "graph_input_asset", "observation_projection_asset",
    "observation_projection_commits", "event_projection_commits", "snapshot_projection_commit",
    "runtime_result_asset", "application_projection_commit", "semantic_input",
)


@pytest.mark.parametrize("key", STAGE_MATERIAL_KEYS)
@pytest.mark.parametrize("new_field", [{"schema_version": 99}, {"future_field": "private-material-input"}])
def test_known_stage_materials_use_the_installed_strict_codecs(key, new_field):
    from src.runtime.framework_message_stages import message_stage_material_readers

    assert set(message_stage_material_readers()) == set(STAGE_MATERIAL_KEYS)
    material = {"approval-task": new_field} if key == "approval_graph_inputs" else new_field
    # These are deliberately incomplete future formats. No valid asset or
    # recovery proof is implied by their presence in a manifest.
    value = stage_record(source_manifest={key: material})
    with pytest.raises(TaskDeferred, match="framework_message_stage_format_unavailable") as caught:
        FrameworkWorkerCapabilities("a" * 64, True).require_message_stage_format(value)
    assert caught.value.__suppress_context__ and "private-material-input" not in str(caught.value)


@pytest.mark.parametrize("version", range(1, 30))
def test_stage_format_reader_retains_each_existing_version(version):
    from src.runtime.framework_message_stages import validate_message_stage

    value = stage_record(schema_version=version)
    FrameworkWorkerCapabilities("a" * 64, True).require_message_stage_format(value)
    assert validate_message_stage(value).schema_version == version


def test_missing_stage_version_keeps_the_original_default_without_rewriting_input():
    from src.runtime.framework_message_stages import validate_message_stage

    value = stage_record()
    del value["schema_version"]
    FrameworkWorkerCapabilities("a" * 64, True).require_message_stage_format(value)
    assert validate_message_stage(value).schema_version == 3 and "schema_version" not in value


@pytest.mark.parametrize("version,reason", [(None, "framework_message_stage_format_unavailable"),
    *[(value, "framework_message_stage_unavailable") for value in (True, "3", 3.0, 0, 30)]])
def test_stage_format_requires_an_explicit_supported_integer_version(version, reason):
    with pytest.raises(TaskDeferred, match=reason):
        FrameworkWorkerCapabilities("a" * 64, True).require_message_stage_format(stage_record(schema_version=version))


@pytest.mark.parametrize("value", [None, [], "future-stage", 3])
def test_nonobject_stage_is_not_confused_with_a_missing_stage(value):
    with pytest.raises(TaskDeferred, match="framework_message_stage_format_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True).require_message_stage_format(value)


@pytest.mark.parametrize("key", ["recoveries", "approval_finalizations", "approval_settlements"])
def test_unknown_stage_lineage_fields_defer_before_execution(key):
    from src.schemas.framework_message_stage import FrameworkMessageRecoveryLink

    link = FrameworkMessageRecoveryLink(task_id="next", predecessor_task_id="task-1",
        predecessor_attempt_id="attempt-1", predecessor_owner_epoch=1, budget_sha256="budget",
        admitted_at=datetime.now(timezone.utc)).model_dump(mode="json")
    link["resume_phase"] = {"recoveries": "submitted", "approval_finalizations": "finalizing",
        "approval_settlements": "settling"}[key]
    link["future_field"] = "private-lineage-input"
    with pytest.raises(TaskDeferred, match="framework_message_stage_format_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True).require_message_stage_format(stage_record(**{key: [link]}))


def test_format_compatibility_neither_recurses_into_metadata_nor_grants_recovery_authority():
    from src.runtime.framework_message_stages import validate_message_stage, FrameworkMessageStageRecoveryRequired
    from src.schemas.framework_approval import approval_value_hash

    manifest = {"application_metadata": {"schema_version": 99, "future_field": True,
        "runtime_result_asset": {"schema_version": 99}}}
    value = stage_record(phase="orchestrating", source_manifest=manifest,
        source_sha256=approval_value_hash(manifest))
    caps = FrameworkWorkerCapabilities("a" * 64, True)
    caps.require_message_stage_format(value)
    assert validate_message_stage(value).source_manifest == manifest
    value["source_sha256"] = "0" * 64
    caps.require_message_stage_format(value)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="asset integrity"):
        validate_message_stage(value)


def approval_receipt_record(**changes):
    from src.schemas.framework_approval import FrameworkDecisionReceipt

    value = FrameworkDecisionReceipt(decision_id="decision", approval_id="approval", decision={"type": "approve"},
        decision_hash="hash", state="queued", created_at=datetime.now(timezone.utc)).model_dump(mode="json")
    value.update(changes)
    return value


@pytest.mark.parametrize("changes", [{"future_field": None},
    {"proof": {"format_version": "langgraph-next"}},
    {"proof": {"application": {"format_version": "langgraph-next"}}}])
def test_receipt_format_reader_rejects_strict_fields_and_declared_future_native_protocol(changes):
    with pytest.raises(TaskDeferred, match="framework_approval_receipt_format_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True).require_approval_journal_format(
            "receipt", approval_receipt_record(**changes))


@pytest.mark.parametrize("version", [1, 2])
def test_receipt_retains_both_tool_projection_readers(version):
    from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY
    from tests.test_framework_tool_projection import sample

    binding, source, material = sample()
    if version == 2:
        material = FrameworkToolProjection.capture(binding=binding, source=source, record=material.record,
            message=material.message, model_content=material.model_content,
            runtime_message={"role": "tool", "tool_call_id": material.record.call_id,
                "name": material.record.tool_key, "content": "original-runtime-slot"})
    proof = {"application": {TOOL_PROJECTION_KEY: material.model_dump(mode="json")}}
    FrameworkWorkerCapabilities("a" * 64, True).require_approval_journal_format("receipt", approval_receipt_record(proof=proof))


@pytest.mark.parametrize("change", ["schema_version", "future_field", "digest"])
def test_receipt_typed_material_uses_original_codec_without_relabeling_integrity_errors(change):
    from pydantic import ValidationError
    from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY
    from tests.test_framework_tool_projection import sample

    _, _, original = sample()
    material = original.model_dump(mode="json")
    material[change if change != "digest" else "model_content"] = 99 if change == "schema_version" else "private-input"
    value = approval_receipt_record(proof={"application": {TOOL_PROJECTION_KEY: material}})
    expected = ValidationError if change == "digest" else TaskDeferred
    with pytest.raises(expected):
        FrameworkWorkerCapabilities("a" * 64, True).require_approval_journal_format("receipt", value)


def test_receipt_proof_metadata_is_not_a_recursive_format_whitelist():
    proof = {"format_version": "langgraph-1.2.12", "metadata": {"format_version": "future", "schema_version": 99},
        "application": {"format_version": "langgraph-1.2.12", "metadata": {"future_field": True}}}
    FrameworkWorkerCapabilities("a" * 64, True).require_approval_journal_format("receipt", approval_receipt_record(proof=proof))


def test_receipt_format_accepts_the_mapping_contract_of_database_rows():
    from types import MappingProxyType

    FrameworkWorkerCapabilities("a" * 64, True).require_approval_journal_format(
        "receipt", MappingProxyType(approval_receipt_record()))

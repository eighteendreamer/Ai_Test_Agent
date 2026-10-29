"""New-turn route rollback is not bound-runtime shutdown or task cancellation."""

from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_settings import BaseSettings

from src.application.deep_agents import DeepAgentRuntimeResult
from src.application.runtime.runtime_service import RuntimeService
from src.core.config import DeepAgentsConfig, Settings
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_v2_worker_contract import binding, command, stored_task, _Tasks
from tests.test_deep_agent_runtime_adapter import _ModelRuntimeStub, _runtime_request, _runtime_session


class LegacyGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state):
        self.calls.append(state["turn_id"])
        return {**state, "continue_loop": False, "final_response": "legacy-route", "termination_reason": "completed"}


class Adapter:
    def __init__(self):
        self.calls = []

    async def execute(self, request):
        self.calls.append(request.turn_id)
        return DeepAgentRuntimeResult(output_text="framework-route")


def runtime_for(*, enabled=True, new_turns_enabled=True):
    graph, adapter = LegacyGraph(), Adapter()
    runtime = RuntimeService(graph=graph, model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=adapter, deep_agent_enabled=enabled, deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_new_turns_enabled=new_turns_enabled)
    return runtime, graph, adapter


def test_configuration_can_close_new_turns_without_disabling_bound_runtime():
    config = DeepAgentsConfig(enabled=True, checkpoint_enabled=True, new_turns_enabled=False)
    assert config.model_dump().get("new_turns_enabled") is False, "The rollback control was silently discarded"
    assert config.enabled and config.checkpoint_enabled
    assert DeepAgentsConfig().model_dump().get("new_turns_enabled") is True
    assert DeepAgentsConfig().enabled is False, "The new control must not enable production by default"


@pytest.mark.asyncio
async def test_closed_new_turn_route_uses_legacy_without_invoking_framework(caplog):
    runtime, graph, adapter = runtime_for(new_turns_enabled=False)
    session = _runtime_session("route-rollback-session")
    caplog.set_level(logging.INFO, logger="src.application.runtime.runtime_service")
    result = await runtime.execute_turn(session, _runtime_request(session.id, "new-turn"))
    assert result.output_text == "legacy-route", "Stopping new admission still invoked the framework"
    assert graph.calls == ["new-turn"] and adapter.calls == []
    assert not runtime.is_interrupt_requested(session.id)
    events = [record for record in caplog.records if record.message == "deep_agent_new_turn_route_disabled"]
    assert len(events) == 1 and events[0].turn_id == "new-turn"
    assert events[0].recovery_reason == "new_turn_admission_disabled"


def test_production_runtime_assembly_passes_the_distinct_admission_setting():
    """Wiring audit only; not a full production lifespan/E4 assertion."""
    source = Path(__file__).resolve().parents[1] / "src" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "RuntimeService"]
    assert len(calls) == 1
    keywords = {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
    assert keywords.get("deep_agent_new_turns_enabled") == "settings.deep_agents.new_turns_enabled"
    assert keywords["deep_agent_enabled"] == "settings.deep_agents.enabled"


@pytest.mark.parametrize("new_turns_enabled", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("bound", [True, False])
def test_binding_and_admission_have_separate_capability_rules(new_turns_enabled, enabled, bound):
    runtime, graph, adapter = runtime_for(enabled=enabled, new_turns_enabled=new_turns_enabled)
    request = _runtime_request("session-1", "turn-1")
    if bound:
        request.runtime_binding = binding()
    if bound and not enabled:
        with pytest.raises(RuntimeError, match="bound Deep Agents runtime is unavailable"):
            runtime._should_use_deep_agent(request)
    else:
        assert runtime._should_use_deep_agent(request) is (enabled and (bound or new_turns_enabled))
    assert graph.calls == adapter.calls == []


@pytest.mark.parametrize("drift", [{"session_id": "foreign"}, {"turn_id": "foreign"}, {"runtime_kind": "langgraph"}])
def test_closed_admission_does_not_fallback_corrupt_or_unsupported_binding(drift):
    runtime, graph, adapter = runtime_for(new_turns_enabled=False)
    request = _runtime_request("session-1", "turn-1")
    request.runtime_binding = binding(**drift)
    with pytest.raises(ValueError):
        runtime._should_use_deep_agent(request)
    assert graph.calls == adapter.calls == []


def test_closed_admission_keeps_required_bound_adapter_check():
    runtime, graph, adapter = runtime_for(new_turns_enabled=False)
    runtime._deep_agent_bound_runtime_adapter = None
    request = _runtime_request("session-1", "turn-1")
    request.runtime_binding = binding()
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime._should_use_deep_agent(request)
    assert graph.calls == adapter.calls == []


@pytest.mark.parametrize("new_turns_enabled", [True, False])
@pytest.mark.asyncio
async def test_new_routing_keeps_mode_filter_and_ignores_request_flag_override(new_turns_enabled):
    runtime, graph, adapter = runtime_for(new_turns_enabled=new_turns_enabled)
    session = _runtime_session("request-route-override")
    request = _runtime_request(session.id, "other-mode-turn")
    request.mode_key = "default"
    request.context.update(deep_agent_new_turns_enabled=True, new_turns_enabled=True,
        deep_agents={"enabled": True, "new_turns_enabled": True})
    result = await runtime.execute_turn(session, request)
    assert result.output_text == "legacy-route" and adapter.calls == []
    if not new_turns_enabled:
        request.mode_key = "code_review"
        result = await runtime.execute_turn(session, request)
        assert result.output_text == "legacy-route" and adapter.calls == []


@pytest.mark.asyncio
async def test_route_change_does_not_cancel_an_already_admitted_invocation():
    runtime, graph, adapter = runtime_for()
    entered, release = asyncio.Event(), asyncio.Event()
    execute = adapter.execute

    async def in_flight(request):
        entered.set()
        await release.wait()
        return await execute(request)

    adapter.execute = in_flight
    session = _runtime_session("active-route-change")
    active = asyncio.create_task(runtime.execute_turn(session, _runtime_request(session.id, "already-admitted")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        runtime._deep_agent_new_turns_enabled = False
        release.set()
        result = await asyncio.wait_for(active, timeout=5)
        assert result.output_text == "framework-route" and adapter.calls == ["already-admitted"]
        assert not runtime.is_interrupt_requested(session.id)
        later = await runtime.execute_turn(session, _runtime_request(session.id, "after-rollback"))
        assert later.output_text == "legacy-route" and graph.calls == ["after-rollback"]
    finally:
        release.set()
        if not active.done():
            active.cancel()
        await asyncio.gather(active, return_exceptions=True)


@pytest.mark.parametrize("flag", ["true", "false"])
def test_nested_environment_parses_new_route_without_changing_capability(monkeypatch, flag):
    class Probe(BaseSettings):
        deep_agents: DeepAgentsConfig
        model_config = Settings.model_config

    monkeypatch.setenv("DEEP_AGENTS__ENABLED", "true")
    monkeypatch.setenv("DEEP_AGENTS__CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("DEEP_AGENTS__NEW_TURNS_ENABLED", flag)
    settings = Probe(_env_file=None, deep_agents={})
    assert settings.deep_agents.enabled is True and settings.deep_agents.checkpoint_enabled is True
    assert settings.deep_agents.new_turns_enabled is (flag == "true")


@pytest.mark.parametrize("enabled,checkpoint,new_turns", [(True, True, False), (True, True, True), (False, True, False), (True, False, False)])
@pytest.mark.asyncio
async def test_v2_worker_cli_keeps_bound_capability_when_only_new_admission_is_closed(monkeypatch, enabled, checkpoint, new_turns):
    """Real CLI admission/dispatcher; queue, lifespan and domain execution are doubles."""
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.cli import run_agent_v2_worker as cli
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities

    settings = SimpleNamespace(deep_agents=DeepAgentsConfig(enabled=enabled,
        checkpoint_enabled=checkpoint, new_turns_enabled=new_turns),
        database=SimpleNamespace(redis_url="redis://unused"), orchestration=SimpleNamespace(
            agent_task_stream="route-test:legacy", agent_task_v2_stream="route-test:v2",
            agent_task_v2_consumer_group="route-test-workers", redis_task_socket_timeout_seconds=1,
            redis_task_retention_seconds=60, redis_task_retention_interval_seconds=10, redis_task_retention_batch_size=10))
    artifact = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    original = binding(dependency_lock_sha256=artifact.dependency_lock_sha256)
    tasks = _Tasks(stored_task(original))
    dispatcher = AgentTaskDispatcher(settings, tasks, None, None)
    effects, closed = [], []

    async def execute(task):
        effects.append(task.runtime_binding)

    dispatcher._execute = execute

    @asynccontextmanager
    async def lifespan(app):
        yield

    class Queue:
        def __init__(self, *args, **kwargs):
            self.stream = kwargs["stream"]

        async def connect(self):
            pass

        async def close(self):
            closed.append("queue")

    class Worker:
        @classmethod
        def from_settings(cls, queue, *, settings, consumer, handler):
            assert queue.stream == settings.orchestration.agent_task_v2_stream
            assert consumer in {"route-rollback-worker", "unavailable-worker"}
            worker = cls()
            worker.handler = handler
            return worker

        async def run_once(self):
            await self.handler(QueuedTask("delivery", command(original).model_dump(mode="json")))

        def stop(self):
            closed.append("worker")

    app = SimpleNamespace(state=SimpleNamespace(agent_task_dispatcher=dispatcher),
        router=SimpleNamespace(lifespan_context=lifespan))
    monkeypatch.setattr(cli, "app", app)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "RedisTaskQueue", Queue)
    monkeypatch.setattr(cli, "RedisTaskWorker", Worker)
    if enabled and checkpoint:
        await cli._run(once=True, worker_id="route-rollback-worker")
        assert tasks.claimed == ["route-rollback-worker"] and effects == [original]
    else:
        with pytest.raises(TaskDeferred, match="framework_deepagents_unavailable"):
            await cli._run(once=True, worker_id="unavailable-worker")
        assert tasks.claimed == effects == []
    assert closed == ["worker", "queue"]

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.application.deep_agents import (
    DeepAgentRuntimeAdapter,
    DeepAgentRuntimeRequest,
    DeepAgentRuntimeResult,
)
from src.application.deep_agents.runtime_adapter import _project_root_from_context
from src.application.deep_agents.read_only_backend import build_read_only_filesystem_backend
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.runtime.runtime_service import RuntimeService
from src.application.sessions.session_service import SessionService
from src.core.config import DeepAgentsConfig
from src.domain.models import SessionRecord
from src.modes.code_review_mode.models import ProjectSource
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.session import ExecutionRequest, RuntimeMode, SessionMode, SessionStatus


class _FakeAgent:
    def __init__(self, calls: list[dict]):
        self._calls = calls

    async def ainvoke(self, payload, *, config):
        self._calls.append({"payload": payload, "config": config})
        return {
            "messages": [
                {"role": "user", "content": "inspect"},
                {"role": "assistant", "content": "DA_E1_OK"},
            ]
        }


@pytest.mark.asyncio
async def test_da_e1_maps_messages_and_keeps_business_tools_empty():
    calls: list[dict] = []
    factory_calls: list[dict] = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return _FakeAgent(calls)

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=factory,
    )
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            model_key="qwen-test",
            system_prompt="pilot",
            messages=[{"role": "user", "content": "inspect"}],
        )
    )

    assert result.output_text == "DA_E1_OK"
    assert result.metadata["stage"] == "DA-E1"
    assert factory_calls[0]["tools"] == []
    assert calls[0]["config"]["configurable"] == {"thread_id": "turn-1"}
    assert calls[0]["config"]["run_name"] == "enterprise_ai_qa_agent.deep_agent_turn"
    assert calls[0]["config"]["metadata"]["trace_id"] == "trace-1"
    assert calls[0]["payload"]["messages"] == [{"role": "user", "content": "inspect"}]


@pytest.mark.asyncio
async def test_da_e1_rejects_missing_assistant_output():
    class EmptyAgent:
        async def ainvoke(self, payload, *, config):
            return {"messages": [{"role": "user", "content": "only input"}]}

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=lambda **_kwargs: EmptyAgent(),
    )

    with pytest.raises(RuntimeError, match="no assistant message"):
        await adapter.execute(
            DeepAgentRuntimeRequest(
                session_id="session-1",
                turn_id="turn-1",
                trace_id="trace-1",
                model_key="qwen-test",
                system_prompt="pilot",
                messages=[],
            )
        )


def test_da_e2_resolves_only_explicit_local_project_root():
    assert _project_root_from_context({"project_root": "C:/workspace"}) == "C:/workspace"
    assert _project_root_from_context(
        {"project_source": {"source_type": "local", "root_path": "C:/repo"}}
    ) == "C:/repo"
    assert _project_root_from_context(
        {"project_source": {"source_type": "ssh", "root_path": "/srv/repo"}}
    ) == ""


def test_deep_agents_nested_env_uses_json_list(monkeypatch):
    class DeepAgentsSettingsProbe(BaseSettings):
        deep_agents: DeepAgentsConfig

        model_config = SettingsConfigDict(env_nested_delimiter="__")

    monkeypatch.setenv("DEEP_AGENTS__PILOT_MODE_KEYS", '["code_review"]')
    settings = DeepAgentsSettingsProbe(
        _env_file=None,
        deep_agents=DeepAgentsConfig(),
    )
    assert settings.deep_agents.pilot_mode_keys == ["code_review"]


def test_deep_agents_turn_timeout_must_be_positive():
    with pytest.raises(ValueError, match="greater than zero"):
        DeepAgentsConfig(turn_timeout_seconds=0)


@pytest.mark.parametrize(
    "field",
    [
        "max_subagent_calls_per_turn",
        "subagent_model_call_limit",
        "subagent_tool_call_limit",
    ],
)
def test_deep_agents_subagent_limits_must_be_positive(field: str):
    with pytest.raises(ValueError, match="greater than zero"):
        DeepAgentsConfig(**{field: 0})


async def _resolved_model():
    return object()


async def _async_value(value):
    return value


def test_da_e2_read_matches_legacy_project_file_reader(tmp_path: Path):
    """The pilot must preserve the existing code-review reader's text contract."""
    pytest.importorskip("deepagents")
    target = tmp_path / "sample.py"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    source = ProjectSource(source_type="local", root_path=str(tmp_path), project_name="fixture")

    legacy = ToolRuntimeService._read_local_project_file(
        object(), source, file_path="sample.py", start_line=2, end_line=3, max_chars=16000
    )
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    current = backend.read("/sample.py", offset=1, limit=2)

    assert current.error is None
    assert current.file_data is not None
    # The legacy reader uses ``splitlines()`` and drops the slice's final
    # newline; the official backend preserves it.  Compare the actual text
    # lines while making this presentation-only difference explicit.
    assert current.file_data["content"].rstrip("\n") == legacy["content"]
    assert legacy["file"]["path"] == "sample.py"
    assert legacy["file"]["start_line"] == 2
    assert legacy["file"]["end_line"] == 3


@pytest.mark.asyncio
async def test_da_e2_read_and_grep_are_safe_under_concurrency(tmp_path: Path):
    """Concurrent read-only calls must not share mutable request or Skill state."""
    pytest.importorskip("deepagents")
    target = tmp_path / "parallel.txt"
    target.write_text("needle\n" * 200, encoding="utf-8")
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)

    async def read_once():
        return await asyncio.to_thread(backend.read, "/parallel.txt", 0, 5)

    async def grep_once():
        return await asyncio.to_thread(backend.grep, "needle", "/parallel.txt", max_count=3)

    reads, greps = await asyncio.gather(
        asyncio.gather(*(read_once() for _ in range(12))),
        asyncio.gather(*(grep_once() for _ in range(12))),
    )
    assert all(item.error is None for item in reads)
    assert all(item.file_data and "needle" in item.file_data["content"] for item in reads)
    assert all(item.error is None and len(item.matches) == 3 for item in greps)


@pytest.mark.asyncio
async def test_da_e2_concurrent_skill_staging_is_request_scoped(tmp_path: Path):
    pytest.importorskip("deepagents")

    async def configure(key: str):
        skills_root = tmp_path / key
        skill_dir = skills_root / key
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {key}\ndescription: {key} only\n---\n\n# {key}\n",
            encoding="utf-8",
        )
        adapter = DeepAgentRuntimeAdapter(
            model_resolver=lambda _key: _resolved_model(),
            skill_registry=SkillRegistry(skills_root=skills_root),
        )
        kwargs, cleanup = await asyncio.to_thread(
            adapter._configure_harness,
            object(),
            read_only_filesystem_enabled=True,
            read_only_max_file_size_mb=1,
            read_only_max_output_chars=120000,
            context={"project_root": str(tmp_path), "skill_keys": [key]},
        )
        return key, kwargs["backend"], cleanup

    configured = await asyncio.gather(configure("alpha"), configure("beta"))
    try:
        for key, backend, _cleanup in configured:
            own = backend.read(f"/skills/{key}/SKILL.md")
            other = "beta" if key == "alpha" else "alpha"
            foreign = backend.read(f"/skills/{other}/SKILL.md")
            assert own.error is None and own.file_data and f"# {key}" in own.file_data["content"]
            assert foreign.error is not None
    finally:
        for _key, _backend, cleanup in configured:
            assert cleanup is not None
            cleanup()


@pytest.mark.asyncio
async def test_da_e2_official_agent_reads_project_file(tmp_path: Path):
    """Exercise the real Deep Agents graph, not only the backend in isolation."""
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    class ToolCapableFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolCapableFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/README.md"},
                        "id": "read-readme",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="DA_E2_AGENT_READ_OK"),
        ]
    )
    (tmp_path / "README.md").write_text("# Read-only fixture\n", encoding="utf-8")
    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda _key: _async_value(model))
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-e2",
            turn_id="turn-e2",
            trace_id="trace-e2",
            model_key="fake-tool-capable",
            system_prompt="Read README.md and report completion.",
            messages=[{"role": "user", "content": "Read README.md."}],
            context={"project_root": str(tmp_path)},
            read_only_filesystem_enabled=True,
        )
    )
    assert result.output_text == "DA_E2_AGENT_READ_OK"
    assert any(message.get("role") == "tool" for message in result.messages)


@pytest.mark.asyncio
async def test_da_e3_official_todo_state_is_returned(tmp_path: Path):
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    class ToolCapableFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    todos = [
        {"content": "Inspect the relevant files", "status": "completed"},
        {"content": "Summarize the evidence", "status": "completed"},
    ]
    model = ToolCapableFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {"todos": todos},
                        "id": "write-plan",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="DA_E3_TODO_OK"),
        ]
    )
    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda _key: _async_value(model))
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-e3",
            turn_id="turn-e3",
            trace_id="trace-e3",
            model_key="fake-tool-capable",
            system_prompt="Plan and complete the review.",
            messages=[{"role": "user", "content": "Review two concerns."}],
            context={"project_root": str(tmp_path)},
            read_only_filesystem_enabled=True,
            cognitive_planning_enabled=True,
        )
    )
    assert result.output_text == "DA_E3_TODO_OK"
    assert result.metadata["stage"] == "DA-E3"
    assert result.todos == todos
    assert any(message.get("role") == "tool" for message in result.messages)


@pytest.mark.asyncio
async def test_da_e3_official_sync_subagent_is_read_only_and_emits_typed_events(
    tmp_path: Path,
):
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    bound_tool_names: list[list[str]] = []

    class ToolCapableFake(FakeMessagesListChatModel):
        def _get_ls_params(self, **kwargs):
            return {
                "ls_provider": "da-e3-subagent-test",
                "ls_model_name": "tool-capable-fake",
                "ls_model_type": "chat",
            }

        def bind_tools(self, tools, **kwargs):
            bound_tool_names.append(
                [str(getattr(tool, "name", "")) for tool in tools]
            )
            return self

    model = ToolCapableFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "Inspect the bounded fixture.",
                            "subagent_type": "code-review-researcher",
                        },
                        "id": "delegate-review",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="SUBAGENT_EVIDENCE"),
            AIMessage(content="DA_E3_SUBAGENT_OK"),
        ]
    )
    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda _key: _async_value(model))
    streamed_events: list[dict] = []
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-e3-subagent",
            turn_id="turn-e3-subagent",
            trace_id="trace-e3-subagent",
            model_key="fake-tool-capable",
            system_prompt="Delegate one bounded evidence task.",
            messages=[{"role": "user", "content": "Review the fixture."}],
            context={"project_root": str(tmp_path)},
            read_only_filesystem_enabled=True,
            cognitive_subagents_enabled=True,
            on_subagent_event=streamed_events.append,
        )
    )

    # The declarative subagent compiler copies FakeMessagesListChatModel; its
    # response index sharing differs depending on earlier profile registration.
    # Either non-empty terminal response is valid for this contract test.
    assert result.output_text in {"SUBAGENT_EVIDENCE", "DA_E3_SUBAGENT_OK"}
    assert result.metadata["stage"] == "DA-E3"
    assert [item["type"] for item in result.subagent_events] == [
        "started",
        "completed",
    ], {"messages": result.messages, "bound_tool_names": bound_tool_names}
    assert streamed_events == result.subagent_events
    assert all(
        item["name"] == "code-review-researcher"
        for item in result.subagent_events
    )
    assert any("task" in names for names in bound_tool_names)
    subagent_tool_sets = [names for names in bound_tool_names if "task" not in names]
    assert ["ls", "read_file", "glob", "grep"] in subagent_tool_sets
    assert all(
        not ({"write_file", "edit_file", "delete", "execute", "task"} & set(names))
        for names in subagent_tool_sets
    )


def test_da_e3_subagent_limits_are_applied_to_official_middleware(tmp_path: Path):
    pytest.importorskip("deepagents")
    from langchain.agents.middleware import (
        ModelCallLimitMiddleware,
        ToolCallLimitMiddleware,
    )

    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda _key: _resolved_model())
    kwargs, cleanup = adapter._configure_harness(
        object(),
        read_only_filesystem_enabled=True,
        read_only_max_file_size_mb=1,
        read_only_max_output_chars=120000,
        cognitive_subagents_enabled=True,
        max_subagent_calls_per_turn=1,
        subagent_model_call_limit=4,
        subagent_tool_call_limit=7,
        context={"project_root": str(tmp_path)},
    )
    try:
        parent_limiters = [
            item
            for item in kwargs["middleware"]
            if isinstance(item, ToolCallLimitMiddleware) and item.tool_name == "task"
        ]
        assert len(parent_limiters) == 1
        assert parent_limiters[0].run_limit == 1
        assert parent_limiters[0].exit_behavior == "continue"

        subagent = kwargs["subagents"][0]
        assert subagent["name"] == "code-review-researcher"
        assert subagent["tools"] == []
        model_limiters = [
            item
            for item in subagent["middleware"]
            if isinstance(item, ModelCallLimitMiddleware)
        ]
        tool_limiters = [
            item
            for item in subagent["middleware"]
            if isinstance(item, ToolCallLimitMiddleware)
        ]
        assert model_limiters[0].run_limit == 4
        assert tool_limiters[0].run_limit == 7
    finally:
        if cleanup is not None:
            cleanup()


@pytest.mark.asyncio
async def test_da_e3_runtime_maps_todos_to_existing_plan_event():
    class ModelRuntimeStub:
        def get_model_config(self, model_key):
            return None

        def get_default_model_config(self):
            return None

        @asynccontextmanager
        async def stream_handler(self, handler):
            yield

    class AdapterStub:
        async def execute(self, request):
            assert request.cognitive_planning_enabled is True
            return DeepAgentRuntimeResult(
                output_text="DA_E3_RUNTIME_OK",
                metadata={"stage": "DA-E3"},
                todos=[
                    {"content": "Inspect evidence", "status": "completed"},
                    {"content": "Report findings", "status": "completed"},
                ],
            )

    runtime = RuntimeService(
        graph=None,
        model_runtime_service=ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=AdapterStub(),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_cognitive_planning_enabled=True,
    )
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-e3-runtime",
        title="DA-E3 runtime",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="code_review",
        created_at=now,
        updated_at=now,
    )
    result = await runtime.execute_turn(
        session,
        ExecutionRequest(
            turn_id="turn-e3-runtime",
            session_id=session.id,
            user_message="Review the evidence.",
            normalized_input="Review the evidence.",
            mode_key="code_review",
            model_key="test-model",
        ),
    )
    assert result.output_text == "DA_E3_RUNTIME_OK"
    assert result.state["plan_steps"] == ["Inspect evidence", "Report findings"]
    plan_events = [event for event in result.events if event.type == "graph.plan_built"]
    assert len(plan_events) == 1
    assert plan_events[0].payload["todos"][0]["status"] == "completed"
    assert result.state["worker_dispatches"] == []


@pytest.mark.asyncio
async def test_da_e3_runtime_projects_subagent_events_without_worker_dispatch():
    class AdapterStub:
        async def execute(self, request):
            assert request.cognitive_subagents_enabled is True
            started = {
                "type": "started",
                "name": "code-review-researcher",
                "run_id": "subagent-run",
                "parent_run_id": "task-run",
            }
            completed = {**started, "type": "completed"}
            request.on_subagent_event(started)
            request.on_subagent_event(completed)
            return DeepAgentRuntimeResult(
                output_text="SUBAGENT_RUNTIME_OK",
                metadata={"stage": "DA-E3"},
                subagent_events=[started, completed],
            )

    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=AdapterStub(),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_cognitive_subagents_enabled=True,
    )
    result = await runtime.execute_turn(
        _runtime_session("session-subagent-runtime"),
        _runtime_request("session-subagent-runtime", "turn-subagent-runtime"),
    )

    assert result.output_text == "SUBAGENT_RUNTIME_OK"
    assert result.state["worker_dispatches"] == []
    assert result.state["cognitive_subagent_events"][1]["type"] == "completed"
    assert [
        event.type
        for event in result.events
        if event.type.startswith("graph.subagent_")
    ] == ["graph.subagent_started", "graph.subagent_completed"]
    assert all(
        event.payload["persistent_worker"] is False
        for event in result.events
        if event.type.startswith("graph.subagent_")
    )


@pytest.mark.asyncio
async def test_deep_agent_turn_is_nested_under_existing_observability_root():
    class AdapterStub:
        async def execute(self, request):
            return DeepAgentRuntimeResult(output_text="OBSERVED_DEEP_AGENT_OK")

    class TraceScopeStub:
        def __init__(self):
            self.outputs = []
            self.outcomes = []

        def reference(self):
            return {"run_id": "langsmith-run", "trace_id": "langsmith-trace"}

        def set_outputs(self, outputs):
            self.outputs.append(outputs)

        def set_outcome(self, **outcome):
            self.outcomes.append(outcome)

    class ObservabilityStub:
        def __init__(self):
            self.scope = TraceScopeStub()
            self.contexts = []

        @contextmanager
        def trace_turn(self, context, *, inputs):
            self.contexts.append((context, inputs))
            yield self.scope

    observability = ObservabilityStub()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        observability_service=observability,
        deep_agent_runtime_adapter=AdapterStub(),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
    )
    result = await runtime.execute_turn(
        _runtime_session("session-observed-deep-agent"),
        _runtime_request("session-observed-deep-agent", "turn-observed-deep-agent"),
    )

    assert result.output_text == "OBSERVED_DEEP_AGENT_OK"
    assert len(observability.contexts) == 1
    assert observability.scope.outputs[0]["termination_reason"] == "completed"
    assert observability.scope.outcomes == [
        {"termination_reason": "completed", "control_state": "completed"}
    ]
    trace_events = [
        event for event in result.events if event.type == "observability.trace_linked"
    ]
    assert len(trace_events) == 1
    assert trace_events[0].payload["run_id"] == "langsmith-run"


def _runtime_session(session_id: str) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        title="Deep Agents bounded turn",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="code_review",
        created_at=now,
        updated_at=now,
    )


def _runtime_request(session_id: str, turn_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        turn_id=turn_id,
        session_id=session_id,
        user_message="Review the evidence.",
        normalized_input="Review the evidence.",
        mode_key="code_review",
        model_key="test-model",
    )


class _ModelRuntimeStub:
    def get_model_config(self, model_key):
        return None

    def get_default_model_config(self):
        return None

    @asynccontextmanager
    async def stream_handler(self, handler):
        yield


@pytest.mark.asyncio
async def test_deep_agent_interrupt_cancels_active_invocation_and_returns_terminal_snapshot():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingAdapter:
        async def execute(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    control = RuntimeControlRegistry()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=control,
        deep_agent_runtime_adapter=BlockingAdapter(),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_turn_timeout_seconds=30,
    )
    execution = asyncio.create_task(
        runtime.execute_turn(
            _runtime_session("session-interrupt"),
            _runtime_request("session-interrupt", "turn-interrupt"),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    assert control.has_interruptible_task("session-interrupt") is True

    runtime.request_interrupt("session-interrupt", "operator requested stop")
    result = await asyncio.wait_for(execution, timeout=1)

    assert cancelled.is_set()
    assert result.state["termination_reason"] == "interrupted"
    assert result.state["interrupt_reason"] == "operator requested stop"
    assert result.pending_turn == {}
    assert result.snapshot.stage == "interrupted"
    assert any(event.type == "runtime.interrupt_applied" for event in result.events)
    assert control.has_interruptible_task("session-interrupt") is False


@pytest.mark.asyncio
async def test_deep_agent_timeout_cancels_invocation_without_leaking_task():
    cancelled = asyncio.Event()

    class BlockingAdapter:
        async def execute(self, request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    control = RuntimeControlRegistry()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=control,
        deep_agent_runtime_adapter=BlockingAdapter(),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_turn_timeout_seconds=0.01,
    )
    result = await runtime.execute_turn(
        _runtime_session("session-timeout"),
        _runtime_request("session-timeout", "turn-timeout"),
    )

    assert cancelled.is_set()
    assert result.state["termination_reason"] == "interrupted"
    assert result.state["model_response_summary"]["cause"] == "timeout"
    assert result.pending_turn == {}
    assert any(event.type == "runtime.turn_timed_out" for event in result.events)
    assert control.has_interruptible_task("session-timeout") is False

    class DetailStoreStub:
        async def list_approvals(self, session_id):
            return []

        async def get_latest_snapshot(self, session_id):
            return result.snapshot

    interrupted_session = _runtime_session("session-timeout")
    interrupted_session.status = SessionStatus.interrupted
    interrupted_session.metadata = {
        "control": {"is_interrupted": True, "is_resumable": False},
        "pending_turn": {},
    }
    session_service = SessionService(
        store=DetailStoreStub(),
        input_orchestrator_service=object(),
        runtime_service=runtime,
        mode_registry=object(),
    )
    detail = await session_service._to_detail(interrupted_session)
    assert detail.is_resumable is False


@pytest.mark.asyncio
async def test_message_route_interrupts_runtime_when_client_disconnects():
    from types import SimpleNamespace

    from src.api.routes.sessions import send_message

    release = asyncio.Event()

    class SessionServiceStub:
        def __init__(self):
            self.interrupt_payload = None

        async def send_message(self, session_id, payload):
            await release.wait()
            return {"status": "interrupted"}

        async def interrupt_session(self, session_id, payload):
            self.interrupt_payload = payload
            release.set()

    class RequestStub:
        def __init__(self, service):
            self.app = SimpleNamespace(state=SimpleNamespace(session_service=service))

        async def is_disconnected(self):
            return True

    service = SessionServiceStub()
    response = await asyncio.wait_for(
        send_message(
            "session-disconnect",
            SimpleNamespace(),
            RequestStub(service),
        ),
        timeout=1,
    )

    assert response == {"status": "interrupted"}
    assert service.interrupt_payload.source == "client_disconnect"


@pytest.mark.asyncio
async def test_message_route_stops_disconnect_watch_after_normal_completion():
    from types import SimpleNamespace

    from src.api.routes.sessions import send_message

    class SessionServiceStub:
        async def send_message(self, session_id, payload):
            return {"status": "completed"}

    class RequestStub:
        def __init__(self, service):
            self.app = SimpleNamespace(state=SimpleNamespace(session_service=service))

        async def is_disconnected(self):
            return False

    response = await asyncio.wait_for(
        send_message(
            "session-completed",
            SimpleNamespace(),
            RequestStub(SessionServiceStub()),
        ),
        timeout=1,
    )

    assert response == {"status": "completed"}


@pytest.mark.parametrize("path", ["../outside.txt", "/../outside.txt"])
def test_da_e2_rejects_virtual_path_traversal(tmp_path: Path, path: str):
    pytest.importorskip("deepagents")
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    result = backend.read(path)
    assert result.error and "path traversal" in result.error.lower()


def test_da_e2_does_not_expand_home_shorthand(tmp_path: Path):
    pytest.importorskip("deepagents")
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    result = backend.read("~/.env")
    assert result.error == "File '~/.env' not found"


def test_da_e2_bounds_single_line_output(tmp_path: Path):
    pytest.importorskip("deepagents")
    (tmp_path / "long-line.txt").write_text("x" * 1201, encoding="utf-8")
    backend = build_read_only_filesystem_backend(
        tmp_path,
        max_file_size_mb=1,
        max_output_chars=1000,
    )
    result = backend.read("/long-line.txt", offset=0, limit=1)
    assert result.error and "output limit" in result.error.lower()


def test_da_e2_rejects_escape_symlink_and_accepts_in_root_symlink(tmp_path: Path):
    pytest.importorskip("deepagents")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    inside = tmp_path / "inside.txt"
    inside.write_text("inside", encoding="utf-8")
    link_out = tmp_path / "outside-link.txt"
    link_in = tmp_path / "inside-link.txt"
    try:
        link_out.symlink_to(outside)
        link_in.symlink_to(inside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic-link privilege is unavailable: {exc}")

    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    assert backend.read("/inside-link.txt").error is None
    outside_result = backend.read("/outside-link.txt")
    assert outside_result.error and "outside root" in outside_result.error.lower()


def test_da_e2_rejects_escape_directory_junction(tmp_path: Path):
    """Cover the Windows reparse-point variant available without symlink privilege."""
    pytest.importorskip("deepagents")
    if os.name != "nt":
        pytest.skip("directory junctions are Windows-specific")
    outside = tmp_path.parent / f"{tmp_path.name}-junction-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    link = tmp_path / "outside-directory-link"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"directory junction unavailable: {created.stderr or created.stdout}")
    try:
        backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
        result = backend.read("/outside-directory-link/secret.txt")
        assert result.error and "outside root" in result.error.lower()
    finally:
        subprocess.run(
            ["cmd.exe", "/c", "rmdir", "/s", "/q", str(link)],
            capture_output=True,
            text=True,
            check=False,
        )
        (outside / "secret.txt").unlink(missing_ok=True)
        outside.rmdir()


def test_da_e2_rejects_escape_directory_symlink_and_accepts_in_root_directory_symlink(
    tmp_path: Path,
):
    pytest.importorskip("deepagents")
    outside = tmp_path.parent / f"{tmp_path.name}-symlink-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    inside = tmp_path / "inside-directory"
    inside.mkdir()
    (inside / "visible.txt").write_text("inside", encoding="utf-8")
    link_out = tmp_path / "outside-directory-symlink"
    link_in = tmp_path / "inside-directory-symlink"
    try:
        link_out.symlink_to(outside, target_is_directory=True)
        link_in.symlink_to(inside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symbolic-link privilege is unavailable: {exc}")

    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    inside_result = backend.read("/inside-directory-symlink/visible.txt")
    assert inside_result.error is None
    outside_result = backend.read("/outside-directory-symlink/secret.txt")
    assert outside_result.error and "outside root" in outside_result.error.lower()


def test_da_e2_rejects_symbolic_link_loop(tmp_path: Path):
    pytest.importorskip("deepagents")
    first = tmp_path / "loop-first"
    second = tmp_path / "loop-second"
    try:
        first.symlink_to(second)
        second.symlink_to(first)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic-link privilege is unavailable: {exc}")

    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    result = backend.read("/loop-first")
    assert result.error
    assert "symlink loop" in result.error.lower() or "cannot resolve" in result.error.lower()

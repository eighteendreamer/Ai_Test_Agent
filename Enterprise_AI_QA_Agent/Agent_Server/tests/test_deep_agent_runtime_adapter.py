from __future__ import annotations

import asyncio
from copy import deepcopy
from collections import defaultdict
import os
import subprocess
from contextlib import asynccontextmanager, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

from src.application.deep_agents import (
    DeepAgentRuntimeAdapter,
    DeepAgentRuntimeRequest,
    DeepAgentRuntimeResult,
)
from src.application.deep_agents.runtime_adapter import _project_root_from_context
from src.application.model_adapters.tool_adapter import LangChainToolAdapter
from src.application.deep_agents.read_only_backend import build_read_only_filesystem_backend
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.runtime.tool_governance_service import (
    GovernedToolCallContext,
    ToolGovernanceService,
)
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.runtime_service import RuntimeTurnResult
from src.application.runtime.state_persistence import build_pending_turn
from src.schemas.framework_transcript import framework_transcript_asset
from src.application.sessions.session_service import SessionService
from src.core.config import DeepAgentsConfig
from src.domain.models import SessionRecord
from src.modes.code_review_mode.models import ProjectSource
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.session import (
    ExecutionRequest,
    ResumeSessionRequest,
    RuntimeMode,
    SessionMode,
    SessionStatus,
    SessionSnapshot,
)
from src.schemas.session import MessageKind
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


class _FakeAgent:
    def __init__(self, calls: list[dict]):
        self._calls = calls

    async def ainvoke(self, payload, *, config, durability=None):
        self._calls.append({"payload": payload, "config": config})
        return {
            "messages": [
                {"role": "user", "content": "inspect"},
                {"role": "assistant", "content": "DA_E1_OK"},
            ]
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "resume", "continue"])
@pytest.mark.parametrize("stream_events", [False, True])
async def test_all_invocation_paths_use_sync_and_checkpoint_continuation_has_no_history(operation, stream_events):
    captured = []

    class Agent:
        async def ainvoke(self, payload, *, config, durability=None):
            captured.append((payload, durability))
            return {"messages": [AIMessage(content="done")]}

        async def astream_events(self, payload, *, config, version, stream_mode, durability=None):
            captured.append((payload, durability))
            yield {"event": "on_chain_end", "name": config["run_name"],
                "data": {"output": {"messages": [AIMessage(content="done")]}}}

    @asynccontextmanager
    async def checkpoint():
        yield object()

    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda _key: _resolved_model(),
        agent_factory=lambda **kwargs: Agent(), checkpointer_factory=checkpoint)
    options = {"resume": {"interrupt-1": {"type": "approve"}}} if operation == "resume" else {}
    if operation == "continue":
        options["continue_from_checkpoint"] = True
    await adapter.execute(DeepAgentRuntimeRequest(session_id="session", turn_id="turn", trace_id="trace",
        model_key="model", system_prompt="system", messages=[{"role": "user", "content": "new input"}],
        # This deliberately cannot be deserialized: continuations must not read it.
        framework_messages=([{"invalid": "history"}] if operation != "start" else None),
        cognitive_subagents_enabled=stream_events, **options))
    assert captured[0][1] == "sync"
    if operation == "continue":
        assert captured[0][0] is None
    elif operation == "resume":
        from langgraph.types import Command
        assert isinstance(captured[0][0], Command)
        assert captured[0][0].resume == options["resume"]
    else:
        assert captured[0][0]["messages"][0]["content"] == "new input"


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"continue_from_checkpoint": True}, {"resume": {"interrupt-1": {"type": "approve"}}},
])
async def test_checkpoint_continuation_requires_a_checkpointer_before_model_resolution(options):
    async def unexpected_model(key):
        raise AssertionError("model must not be resolved")

    adapter = DeepAgentRuntimeAdapter(model_resolver=unexpected_model)
    with pytest.raises(RuntimeError, match="requires a checkpointer"):
        await adapter.execute(DeepAgentRuntimeRequest(session_id="session", turn_id="turn", trace_id="trace",
            model_key="model", system_prompt="system", messages=[], **options))


@pytest.mark.asyncio
async def test_checkpoint_continuation_cannot_also_submit_a_decision():
    @asynccontextmanager
    async def checkpoint():
        raise AssertionError("must validate before opening the checkpoint")
        yield

    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda key: _resolved_model(), checkpointer_factory=checkpoint)
    with pytest.raises(RuntimeError, match="cannot also submit"):
        await adapter.execute(DeepAgentRuntimeRequest(session_id="session", turn_id="turn", trace_id="trace",
            model_key="model", system_prompt="system", messages=[], continue_from_checkpoint=True,
            resume={"interrupt-1": {"type": "approve"}}))


@pytest.mark.asyncio
async def test_framework_transcript_round_trip_preserves_native_blocks_tool_ids_and_metadata():
    native_messages = [
        HumanMessage(content="inspect", id="human-1"),
        AIMessage(
            content=[{"type": "text", "text": "call lookup", "id": "part-1"}],
            tool_calls=[
                {"name": "lookup", "args": {"q": "x"}, "id": "call-1", "type": "tool_call"}
            ],
            additional_kwargs={"provider_signature": "sig-1"},
            response_metadata={"token_usage": {"input_tokens": 2}},
            id="assistant-1",
        ),
        ToolMessage(
            content={"answer": "ok"},
            tool_call_id="call-1",
            name="lookup",
            status="success",
            id="tool-1",
        ),
        AIMessage(content="done", id="assistant-2"),
    ]
    calls: list[dict] = []

    class NativeAgent:
        async def ainvoke(self, payload, *, config, durability=None):
            calls.append({"payload": payload, "config": config})
            assert isinstance(payload["messages"][1], AIMessage)
            assert payload["messages"][1].tool_calls[0]["id"] == "call-1"
            assert payload["messages"][1].additional_kwargs["provider_signature"] == "sig-1"
            assert payload["messages"][2].tool_call_id == "call-1"
            return {"messages": native_messages}

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=lambda **_kwargs: NativeAgent(),
    )
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            model_key="qwen-test",
            system_prompt="pilot",
            messages=[],
            framework_messages=messages_to_dict(native_messages),
        )
    )

    assert calls
    assert result.framework_messages[1]["data"]["tool_calls"][0]["id"] == "call-1"
    assert result.framework_messages[1]["data"]["content"][0]["id"] == "part-1"
    assert result.framework_messages[1]["data"]["additional_kwargs"]["provider_signature"] == "sig-1"
    assert result.framework_messages[2]["data"]["tool_call_id"] == "call-1"


def test_pending_turn_persists_framework_transcript_separately_from_display_projection():
    state = defaultdict(list)
    state.update(
        {
            "turn_id": "turn-1",
            "trace_id": "trace-1",
            "user_message": "inspect",
            "normalized_input": "inspect",
            "session_mode": "normal",
            "runtime_mode": "interactive",
            "mode_key": "code_review",
            "selected_agent_key": "default",
            "selected_agent_name": "Default",
            "selected_model_key": "model-1",
            "selected_model_name": "Model",
            "selected_model_provider": "test",
            "system_prompt": "pilot",
            "runtime_messages": [{"role": "assistant", "content": "display"}],
            "framework_messages": [
                {"type": "ai", "data": {"content": [{"type": "text", "text": "native"}]}}
            ],
        }
    )

    pending = build_pending_turn(state, "waiting_approval")

    assert pending["runtime_messages"] == [{"role": "assistant", "content": "display"}]
    assert pending["framework_messages"][0]["data"]["content"][0]["text"] == "native"
    assert pending["graph_state"]["framework_messages"] == pending["framework_messages"]


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
            checkpoint_namespace="qa:v2",
        )
    )

    assert result.output_text == "DA_E1_OK"
    assert result.metadata["stage"] == "DA-E1"
    assert factory_calls[0]["tools"] == []
    assert calls[0]["config"]["configurable"] == {
        "thread_id": "turn-1", "checkpoint_ns": "qa:v2",
    }
    assert calls[0]["config"]["run_name"] == "enterprise_ai_qa_agent.deep_agent_turn"
    assert calls[0]["config"]["metadata"]["trace_id"] == "trace-1"
    assert calls[0]["payload"]["messages"] == [{"role": "user", "content": "inspect"}]


@pytest.mark.asyncio
async def test_da_e1_rejects_missing_assistant_output():
    class EmptyAgent:
        async def ainvoke(self, payload, *, config, durability=None):
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
@pytest.mark.parametrize("async_callback", [False, True])
async def test_da_e3_official_sync_subagent_is_read_only_and_emits_typed_events(
    tmp_path: Path, async_callback,
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

    async def persist_event(item):
        await asyncio.sleep(0)
        streamed_events.append(item)

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
            on_subagent_event=persist_event if async_callback else streamed_events.append,
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


def subagent_audit_fixture(tmp_path, on_event, *, before_model=None, delegations=1):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    calls = []

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "subagent-audit-barrier-fixture"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError("Fixture must use the asynchronous SDK model path")

        async def _agenerate(self, messages, **kwargs):
            calls.append(list(messages))
            number = len(calls)
            if before_model is not None:
                await before_model(number)
            reply = (AIMessage(content="", tool_calls=[{"name": "task", "args": {
                "description": "Inspect the bounded fixture", "subagent_type": "code-review-researcher"},
                "id": "audit-barrier-task-" + str(index), "type": "tool_call"} for index in range(delegations)]) if number == 1
                else AIMessage(content="child evidence" if number <= delegations + 1 else "parent answer"))
            return ChatResult(generations=[ChatGeneration(message=reply)])

    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda key: _async_value(Model()))
    request = DeepAgentRuntimeRequest(
        session_id="audit-session", turn_id="audit-turn", trace_id="audit-trace", model_key="fixture",
        system_prompt="Delegate one read-only task", messages=[{"role": "user", "content": "inspect"}],
        context={"project_root": str(tmp_path)}, read_only_filesystem_enabled=True,
        cognitive_subagents_enabled=True, max_subagent_calls_per_turn=delegations, on_subagent_event=on_event)
    return adapter, request, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_event", ["started", "completed"])
async def test_subagent_audit_ack_blocks_the_sdk_before_the_next_model(tmp_path, blocked_event):
    callback_entered, release_callback = asyncio.Event(), asyncio.Event()
    next_model_entered, release_model = asyncio.Event(), asyncio.Event()
    observed = []
    next_call = 2 if blocked_event == "started" else 3

    async def before_model(number):
        if number == next_call:
            next_model_entered.set()
            await release_model.wait()

    async def persist(item):
        if item["type"] == blocked_event:
            callback_entered.set()
            await release_callback.wait()
        observed.append(item)

    adapter, request, calls = subagent_audit_fixture(tmp_path, persist, before_model=before_model)
    running = asyncio.create_task(adapter.execute(request))
    try:
        await asyncio.wait_for(callback_entered.wait(), 10)
        try:
            await asyncio.wait_for(next_model_entered.wait(), 1)
            advanced_without_ack = True
        except TimeoutError:
            advanced_without_ack = False
        assert len(calls) == next_call - 1, "SDK advanced while its original audit ACK was still blocked"
        assert not advanced_without_ack
    finally:
        release_callback.set()
        release_model.set()
        result = await asyncio.wait_for(running, 10)
    assert len(calls) == 3 and result.output_text == "parent answer"
    assert [item["type"] for item in observed] == ["started", "completed"]
    assert result.subagent_events == observed


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_event", ["started", "completed"])
@pytest.mark.parametrize("error_type", ["owner", "io"])
async def test_official_subagent_audit_failure_stops_execution_and_preserves_error(tmp_path, failed_event, error_type):
    from src.application.deep_agents.result_projection import DeepAgentRuntimeError
    from src.runtime.store import ContinuationLeaseLostError

    error = (ContinuationLeaseLostError("injected subagent audit lost owner") if error_type == "owner"
        else ConnectionError("injected subagent audit database unavailable"))

    async def persist(item):
        if item["type"] == failed_event:
            raise error

    adapter, request, calls = subagent_audit_fixture(tmp_path, persist)
    if error_type == "owner":
        with pytest.raises(ContinuationLeaseLostError) as caught:
            await adapter.execute(request)
        assert caught.value is error
    else:
        with pytest.raises(DeepAgentRuntimeError, match="ConnectionError") as caught:
            await adapter.execute(request)
        assert caught.value.__cause__ is error
    assert len(calls) == (1 if failed_event == "started" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("delegations", [1, 2])
async def test_execution_side_audit_keeps_original_sdk_hierarchy_once(tmp_path, monkeypatch, delegations):
    import deepagents

    create = deepagents.create_deep_agent
    raw, observed = [], []

    def factory(**kwargs):
        graph = create(**kwargs)
        stream = graph.astream_events

        async def observe(*args, **kwargs):
            async for event in stream(*args, **kwargs):
                if event.get("name") == "task" and event.get("event") in {"on_tool_start", "on_tool_end"}:
                    raw.append(event)
                yield event

        graph.astream_events = observe
        return graph

    monkeypatch.setattr(deepagents, "create_deep_agent", factory)

    async def persist(item):
        await asyncio.sleep(0)
        observed.append(dict(item))

    adapter, request, calls = subagent_audit_fixture(tmp_path, persist, delegations=delegations)
    result = await adapter.execute(request)
    by_run = {}
    expected = []
    for event in raw:
        run_id = str(event["run_id"])
        if event["event"] == "on_tool_start":
            by_run[run_id] = event["data"]["input"]["subagent_type"]
        expected.append({"type": "started" if event["event"] == "on_tool_start" else "completed",
            "name": by_run[run_id], "run_id": run_id, "parent_run_id": event["parent_ids"][-1]})
    assert len(raw) == len(observed) == delegations * 2
    assert len(by_run) == delegations and len(calls) == delegations + 2
    assert observed == result.subagent_events == expected
    assert result.output_text == "parent answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_event", ["started", "completed"])
async def test_cancelling_blocked_audit_closes_sdk_before_releasing_execution(tmp_path, blocked_event):
    entered, closed = asyncio.Event(), asyncio.Event()

    async def persist(item):
        if item["type"] == blocked_event:
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    adapter, request, calls = subagent_audit_fixture(tmp_path, persist)
    running = asyncio.create_task(adapter.execute(request))
    try:
        await asyncio.wait_for(entered.wait(), 10)
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 10)
    assert closed.is_set()
    assert len(calls) == (1 if blocked_event == "started" else 2)


@pytest.mark.asyncio
async def test_da_e4_business_tool_callback_is_wrapped_and_approval_stops_agent(tmp_path: Path):
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
                        "name": "knowledge-rag",
                        "args": {"query": "governed"},
                        "id": "governed-call",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="MUST_NOT_RUN_AFTER_APPROVAL"),
        ]
    )
    descriptor = ToolRegistry().get("knowledge-rag")

    async def invoke(_descriptor, runtime, arguments):
        pytest.fail("HITL must interrupt before the tool executes")

    from langgraph.checkpoint.memory import InMemorySaver
    saver = InMemorySaver()
    tools = LangChainToolAdapter().to_governed_tools([descriptor], invoke=invoke)
    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _async_value(model),
        checkpointer_factory=lambda: nullcontext(saver),
    )
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-e4",
            turn_id="turn-e4",
            trace_id="trace-e4",
            model_key="fake-tool-capable",
            system_prompt="Use the governed tool.",
            messages=[{"role": "user", "content": "Call the tool."}],
            business_tools=tools,
            interrupt_on={"knowledge-rag": {"allowed_decisions": ["approve", "reject"]}},
        )
    )
    assert result.output_text == ""
    assert result.interrupts[0]["value"]["type"] == "tool_approval"
    assert result.interrupts[0]["value"]["name"] == "knowledge-rag"
    assert result.interrupts[0]["value"]["tool_call_id"] == "governed-call"
    assert result.messages[-1]["tool_calls"][0]["id"] == "governed-call"


@pytest.mark.asyncio
async def test_da_e4_governance_allows_safe_runtime_and_blocks_ask_before_execution():
    from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
    from src.application.runtime.tool_runtime_service import ToolExecutionContext

    class RuntimeStub:
        def __init__(self):
            self.calls = []

        async def execute(self, tool, call, context):
            self.calls.append((tool.key, call.id, call.arguments, context.trace_id))
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="completed",
                summary="governed",
                trace_id=context.trace_id,
                input=call.arguments,
                output={"ok": True},
            )

    registry = ToolRegistry()
    permission = PermissionService()
    runtime_stub = RuntimeStub()
    service = ToolGovernanceService(
        tool_registry=registry,
        permission_service=permission,
        tool_runtime_service=runtime_stub,
    )
    execution = ToolExecutionContext(
        session_id="session-e4-governance",
        turn_id="turn-e4-governance",
        trace_id="trace-e4-governance",
        user_message="governed tools",
        normalized_input="governed tools",
        context_bundle={"safety_assessment": {"decision": "allow"}},
    )
    policy = PermissionPolicyContext(
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        selected_agent_key="coordinator",
        message_kind=MessageKind.user_input,
        submit_mode="immediate",
        execution_lane="conversation_turn",
        active_mode_key="code_review",
        workflow_mode_key="code_review",
    )
    safe = await service.execute(
        ModelToolCall(id="safe-call", name="knowledge-rag", arguments={"query": "x"}),
        GovernedToolCallContext(
            execution=execution,
            policy=policy,
            active_mode_key="code_review",
            available_tool_keys=frozenset({"knowledge-rag"}),
        ),
    )
    ask = await service.execute(
        ModelToolCall(id="ask-call", name="cli-executor", arguments={"command": "echo no-run"}),
        GovernedToolCallContext(
            execution=execution,
            policy=policy,
            active_mode_key="code_review",
            available_tool_keys=frozenset({"cli-executor"}),
        ),
        prepare_approval=True,
    )
    assert safe.record.status == "completed"
    assert ask.record.status == "waiting_approval"
    assert ask.approval and ask.approval["metadata"]["approval_scope_hash"]
    assert runtime_stub.calls == [
        ("knowledge-rag", "safe-call", {"query": "x"}, "trace-e4-governance")
    ]


def test_da_e4_unknown_requested_skill_is_ignored_like_skill_registry_get_many():
    from src.application.permissions.permission_service import PermissionService

    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        deep_agent_governed_tools_enabled=True,
        permission_service=PermissionService(),
        skill_registry=SkillRegistry(),
    )
    session = _runtime_session("session-e4-unknown-skill")
    request = ExecutionRequest(
        turn_id="turn-e4-unknown-skill",
        session_id=session.id,
        user_message="Review this.",
        normalized_input="Review this.",
        mode_key="code_review",
        skill_keys=["missing-skill"],
    )
    state = runtime._build_initial_state(session, request)

    runtime._prepare_deep_agent_governed_tools(session, request, state)

    assert state["available_tool_keys"] == []
    assert state["model_visible_tool_keys"] == []


def test_da_e4_requested_tool_and_skill_expose_each_tool_only_once():
    from src.application.permissions.permission_service import PermissionService

    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        deep_agent_governed_tools_enabled=True,
        permission_service=PermissionService(),
        skill_registry=SkillRegistry(),
    )
    session = _runtime_session("session-e4-overlapping-tools")
    request = ExecutionRequest(
        turn_id="turn-e4-overlapping-tools",
        session_id=session.id,
        user_message="Review the CI pipeline.",
        normalized_input="Review the CI pipeline.",
        mode_key="code_review",
        skill_keys=["ci-pipeline-review", "tdd-review"],
        context={"requested_tool_keys": ["knowledge-rag", "report-writer"]},
    )
    state = runtime._build_initial_state(session, request)

    runtime._prepare_deep_agent_governed_tools(session, request, state)

    for field in (
        "available_tool_keys", "model_visible_tool_keys", "allowed_tool_keys",
        "approval_required_tool_keys", "denied_tool_keys",
    ):
        assert len(state[field]) == len(set(state[field])), field
    assert state["model_visible_tool_keys"].count("knowledge-rag") == 1
    assert state["model_visible_tool_keys"].count("report-writer") == 1
    assert len(state["permission_decisions"]) == len(state["available_tool_keys"])


def test_da_e4_latest_assistant_tool_call_ids_preserve_declared_order():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-b"},
                {"id": "call-a"},
                {"id": "call-b"},
            ],
        }
    ]

    assert RuntimeService._latest_assistant_tool_call_ids(messages) == [
        "call-b",
        "call-a",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied", "scope_changed", "batch_mixed"])
@pytest.mark.parametrize("new_turns_enabled", [True, False])
async def test_da_e4_approved_tool_resumes_back_into_deep_agent_loop(decision, new_turns_enabled, monkeypatch):
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    from src.application.permissions.permission_service import PermissionService
    from src.registry.agents import AgentRegistry
    from src.registry.modes import ModeRegistry
    from langgraph.checkpoint.memory import InMemorySaver

    class ToolCapableFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolCapableFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "cli-executor",
                        "args": {"command": "echo governed"},
                        "id": "approval-call",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="DA_E4_RESUMED_OK"),
        ]
    )
    if decision == "batch_mixed":
        model.responses[0].tool_calls.append({
            "name": "cli-executor", "args": {"command": "echo denied"},
            "id": "second-call", "type": "tool_call",
        })

    async def resolve_model(_key):
        return model

    from src.application.runtime.tool_job_service import ToolJobService
    from src.runtime.tool_job_store import InMemoryToolJobStore

    tool_jobs = ToolJobService(InMemoryToolJobStore())
    tool_runtime = ToolRuntimeService(tool_job_service=tool_jobs)
    tool_runtime.calls = []

    async def execute_cli(arguments, context):
        tool_runtime.calls.append(
            ("cli-executor", context.call_id, dict(arguments), context.tool_job_id)
        )
        return {"status": "completed", "summary": "approved execution", "stdout": "governed"}

    tool_runtime._handlers["cli-executor"] = execute_cli
    saver = InMemorySaver()
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=tool_runtime,
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=DeepAgentRuntimeAdapter(
            model_resolver=resolve_model, checkpointer_factory=lambda: nullcontext(saver),
        ),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_governed_tools_enabled=True,
        permission_service=PermissionService(),
        agent_registry=AgentRegistry(),
        tool_job_service=tool_jobs,
    )
    observed_requests = []
    execute_observed = runtime._execute_observed

    async def capture_execution_request(observed_session, observed_request, state, executor):
        observed_requests.append(observed_request)
        return await execute_observed(observed_session, observed_request, state, executor)

    monkeypatch.setattr(runtime, "_execute_observed", capture_execution_request)
    session = _runtime_session("session-e4-resume")
    request = ExecutionRequest(
        turn_id="turn-e4-resume",
        trace_id="trace-e4-resume",
        session_id=session.id,
        user_message="Run the governed command.",
        normalized_input="Run the governed command.",
        mode_key="code_review",
        agent_key="coordinator",
        model_key="fake-model",
        context={
            "selected_mode": ModeRegistry().get("code_review").model_dump(mode="python"),
            "requested_tool_keys": ["cli-executor"],
            "safety_assessment": {"decision": "allow"},
        },
    )

    pending = await runtime.execute_turn(session, request)
    assert pending.snapshot.stage == "waiting_approval"
    assert tool_runtime.calls == []
    approval_job_id = pending.approvals[0]["metadata"]["tool_job_id"]

    # Test-only route change after admission; not a configuration hot-reload API.
    runtime._deep_agent_new_turns_enabled = new_turns_enabled
    approval = dict(pending.approvals[0])
    approval["status"] = "denied" if decision == "denied" else "approved"
    if decision == "scope_changed":
        approval["metadata"]["approval_scope_hash"] = "changed-scope"
    session.metadata["pending_turn"] = pending.pending_turn
    if decision == "batch_mixed":
        second = dict(pending.approvals[1])
        second["status"] = "denied"
        intermediate = await runtime.resume_after_approval(session, second)
        assert intermediate.snapshot.stage == "waiting_approval"
        assert tool_runtime.calls == []
        session.metadata["pending_turn"] = intermediate.pending_turn
    resumed = await runtime.resume_after_approval(session, approval)

    assert resumed is not None
    assert resumed.output_text == "DA_E4_RESUMED_OK"
    assert resumed.snapshot.stage == "completed"
    assert observed_requests[-1].trace_id == pending.state["trace_id"] == "trace-e4-resume"
    if decision in {"approved", "batch_mixed"}:
        assert len(tool_runtime.calls) == 1
        assert tool_runtime.calls[0][0:2] == ("cli-executor", "approval-call")
        assert tool_runtime.calls[0][3] == approval_job_id
        assert resumed.state["tool_results"][-1]["status"] == "completed"
        assert [
            event.type for event in resumed.events
            if event.type in {"tool.execution_requested", "tool.execution_completed"}
        ] == ["tool.execution_requested", "tool.execution_completed"]
        if decision == "batch_mixed":
            assert {item["call_id"]: item["status"] for item in resumed.state["tool_results"]} == {
                "approval-call": "completed", "second-call": "denied",
            }
    else:
        assert tool_runtime.calls == []
        assert resumed.state["tool_results"][-1]["status"] == "denied"
        assert (await tool_jobs.get_job(approval_job_id)).status.value == "denied"


def test_deep_agent_checkpoint_configuration_is_opt_in_and_schema_is_safe():
    assert DeepAgentsConfig().checkpoint_enabled is False
    assert DeepAgentsConfig(checkpoint_pool_size=2).checkpoint_pool_size == 2
    with pytest.raises(ValueError):
        DeepAgentsConfig(checkpoint_schema="public,other")


def test_da_e5_approval_continuation_heartbeat_must_precede_lease_expiry():
    from src.core.config import OrchestrConfig

    assert OrchestrConfig().approval_continuation_heartbeat_seconds == 30
    with pytest.raises(ValueError, match="heartbeat_seconds must be shorter"):
        OrchestrConfig(
            approval_continuation_lease_seconds=30,
            approval_continuation_heartbeat_seconds=30,
        )
    assert OrchestrConfig().turn_execution_heartbeat_seconds == 30
    with pytest.raises(ValueError, match="turn_execution_heartbeat_seconds"):
        OrchestrConfig(
            turn_execution_lease_seconds=30,
            turn_execution_heartbeat_seconds=30,
        )


@pytest.mark.asyncio
async def test_da_e4_duplicate_resolved_approval_does_not_reopen_completed_session():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore
    from src.schemas.session import ApprovalDecisionRequest, ToolApprovalRequest, ToolApprovalStatus

    store = InMemorySessionStore()
    session = _runtime_session("completed-hitl-session")
    session.status = SessionStatus.completed
    await store.save_session(session)
    approval = ToolApprovalRequest(
        id="resolved-hitl-approval", session_id=session.id,
        tool_key="cli-executor", tool_name="CLI Executor", reason="acceptance",
        status=ToolApprovalStatus.approved, created_at=datetime.now(UTC),
        metadata={"deep_agent_interrupt_id": "interrupt-1"},
    )
    await store.save_approval(session.id, approval)
    service = SessionService(
        store=store, input_orchestrator_service=InputOrchestratorService(mode_registry=ModeRegistry()),
        runtime_service=object(), mode_registry=ModeRegistry(),
    )
    await service.resolve_approval(
        session.id, approval.id, ApprovalDecisionRequest(decision=ToolApprovalStatus.approved),
    )
    assert (await store.get_session(session.id)).status == SessionStatus.completed
    assert service._approval_resume_tasks == {}


@pytest.mark.asyncio
async def test_da_e5_two_service_instances_schedule_one_approval_continuation():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore
    from src.schemas.session import (
        ApprovalDecisionRequest,
        ToolApprovalRequest,
        ToolApprovalStatus,
    )

    class RuntimeStub:
        def __init__(self):
            self.calls = 0

        async def resume_after_approval(self, *_args, **_kwargs):
            self.calls += 1
            return None

    store = InMemorySessionStore()
    session = _runtime_session("concurrent-hitl-session")
    session.status = SessionStatus.waiting_approval
    session.metadata["pending_turn"] = {
        "turn_id": "concurrent-hitl-turn",
        "pending_approval_ids": ["concurrent-hitl-approval"],
    }
    await store.save_session(session)
    approval = ToolApprovalRequest(
        id="concurrent-hitl-approval",
        session_id=session.id,
        tool_key="cli-executor",
        tool_name="CLI Executor",
        reason="concurrent continuation acceptance",
        status=ToolApprovalStatus.pending,
        created_at=datetime.now(UTC),
    )
    await store.save_approval(session.id, approval)
    runtime = RuntimeStub()

    def service():
        return SessionService(
            store=store,
            input_orchestrator_service=InputOrchestratorService(mode_registry=ModeRegistry()),
            runtime_service=runtime,
            mode_registry=ModeRegistry(),
        )

    first, second = service(), service()
    payload = ApprovalDecisionRequest(decision=ToolApprovalStatus.approved)
    await asyncio.gather(
        first.resolve_approval(session.id, approval.id, payload),
        second.resolve_approval(session.id, approval.id, payload),
    )
    for _ in range(20):
        if not first._approval_resume_tasks and not second._approval_resume_tasks:
            break
        await asyncio.sleep(0)

    assert runtime.calls == 1
    events = await store.list_events(session.id)
    assert [item.type for item in events].count("approval.resolved") == 1
    assert [item.type for item in events].count("approval.continuation_already_claimed") == 1


@pytest.mark.asyncio
async def test_da_e5_lost_approval_continuation_lease_requests_runtime_interrupt():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore

    class RuntimeStub:
        def __init__(self):
            self.interrupts = []

        def request_interrupt(self, session_id, reason):
            self.interrupts.append((session_id, reason))

    class Store(InMemorySessionStore):
        async def renew_approval_continuation(self, *_args, **_kwargs):
            return False

    runtime = RuntimeStub()
    service = SessionService(
        store=Store(),
        input_orchestrator_service=InputOrchestratorService(mode_registry=ModeRegistry()),
        runtime_service=runtime,
        mode_registry=ModeRegistry(),
        approval_continuation_heartbeat_seconds=0.1,
    )
    stop = asyncio.Event()
    await service._keep_approval_continuation_alive(
        session_id="lease-lost-session",
        approval_id="lease-lost-approval",
        continuation_token="lease-lost-token",
        stop_event=stop,
    )

    assert runtime.interrupts == [
        ("lease-lost-session", "Approval continuation lease was lost to another worker.")
    ]


@pytest.mark.asyncio
async def test_da_e5_cancelled_continuation_keeps_lease_recoverable_not_completed():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore
    from src.schemas.session import ToolApprovalRequest, ToolApprovalStatus

    entered = asyncio.Event()

    class RuntimeStub:
        async def resume_after_approval(self, *_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

    store = InMemorySessionStore()
    session = _runtime_session("cancelled-continuation-session")
    session.status = SessionStatus.running
    session.metadata["pending_turn"] = {"turn_id": "cancelled-continuation-turn"}
    await store.save_session(session)
    approval = ToolApprovalRequest(
        id="cancelled-continuation-approval",
        session_id=session.id,
        tool_key="cli-executor",
        tool_name="CLI Executor",
        reason="shutdown recovery",
        status=ToolApprovalStatus.approved,
        created_at=datetime.now(UTC),
    )
    await store.save_approval(session.id, approval)
    assert await store.claim_approval_continuation(
        session.id, approval.id, "worker", "lease-token", 30
    )
    service = SessionService(
        store=store,
        input_orchestrator_service=InputOrchestratorService(mode_registry=ModeRegistry()),
        runtime_service=RuntimeStub(),
        mode_registry=ModeRegistry(),
    )
    task = asyncio.create_task(
        service._resume_after_approval_async(
            session_id=session.id,
            approval=approval,
            continuation_token="lease-token",
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = (await store.list_approvals(session.id))[0]
    assert stored.metadata["continuation_lease_token"] == "lease-token"
    assert "continuation_completed_at" not in stored.metadata


@pytest.mark.asyncio
async def test_da_e5_expired_continuation_cannot_overwrite_new_owner_session_state():
    from src.runtime.store import InMemorySessionStore
    from src.schemas.session import ToolApprovalRequest, ToolApprovalStatus

    store = InMemorySessionStore()
    session_id = "fenced-continuation-session"
    approval_id = "fenced-continuation-approval"
    initial = _runtime_session(session_id)
    await store.save_session(initial)
    await store.save_approval(
        session_id,
        ToolApprovalRequest(
            id=approval_id,
            session_id=session_id,
            tool_key="knowledge-rag",
            tool_name="Knowledge RAG",
            reason="continuation fencing validation",
            status=ToolApprovalStatus.approved,
            created_at=datetime.now(UTC),
        ),
    )
    assert await store.claim_approval_continuation(
        session_id, approval_id, "old-worker", "old-token", 0
    )
    assert await store.claim_approval_continuation(
        session_id, approval_id, "new-worker", "new-token", 30
    )

    new_owner_state = deepcopy(initial)
    new_owner_state.metadata["continuation_result"] = "new-owner"
    await store.save_session(
        new_owner_state,
        continuation_approval_id=approval_id,
        continuation_lease_token="new-token",
    )
    stale_state = deepcopy(initial)
    stale_state.metadata["continuation_result"] = "stale-owner"
    with pytest.raises(RuntimeError, match="lease"):
        await store.save_session(
            stale_state,
            continuation_approval_id=approval_id,
            continuation_lease_token="old-token",
        )

    stored = await store.get_session(session_id)
    assert stored is not None
    assert stored.metadata["continuation_result"] == "new-owner"


@pytest.mark.asyncio
async def test_da_e5_expired_turn_owner_cannot_complete_lease():
    from src.runtime.store import InMemorySessionStore

    store = InMemorySessionStore()
    session = _runtime_session("expired-turn-owner-session")
    await store.save_session(session)
    assert await store.claim_turn_execution(
        session.id,
        "expired-turn",
        "old-worker",
        "old-token",
        0,
    )

    assert not await store.complete_turn_execution(session.id, "old-token")
    stored = await store.get_session(session.id)
    assert stored is not None
    assert stored.metadata["turn_lease_token"] == "old-token"


@pytest.mark.asyncio
async def test_da_e5_startup_recovery_marks_expired_turn_interrupted():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore

    store = InMemorySessionStore()
    session = _runtime_session("startup-recovery-session")
    await store.save_session(session)
    assert await store.claim_turn_execution(
        session.id,
        "startup-turn",
        "crashed-worker",
        "expired-token",
        0,
    )
    registry = ModeRegistry()
    service = SessionService(
        store=store,
        input_orchestrator_service=InputOrchestratorService(mode_registry=registry),
        runtime_service=SimpleNamespace(request_interrupt=lambda *_args, **_kwargs: None),
        mode_registry=registry,
    )

    assert await service.recover_expired_turn_executions() == [session.id]
    recovered = await store.get_session(session.id)
    assert recovered is not None
    assert recovered.status == SessionStatus.interrupted
    assert "turn_lease_token" not in recovered.metadata
    assert [event.type for event in await store.list_events(session.id)] == [
        "turn.recovery_detected"
    ]


@pytest.mark.asyncio
async def test_da_e5_two_workers_cannot_execute_the_same_new_turn():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.application.runtime.runtime_service import RuntimeTurnResult
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore
    from src.schemas.session import SendMessageRequest, SessionSnapshot

    class DetachedStore(InMemorySessionStore):
        def __init__(self):
            super().__init__()
            self.initial_reads = 0
            self.both_reading = asyncio.Event()

        async def get_session(self, session_id):
            value = await super().get_session(session_id)
            if self.initial_reads < 2:
                self.initial_reads += 1
                if self.initial_reads == 2:
                    self.both_reading.set()
                await self.both_reading.wait()
            return deepcopy(value) if value is not None else None

    class RuntimeStub:
        def __init__(self):
            self.calls = 0
            self.release = asyncio.Event()

        async def execute_turn(self, session, request, **_kwargs):
            self.calls += 1
            await self.release.wait()
            state = {
                "turn_id": request.turn_id,
                "trace_id": f"trace-{request.turn_id}",
                "selected_agent_key": "default-agent",
                "selected_agent_name": "Default Agent",
                "selected_model_key": "fake-model",
                "selected_model_name": "Fake Model",
                "model_response_summary": {"mode": "ok"},
            }
            return RuntimeTurnResult(
                output_text="done",
                events=[],
                snapshot=SessionSnapshot(
                    id=f"snapshot-{request.turn_id}",
                    session_id=session.id,
                    version=1,
                    stage="completed",
                    created_at=datetime.now(UTC),
                    graph_state=state,
                ),
                approvals=[],
                tool_messages=[],
                pending_turn={},
                state=state,
            )

        def request_interrupt(self, *_args, **_kwargs):
            return None

    store = DetachedStore()
    session = _runtime_session("concurrent-new-turn-session")
    await store.save_session(session)
    runtime = RuntimeStub()

    def service():
        registry = ModeRegistry()
        return SessionService(
            store=store,
            input_orchestrator_service=InputOrchestratorService(mode_registry=registry),
            runtime_service=runtime,
            mode_registry=registry,
        )

    first_task = asyncio.create_task(
        service().send_message(session.id, SendMessageRequest(content="first"))
    )
    second_task = asyncio.create_task(
        service().send_message(session.id, SendMessageRequest(content="second"))
    )
    for _ in range(100):
        if runtime.calls >= 2:
            break
        await asyncio.sleep(0)
    runtime.release.set()
    outcomes = await asyncio.gather(first_task, second_task, return_exceptions=True)

    assert runtime.calls == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1


@pytest.mark.asyncio
async def test_da_e5_two_workers_cannot_resume_the_same_turn():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.application.runtime.runtime_service import RuntimeTurnResult
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore
    from src.schemas.session import SessionSnapshot

    class DetachedStore(InMemorySessionStore):
        def __init__(self):
            super().__init__()
            self.initial_reads = 0
            self.both_reading = asyncio.Event()

        async def get_session(self, session_id):
            value = await super().get_session(session_id)
            if self.initial_reads < 2:
                self.initial_reads += 1
                if self.initial_reads == 2:
                    self.both_reading.set()
                await self.both_reading.wait()
            return deepcopy(value) if value is not None else None

    class RuntimeStub:
        def __init__(self):
            self.calls = 0
            self.release = asyncio.Event()

        async def resume_turn(self, session, snapshot, **_kwargs):
            self.calls += 1
            await self.release.wait()
            state = {
                "turn_id": snapshot.graph_state["turn_id"],
                "trace_id": "resume-trace",
                "user_message": "resume",
                "selected_agent_key": "default-agent",
                "selected_agent_name": "Default Agent",
                "selected_model_key": "fake-model",
                "selected_model_name": "Fake Model",
                "model_response_summary": {"mode": "ok"},
            }
            return RuntimeTurnResult(
                output_text="resumed",
                events=[],
                snapshot=SessionSnapshot(
                    id="resume-snapshot",
                    session_id=session.id,
                    version=2,
                    stage="completed",
                    created_at=datetime.now(UTC),
                    graph_state=state,
                ),
                approvals=[],
                tool_messages=[],
                pending_turn={},
                state=state,
            )

        def request_interrupt(self, *_args, **_kwargs):
            return None

    store = DetachedStore()
    session = _runtime_session("concurrent-resume-session")
    session.status = SessionStatus.interrupted
    session.metadata["pending_turn"] = {"turn_id": "resume-turn"}
    await store.save_session(session)
    await store.save_snapshot(
        session.id,
        SessionSnapshot(
            id="resume-snapshot-v1",
            session_id=session.id,
            version=1,
            stage="interrupted",
            created_at=datetime.now(UTC),
            graph_state={"turn_id": "resume-turn"},
        ),
    )
    runtime = RuntimeStub()

    def service():
        registry = ModeRegistry()
        return SessionService(
            store=store,
            input_orchestrator_service=InputOrchestratorService(mode_registry=registry),
            runtime_service=runtime,
            mode_registry=registry,
        )

    first_task = asyncio.create_task(
        service().resume_session(session.id, ResumeSessionRequest(reason="manual"))
    )
    second_task = asyncio.create_task(
        service().resume_session(session.id, ResumeSessionRequest(reason="manual"))
    )
    for _ in range(100):
        if runtime.calls >= 1:
            break
        await asyncio.sleep(0)
    runtime.release.set()
    outcomes = await asyncio.gather(first_task, second_task, return_exceptions=True)

    assert runtime.calls == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1


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
        assert parent_limiters[0].thread_limit == 1
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
        assert model_limiters[0].thread_limit == 4
        assert tool_limiters[0].run_limit == 7
        assert tool_limiters[0].thread_limit == 7
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
async def test_deep_agent_runtime_emits_frozen_graph_input_asset_before_adapter_call():
    captured = []

    async def capture(asset):
        captured.append(asset)

    class AdapterStub:
        async def execute(self, request):
            assert captured and captured[0]["model_key"] == "test-model"
            assert captured[0]["messages"] == []
            assert captured[0]["context"] == request.context
            assert captured[0]["context"]["skill_keys"] == ["fixture-skill"]
            assert captured[0]["framework_messages"] == request.framework_messages
            assert captured[0]["system_prompt"] == request.system_prompt
            assert captured[0]["checkpoint_namespace"] == request.checkpoint_namespace
            # Actual execution may mutate its nested input; the frozen JSON
            # asset must retain the original input, not a shallow reference.
            request.context["nested"]["value"] = "changed during adapter execution"
            return DeepAgentRuntimeResult(output_text="GRAPH_INPUT_ASSET_OK")

    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=AdapterStub(),
        deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"],
    )
    session = _runtime_session("session-graph-input-asset")
    request = _runtime_request(session.id, "turn-graph-input-asset")
    request.context["nested"] = {"value": "original"}
    request.skill_keys = ["fixture-skill"]
    result = await runtime.execute_turn(session, request, on_graph_input=capture)
    assert result.output_text == "GRAPH_INPUT_ASSET_OK"
    assert captured[0]["asset_sha256"]
    assert captured[0]["context"]["nested"] == {"value": "original"}
    assert captured[0]["feature_flags"] == {
        "read_only_filesystem": False,
        "cognitive_planning": False,
        "cognitive_subagents": False,
        "governed_tools": False,
    }


@pytest.mark.asyncio
async def test_graph_input_persistence_failure_prevents_adapter_execution():
    class AdapterStub:
        async def execute(self, request):
            raise AssertionError("Graph must not run before input persistence succeeds")

    async def fail(asset):
        raise ConnectionError("injected graph input write failure")

    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=AdapterStub(), deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"])
    session = _runtime_session("session-input-write-failure")
    with pytest.raises(ConnectionError, match="graph input write failure"):
        await runtime.execute_turn(session, _runtime_request(session.id, "turn-input-write-failure"), on_graph_input=fail)


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
            await request.on_subagent_event(started)
            await request.on_subagent_event(completed)
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
async def test_failed_subagent_event_write_closes_stream_and_preserves_lost_owner():
    from src.runtime.store import ContinuationLeaseLostError

    closed = asyncio.Event()
    lost = ContinuationLeaseLostError("injected subagent event lost owner")

    class Agent:
        async def astream_events(self, *args, **kwargs):
            try:
                from langchain_core.runnables.config import get_async_callback_manager_for_config

                callbacks = get_async_callback_manager_for_config(kwargs["config"])
                await callbacks.on_tool_start({"name": "task"}, "inspect",
                    inputs={"subagent_type": "code-review-researcher"})
                yield {"event": "unexpected_after_failed_callback"}
                raise AssertionError("Failed durable callback must stop consuming the graph")
            finally:
                closed.set()

    async def fail(item):
        await asyncio.sleep(0)
        raise lost

    adapter = DeepAgentRuntimeAdapter(model_resolver=lambda key: _resolved_model(),
        agent_factory=lambda **kwargs: Agent())
    with pytest.raises(ContinuationLeaseLostError) as caught:
        await adapter.execute(DeepAgentRuntimeRequest(session_id="session", turn_id="turn", trace_id="trace",
            model_key="model", system_prompt="system", messages=[], cognitive_subagents_enabled=True,
            on_subagent_event=fail))
    assert caught.value is lost
    assert closed.is_set(), "SDK event stream must close before releasing execution ownership"


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


def test_new_deep_agent_turn_rehydrates_lossless_framework_transcript_asset():
    from src.application.deep_agents.framework_transcript import append_turn_input

    previous = messages_to_dict([
        HumanMessage(content="inspect", id="old-user"),
        AIMessage(
            content=[{"type": "text", "text": "call lookup", "id": "part-1"}],
            tool_calls=[{"name": "lookup", "args": {"q": "x"}, "id": "call-1", "type": "tool_call"}],
            additional_kwargs={"provider_signature": "sig-1"},
            response_metadata={"token_usage": {"input_tokens": 2}},
            id="old-assistant",
        ),
        ToolMessage(content={"answer": "ok"}, tool_call_id="call-1", name="lookup", id="old-tool"),
    ])
    asset = framework_transcript_asset(
        session_id="session-transcript",
        source_turn_id="turn-old",
        runtime_version="deepagents-test",
        framework_messages=previous,
    )
    session = _runtime_session("session-transcript")
    session.metadata["framework_transcript_asset"] = asset.model_dump(mode="json")
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
    )

    state = runtime._build_initial_state(
        session,
        _runtime_request(session.id, "turn-new"),
        include_framework_transcript=True,
    )

    assert state["framework_messages"][1]["data"]["tool_calls"][0]["id"] == "call-1"
    assert state["framework_messages"][1]["data"]["additional_kwargs"]["provider_signature"] == "sig-1"
    assert state["framework_messages"][-1]["type"] == "human"
    assert state["framework_messages"][-1]["data"]["content"] == "Review the evidence."


@pytest.mark.asyncio
async def test_runtime_result_callback_runs_before_execute_turn_returns(monkeypatch):
    session = _runtime_session("session-boundary")
    request = _runtime_request(session.id, "turn-boundary")
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(),
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
    )
    result = RuntimeTurnResult(
        output_text="result",
        events=[],
        snapshot=SessionSnapshot(
            id="snapshot-boundary", session_id=session.id, version=1, stage="completed",
            created_at=datetime.now(UTC), graph_state={"turn_id": request.turn_id},
        ),
        approvals=[], state={"turn_id": request.turn_id}, tool_messages=[], pending_turn={},
    )
    observed = []

    async def fake_execute(*args, **kwargs):
        return result

    async def on_result(value):
        observed.append(value)

    monkeypatch.setattr(runtime, "_execute_turn", fake_execute)
    assert await runtime.execute_turn(session, request, on_runtime_result=on_result) is result
    assert observed == [result]


def test_framework_transcript_asset_rejects_tampered_native_payload():
    from pydantic import ValidationError

    asset = framework_transcript_asset(
        session_id="session-transcript",
        source_turn_id="turn-old",
        runtime_version="deepagents-test",
        framework_messages=messages_to_dict([AIMessage(content="native", id="assistant-1")]),
    )
    payload = asset.model_dump(mode="json")
    payload["framework_messages"][0]["data"]["content"] = "changed"

    with pytest.raises(ValidationError, match="digest"):
        type(asset).model_validate(payload)


def test_session_finalization_projects_deep_agent_native_transcript_asset():
    state = {
        "turn_id": "turn-native",
        "control_state": "completed",
        "termination_reason": "completed",
        "context_bundle": {"runtime_binding": {"runtime_version": "deepagents-test"}},
        "model_response_summary": {"harness": "deepagents"},
        "framework_messages": messages_to_dict([AIMessage(content="native", id="assistant-1")]),
    }
    asset = SessionService._build_framework_transcript_asset("session-transcript", state)

    assert asset is not None
    assert asset["source_turn_id"] == "turn-native"
    assert asset["framework_messages"][0]["data"]["content"] == "native"


@pytest.mark.parametrize("field,value", [
    ("control_state", "active_turn"),
    ("termination_reason", "waiting_approval"),
    ("termination_reason", "interrupted"),
    ("termination_reason", "failed"),
    ("pending_approvals", [{"id": "pending-approval"}]),
    ("pending_turn", {"turn_id": "turn-native"}),
    ("model_response_summary", {"harness": "deepagents", "mode": "incomplete_response"}),
])
def test_noncompleted_native_state_is_not_published_as_next_turn_history(field, value, caplog):
    state = {
        "turn_id": "turn-native", "control_state": "completed", "termination_reason": "completed",
        "model_response_summary": {"harness": "deepagents"},
        "framework_messages": messages_to_dict([AIMessage(content="private-native-body", id="assistant-1")]),
        field: value,
    }
    original = deepcopy(state)
    caplog.set_level("INFO", logger="src.application.sessions.session_service")

    assert SessionService._build_framework_transcript_asset("session-transcript", state) is None
    assert state == original
    assert any(record.message == "framework_transcript_not_published" for record in caplog.records)
    assert "private-native-body" not in caplog.text


@pytest.mark.parametrize("operation", ["publish", "import"])
@pytest.mark.parametrize("changed", ["pending", "duplicate_result", "orphan_result", "invalid_call", "missing_call_id"])
def test_cross_turn_transcript_requires_the_existing_closed_native_protocol(operation, changed, caplog):
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    call = AIMessage(id="source", content="", tool_calls=[
        {"id": "A", "name": "inspect", "args": {}, "type": "tool_call"}],
        additional_kwargs={"provider_signature": "fixture-private-signature"})
    result = ToolMessage(content="private-native-body", tool_call_id="A")
    messages = [HumanMessage(content="inspect"), call, result, AIMessage(id="answer", content="done")]
    if changed == "pending":
        messages.remove(result)
    elif changed == "duplicate_result":
        messages.insert(3, result.model_copy())
    elif changed == "orphan_result":
        messages = [HumanMessage(content="inspect"), result, AIMessage(content="done")]
    elif changed == "invalid_call":
        messages[-1] = AIMessage(content="", invalid_tool_calls=[
            {"name": "inspect", "args": "broken", "id": "B", "error": "bad args"}])
    else:
        call.tool_calls[0]["id"] = None
    wire = messages_to_dict(messages)
    original = deepcopy(wire)

    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        if operation == "publish":
            SessionService._build_framework_transcript_asset("session-transcript", {
                "turn_id": "turn-native", "control_state": "completed", "termination_reason": "completed",
                "model_response_summary": {"harness": "deepagents"}, "framework_messages": wire,
            })
        else:
            session = _runtime_session("session-transcript")
            session.metadata["framework_transcript_asset"] = framework_transcript_asset(
                session_id=session.id, source_turn_id="turn-native", runtime_version="deepagents-test",
                framework_messages=wire,
            ).model_dump(mode="json")
            RuntimeService._framework_messages_for_new_turn(session, _runtime_request(session.id, "turn-next"))

    assert wire == original
    rejected = [record for record in caplog.records if record.message == "framework_transcript_protocol_rejected"]
    assert len(rejected) == 1 and rejected[0].operation == operation
    assert "private-native-body" not in caplog.text
    assert "fixture-private-signature" not in caplog.text


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
    assert control.is_interrupt_requested("session-interrupt") is False


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
async def test_message_route_accepts_durable_input_without_disconnect_cancellation():
    from types import SimpleNamespace

    from src.api.routes.sessions import send_message

    class DispatcherStub:
        def __init__(self):
            self.interrupt_payload = None

        async def submit(self, session_id, payload):
            return {"status": "queued"}

        async def interrupt_session(self, session_id, payload):
            self.interrupt_payload = payload

    class RequestStub:
        def __init__(self, service):
            self.app = SimpleNamespace(state=SimpleNamespace(agent_task_dispatcher=service))

        async def is_disconnected(self):
            return True

    service = DispatcherStub()
    response = await asyncio.wait_for(
        send_message(
            "session-disconnect",
            SimpleNamespace(),
            RequestStub(service),
        ),
        timeout=1,
    )

    assert response == {"status": "queued"}
    assert service.interrupt_payload is None


@pytest.mark.asyncio
async def test_message_route_returns_task_receipt_without_waiting_for_execution():
    from types import SimpleNamespace

    from src.api.routes.sessions import send_message

    class DispatcherStub:
        async def submit(self, session_id, payload):
            return {"status": "queued"}

    class RequestStub:
        def __init__(self, service):
            self.app = SimpleNamespace(state=SimpleNamespace(agent_task_dispatcher=service))

        async def is_disconnected(self):
            return False

    response = await asyncio.wait_for(
        send_message(
            "session-completed",
            SimpleNamespace(),
            RequestStub(DispatcherStub()),
        ),
        timeout=1,
    )

    assert response == {"status": "queued"}


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

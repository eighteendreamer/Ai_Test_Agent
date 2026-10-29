"""Identity-based cross-Turn context, not display-text de-duplication."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

from src.application.runtime.runtime_service import RuntimeService
from src.domain.models import SessionRecord
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ChatMessage, ExecutionRequest, RuntimeMode, SessionMode, SessionStatus
from tests.test_runtime_binding_persistence import make_binding


def message(identity, role, content, turn_id, **metadata):
    return ChatMessage(id=identity, role=role, content=content, created_at=datetime.now(UTC),
        metadata={"turn_id": turn_id, **metadata})


def context_case():
    now = datetime.now(UTC)
    session = SessionRecord(id="covered-session", title="Covered context", status=SessionStatus.running,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key="code_review",
        created_at=now, updated_at=now)
    binding = make_binding(session.id, "current-turn", runtime_version="v2.1.0")
    request = ExecutionRequest(session_id=session.id, turn_id=binding.turn_id, user_message="repeat",
        normalized_input="repeat", model_key="fake", mode_key="code_review", runtime_binding=binding,
        user_message_id="current-user")
    session.messages = [
        message("old-user", "user", "Earlier review", "old-turn"),
        message("old-answer", "assistant", "Earlier answer", "old-turn"),
        message("legacy-user", "user", "repeat", "legacy-turn"),
        message("current-user", "user", "repeat", "current-turn"),
        message("future-user", "user", "not yet admitted to this Turn", "future-turn", delivery_status="delivered"),
        # A predecessor's answer can be saved after this queued input was accepted.
        message("legacy-answer", "assistant", "Legacy answer", "legacy-turn"),
        message("hidden-error", "assistant", "not context", "legacy-turn", context_eligible=False),
    ]
    runtime = RuntimeService(graph=None,
        model_runtime_service=SimpleNamespace(get_model_config=lambda key: None, get_default_model_config=lambda: None),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry())
    return session, request, runtime


def covered_asset(session, binding):
    native = messages_to_dict([
        HumanMessage(id="native-old-user", content="Earlier review"),
        AIMessage(id="native-old-call", content="", tool_calls=[
            {"id": "previous-call", "name": "inspect", "args": {}, "type": "tool_call"}],
            additional_kwargs={"provider_signature": "fixture-native-signature"}),
        ToolMessage(id="native-old-tool", tool_call_id="previous-call", content="original evidence"),
        AIMessage(id="native-old-answer", content=[{"type": "text", "text": "Earlier answer", "id": "part"}]),
    ])
    payload = {
        "schema_version": 2, "runtime_kind": "deepagents", "session_id": session.id,
        "source_turn_id": "old-turn", "runtime_version": "v2.1.0", "framework_messages": native,
        "source_input_message_id": "old-user", "source_output_message_ids": ["old-answer"],
        "covered_message_ids": ["old-user", "old-answer"],
        "business_message_map": {"old-user": ["native-old-user"], "old-answer": ["native-old-answer"]},
        "model_config_fingerprint": binding.model_config_fingerprint, "serializer": "langchain.messages.v1",
    }
    return {**payload, "content_sha256": approval_value_hash(payload)}


def test_new_context_uses_admitted_identity_and_keeps_a_late_predecessor_answer():
    session, request, runtime = context_case()
    before = deepcopy(session)

    state = runtime._build_initial_state(session, request, include_framework_transcript=True)

    assert [item["data"]["id"] for item in state["framework_messages"]] == [
        "old-user", "old-answer", "legacy-user", "legacy-answer", "current-user"]
    assert [item["data"]["content"] for item in state["framework_messages"]].count("repeat") == 2
    assert session == before


def test_native_prefix_and_legacy_middle_are_assembled_once_without_losing_provider_fields():
    session, request, runtime = context_case()
    asset = covered_asset(session, request.runtime_binding)
    session.metadata["framework_transcript_asset"] = asset
    before = deepcopy(asset)

    state = runtime._build_initial_state(session, request, include_framework_transcript=True)

    assert state["framework_messages"][:4] == before["framework_messages"]
    assert [item["data"]["id"] for item in state["framework_messages"][4:]] == [
        "legacy-user", "legacy-answer", "current-user"]
    assert state["runtime_messages"][-1] == {"role": "user", "content": "repeat"}
    preparation = state["context_bundle"]["framework_transcript_preparation"]
    assert preparation["source_input_message_id"] == "current-user"
    assert preparation["covered_message_ids"] == [
        "old-user", "old-answer", "legacy-user", "legacy-answer", "current-user"]
    assert asset == before


def completed_state(session, request, runtime):
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    state.update(control_state="completed", termination_reason="completed",
        model_response_summary={"harness": "deepagents"})
    state["framework_messages"].extend(messages_to_dict([AIMessage(id="current-native-answer", content="done")]))
    return state


def test_execution_request_preserves_legacy_serialization_and_freezes_admitted_identity():
    _, request, _ = context_case()
    assert request.model_dump(mode="json")["user_message_id"] == "current-user"
    legacy = request.model_copy(update={"user_message_id": None})
    assert "user_message_id" not in legacy.model_dump(mode="json")
    assert "user_message_id" not in legacy.model_dump_json()
    assert ExecutionRequest.model_validate(request.model_dump(mode="json")) == request


@pytest.mark.parametrize("change", ["missing_input", "wrong_turn", "duplicate_id", "ineligible_input"])
def test_ambiguous_admitted_identity_is_rejected_before_assembly(change):
    session, request, runtime = context_case()
    if change == "missing_input":
        request.user_message_id = None
    elif change == "wrong_turn":
        session.messages[3].metadata["turn_id"] = "another-turn"
    elif change == "duplicate_id":
        session.messages.append(session.messages[3].model_copy(deep=True))
    else:
        session.messages[3].metadata["context_eligible"] = False
    with pytest.raises(ValueError):
        runtime._build_initial_state(session, request, include_framework_transcript=True)


@pytest.mark.parametrize("change", ["model", "session", "coverage", "map", "source_input", "schema1"])
def test_unusable_transcript_never_falls_back_to_display_text(change):
    session, request, runtime = context_case()
    asset = covered_asset(session, request.runtime_binding)
    if change == "model":
        asset["model_config_fingerprint"] = "another-model"
    elif change == "session":
        asset["session_id"] = "another-session"
    elif change == "coverage":
        asset["covered_message_ids"] = ["old-answer", "old-user"]
    elif change == "map":
        asset["business_message_map"]["old-answer"] = ["missing-native-message"]
    elif change == "source_input":
        asset["source_input_message_id"] = "legacy-user"
    else:
        from src.schemas.framework_transcript import framework_transcript_asset
        asset = framework_transcript_asset(session_id=session.id, source_turn_id="old-turn",
            runtime_version="v2.0.0", framework_messages=asset["framework_messages"]).model_dump(mode="json")
    asset["content_sha256"] = approval_value_hash({key: value for key, value in asset.items() if key != "content_sha256"})
    session.metadata["framework_transcript_asset"] = asset
    with pytest.raises(ValueError):
        runtime._build_initial_state(session, request, include_framework_transcript=True)


def test_client_cannot_select_or_replace_server_context_preparation():
    session, request, runtime = context_case()
    request.context["framework_transcript_preparation"] = {"covered_message_ids": ["future-user"]}
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    assert state["context_bundle"]["framework_transcript_preparation"]["source_input_message_id"] == "current-user"
    request.runtime_binding = request.runtime_binding.model_copy(update={"runtime_version": "v2.0.0"})
    legacy = runtime._build_initial_state(session, request, include_framework_transcript=True)
    assert "framework_transcript_preparation" not in legacy["context_bundle"]
    assert legacy["framework_messages"] == []


def test_an_unbound_client_context_cannot_select_the_new_runtime_or_asset_format():
    from src.application.sessions.session_service import SessionService

    session, request, runtime = context_case()
    request.context["runtime_binding"] = request.runtime_binding.model_dump(mode="json")
    request.runtime_binding = None
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    assert "runtime_binding" not in state["context_bundle"]
    assert "framework_transcript_preparation" not in state["context_bundle"]
    state.update(control_state="completed", termination_reason="completed",
        model_response_summary={"harness": "deepagents"},
        framework_messages=messages_to_dict([AIMessage(id="unbound-answer", content="legacy pilot")]))
    asset = SessionService._build_framework_transcript_asset(session.id, state)
    assert asset["schema_version"] == 1 and asset["runtime_version"] == "deepagents-unbound"


def test_publication_retains_coverage_and_maps_the_original_business_output():
    from src.application.sessions.session_service import SessionService

    session, request, runtime = context_case()
    state = completed_state(session, request, runtime)
    asset = SessionService._build_framework_transcript_asset(session.id, state, assistant_message_id="current-answer")
    assert asset["schema_version"] == 2
    assert asset["source_input_message_id"] == "current-user"
    assert asset["source_output_message_ids"] == ["current-answer"]
    assert asset["covered_message_ids"][-2:] == ["current-user", "current-answer"]
    assert asset["business_message_map"]["current-answer"] == ["current-native-answer"]
    assert asset["business_message_map"]["current-user"] == ["current-user"]
    assert asset["content_sha256"] == approval_value_hash({key: value for key, value in asset.items() if key != "content_sha256"})


def test_publication_does_not_claim_coverage_when_framework_rewrites_the_frozen_prefix():
    from src.application.sessions.session_service import SessionService

    session, request, runtime = context_case()
    state = completed_state(session, request, runtime)
    state["framework_messages"][0]["data"]["content"] = "changed by summary"
    with pytest.raises(ValueError, match="prefix"):
        SessionService._build_framework_transcript_asset(session.id, state, assistant_message_id="current-answer")


def test_unversioned_legacy_summary_is_not_silently_combined_with_native_history():
    session, request, runtime = context_case()
    session.metadata["context_summary"] = {"summary": "legacy summary", "covers_until_index": 2}
    with pytest.raises(ValueError, match="summary"):
        runtime._build_initial_state(session, request, include_framework_transcript=True)


@pytest.mark.parametrize("enabled", [False, True])
def test_context_runtime_capability_is_explicit_and_keeps_legacy_support(enabled):
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    _, request, _ = context_case()
    reader = FrameworkWorkerCapabilities("a" * 64, True, context_transcript_enabled=enabled)
    reader.require(request.runtime_binding.model_copy(update={"runtime_version": "v2.0.0"}), operation="message")
    if enabled:
        reader.require(request.runtime_binding, operation="message")
    else:
        with pytest.raises(TaskDeferred, match="framework_runtime_version_unavailable"):
            reader.require(request.runtime_binding, operation="message")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["old_reader", "legacy_binding", "bad_digest", "wrong_model", "schema1", "protocol"])
async def test_incompatible_context_is_deferred_before_claim(case):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred
    from tests.test_agent_v2_worker_contract import _Settings, _Tasks, command, stored_task

    session, request, _ = context_case()
    asset = covered_asset(session, request.runtime_binding)
    selected = request.runtime_binding
    if case == "legacy_binding":
        selected = selected.model_copy(update={"runtime_version": "v2.0.0"})
    if case == "bad_digest":
        asset["content_sha256"] = "0" * 64
    if case == "wrong_model":
        selected = selected.model_copy(update={"model_config_fingerprint": "changed"})
    if case == "schema1":
        from src.schemas.framework_transcript import framework_transcript_asset
        asset = framework_transcript_asset(session_id=session.id, source_turn_id="old-turn",
            runtime_version="v2.0.0", framework_messages=asset["framework_messages"]).model_dump(mode="json")
    if case == "protocol":
        asset["framework_messages"].pop(2)
        asset["content_sha256"] = approval_value_hash({key: value for key, value in asset.items() if key != "content_sha256"})
    session.metadata["framework_transcript_asset"] = asset
    task = stored_task(selected)
    task.status = "queued"
    tasks = _Tasks(task)

    async def load_stage(task_id, *, session_id):
        return SimpleNamespace(phase="submitted")

    tasks.get_framework_message_stage = load_stage

    async def load(identity):
        assert identity == session.id
        return session

    dispatcher = AgentTaskDispatcher(_Settings(), tasks, SimpleNamespace(_require_session=load), None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True,
            context_transcript_enabled=case != "old_reader"))
    with pytest.raises(TaskDeferred, match="unavailable"):
        await dispatcher.handle_v2(QueuedTask("delivery", command(selected).model_dump(mode="json")), worker_id="reader")
    assert tasks.claimed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["message_recover", "approval_resume", "approval_recover", "framework_reconcile"])
async def test_recovery_does_not_reassemble_the_latest_session_transcript(operation):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_agent_v2_worker_contract import _Settings, _Tasks, command, stored_task

    _, request, _ = context_case()
    task = stored_task(request.runtime_binding)
    task.operation = operation
    task.reference_id = "original-reference"
    tasks = _Tasks(task)
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    executions = []

    async def execute(received):
        executions.append(received.id)

    dispatcher._execute = execute
    envelope = command(request.runtime_binding).model_copy(update={"operation": operation, "reference_id": task.reference_id})
    await dispatcher.handle_v2(QueuedTask("delivery", envelope.model_dump(mode="json")), worker_id="reader")
    assert tasks.claimed == ["reader"] and executions == [task.id]


@pytest.mark.asyncio
async def test_a_frozen_graph_input_does_not_recheck_mutable_session_metadata():
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_agent_v2_worker_contract import _Settings, _Tasks, command, stored_task

    _, request, _ = context_case()
    task = stored_task(request.runtime_binding)
    task.status = "queued"
    tasks = _Tasks(task)

    async def load_stage(task_id, *, session_id):
        return SimpleNamespace(phase="runtime_submitted")

    tasks.get_framework_message_stage = load_stage
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    executions = []

    async def execute(received):
        executions.append(received.id)

    dispatcher._execute = execute
    await dispatcher.handle_v2(QueuedTask("delivery", command(request.runtime_binding).model_dump(mode="json")), worker_id="reader")
    assert tasks.claimed == ["reader"] and executions == [task.id]

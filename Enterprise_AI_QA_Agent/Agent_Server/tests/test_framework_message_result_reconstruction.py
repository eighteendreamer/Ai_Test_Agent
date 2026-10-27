"""Read original ordinary-message output without asset or execution authority."""
from __future__ import annotations

from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from src.application.testing.verification_service import VerificationService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolModule
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.session_event_sink import SessionEventSink
from src.schemas.framework_graph_input import FrameworkProjectionIdentity
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from src.schemas.session import ExecutionRequest, SendMessageRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import fresh_runtime_harness, build_runtime
from tests.test_framework_approval_recovery import expire


@pytest_asyncio.fixture
async def message_asset_gap(fresh_runtime_harness, monkeypatch, request):
    h = fresh_runtime_harness
    selected = getattr(request, "param", "tools")
    tool_key = "api-tester" if selected == "api-tester" else "inspect_value"
    descriptor = h.registry.get("inspect_value").model_copy(update={"key": tool_key, "permission_level": "safe"})
    if tool_key == "api-tester":
        monkeypatch.setitem(h.registry._tools, tool_key, ToolModule(descriptor, tool_key))
    else:
        h.registry.register_dynamic(descriptor, tool_key)
    responses = [AIMessage(id="message-final", content="finished")]
    if selected != "none":
        calls = [{"name": tool_key, "args": {"value": key}, "id": key, "type": "tool_call"} for key in ("A", "B")]
        if selected == "planning":
            calls = [{"name": "write_todos", "args": {"todos": [{"content": "inspect", "status": "completed"}]},
                "id": "todo", "type": "tool_call"}, *calls]
        responses.insert(0, AIMessage(id="message-source", content="", tool_calls=calls))
    build_runtime(h, responses=responses)
    h.runtime._deep_agent_cognitive_planning_enabled = selected == "planning"

    async def handle(arguments, context):
        h.effects.append((arguments["value"], context.tool_job_id))
        return {"status": "failed" if selected == "failed" else "completed", "summary": "original tool evidence",
            "z": {"z": 1, "a": 2}, "a": arguments["value"], "checks": [{"passed": selected != "failed"}],
            **({"large": "x" * 60_000} if selected == "large" else {})}

    h.runtime._tool_runtime_service._handlers[tool_key] = handle
    if selected == "budget":
        from src.application.models.framework_model_budget_projection import capture_model_budget_material
        adapter = h.runtime._deep_agent_bound_runtime_adapter
        execute = adapter.execute

        async def with_budget(request):
            result = await execute(request)
            return replace(result, metadata={**result.metadata,
                "root_model_budget": capture_model_budget_material(h.binding, await h.calls.snapshot(h.binding))})

        monkeypatch.setattr(adapter, "execute", with_budget)
    session = await h.store.get_session(h.session_id)
    if selected == "history":
        from langchain_core.messages import ToolMessage
        from src.application.deep_agents.framework_transcript import serialize_messages
        from src.application.runtime.tool_messages import build_chat_tool_messages
        from src.schemas.framework_approval import framework_tool_call_id
        from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY
        from src.schemas.framework_transcript import framework_transcript_asset
        from tests.test_framework_tool_projection import sample
        from tests.test_runtime_binding_persistence import make_binding

        old_binding = make_binding(h.session_id, "historical-turn")
        _, source, raw = sample()
        record = raw.record.model_copy(update={"call_id": framework_tool_call_id(old_binding, source)})
        display, = build_chat_tool_messages(old_binding.turn_id, [record.model_dump(mode="python")],
            binding_id=old_binding.binding_id, hygiene_metadata={})
        old = FrameworkToolProjection.capture(binding=old_binding, source=source, record=record,
            message=display, model_content=raw.model_content)
        messages = [AIMessage(id=source.source_message_id, content="", tool_calls=[{
            "id": source.tool_call_id, "name": source.tool_name, "args": source.arguments, "type": "tool_call"}]),
            ToolMessage(id="historical-native-tool", name=source.tool_name, tool_call_id=source.tool_call_id,
                content=old.model_content, artifact={TOOL_PROJECTION_KEY: old.model_dump(mode="json")}),
            AIMessage(id="historical-native-answer", content="previous finished")]
        asset = framework_transcript_asset(session_id=h.session_id, source_turn_id=old_binding.turn_id,
            runtime_version=old_binding.runtime_version, framework_messages=serialize_messages(messages))
        await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
            mutate=lambda metadata, status: metadata.update(framework_transcript_asset=asset.model_dump(mode="json")),
            turn_lease_token=h.owner.lease_token)
        session = await h.store.get_session(h.session_id)
    h.initial_task_id = h.owner.id
    h.stages = FrameworkMessageStages(h.tasks)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id=h.owner.trace_id,
        user_message="Inspect A/B", normalized_input="Inspect A/B", mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": [tool_key]})
    await h.stages.begin(h.owner.runtime_owner, message_source_manifest(session, h.binding,
        SendMessageRequest.model_validate(h.owner.input)))
    await h.stages.freeze_execution(h.owner.runtime_owner, request)
    await h.stages.submit_runtime(h.owner.runtime_owner)
    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="message-assistant",
        snapshot_id="message-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 28))
    captured = []

    async def before_asset(result):
        captured.append(FrameworkRuntimeResult.from_result(result))
        h.verification_oracle = VerificationService().build_results(session_id=h.session_id, turn_id=h.binding.turn_id,
            trace_id=result.state["trace_id"], tool_results=result.state["tool_results"], context_bundle=result.state["context_bundle"])
        raise ConnectionError("injected ordinary message asset gap after event seal")

    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ConnectionError, match="ordinary message asset gap"):
        await sink.run(h.runtime.execute_turn, session, request, projection_identity=identity,
            on_graph_input=lambda raw: h.stages.record_graph_input(h.owner.runtime_owner, raw),
            on_runtime_result=before_asset)
    h.oracle, = captured
    assert not h.oracle.approvals and not h.oracle.pending_turn
    h.expired = await expire(h, h.owner)
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest
    assert await h.store.get_latest_snapshot(h.session_id) is None
    return h


async def read_original(h):
    reader = getattr(h.runtime, "read_completed_framework_message", None)
    assert callable(reader), "Ordinary asset gaps need their production source-only result assembly port"
    return await reader(h.binding, h.owner.id, tasks=h.tasks)


def tool_originals(h):
    from src.application.deep_agents.framework_transcript import deserialize_messages
    original = h.stages.get(h.initial_task_id, h.session_id).source_manifest["graph_input_asset"]
    from src.schemas.framework_graph_input import parse_graph_input_asset
    return parse_graph_input_asset(original), deserialize_messages(h.oracle.state["framework_messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["none", "tools", "api-tester", "failed", "large", "budget", "planning", "history"], indirect=True)
async def test_message_original_result_is_complete_repeatable_and_read_only(message_asset_gap, monkeypatch):
    h = message_asset_gap
    stage = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    budget, effects, requests = await h.calls.snapshot(h.binding), deepcopy(h.effects), list(h.adapter_requests)

    def forbidden(*args, **kwargs):
        pytest.fail("Original message read attempted current execution, rule preparation or application writes")

    adapter = h.runtime._deep_agent_bound_runtime_adapter
    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    monkeypatch.setattr(VerificationService, "prepare_projection", forbidden)
    monkeypatch.setattr(FrameworkRuntimeResults, "freeze", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", forbidden)
    first = await read_original(h)
    second = await read_original(h)
    assert first == second
    assert first.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert first.verification_results == h.verification_oracle
    if h.oracle.state["tool_results"] and h.oracle.state["tool_results"][0]["tool_key"] == "api-tester":
        assert first.verification_results
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.store.list_events(h.session_id) == events
    assert await h.calls.snapshot(h.binding) == budget
    assert h.effects == effects and h.adapter_requests == requests
    assert await h.tasks.get(h.owner.id) == h.expired
    assert await h.store.get_latest_snapshot(h.session_id) is None
    # Read output is material only; the existing admission must still refuse.
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["event", "preparation", "job", "producer"])
async def test_message_reader_rechecks_originals_after_source_only_native_io(message_asset_gap, monkeypatch, changed):
    h = message_asset_gap
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    open_reader = adapter.open_native_reader

    @asynccontextmanager
    async def read_then_change(*args, **kwargs):
        async with open_reader(*args, **kwargs) as pair:
            yield pair
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            if changed == "event":
                cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || %s::jsonb WHERE id=%s",
                    ('{"read_drift":true}', h.oracle.events[-1].id))
            elif changed == "preparation":
                from src.schemas.framework_approval import approval_value_hash
                import json
                stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
                stage["source_manifest"]["read_drift"] = True
                stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
                cur.execute(f"UPDATE {h.stages._table} SET record=%s::jsonb WHERE task_id=%s",
                    (json.dumps(stage), h.initial_task_id))
            elif changed == "job":
                cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET summary='read drift' WHERE id=%s",
                    (h.oracle.state["tool_results"][0]["job_id"],))
            else:
                cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET attempt_id='foreign-attempt' WHERE id=%s", (h.owner.id,))

    monkeypatch.setattr(adapter, "open_native_reader", read_then_change)
    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        await read_original(h)
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest
    assert await h.store.get_latest_snapshot(h.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "conflicting_ordinal", "job_output", "old_slot"])
async def test_message_tool_materials_do_not_invent_or_omit_original_results(message_asset_gap, changed):
    from src.runtime.framework_message_result_reader import _tool_materials
    from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY
    from src.schemas.framework_approval import approval_value_hash
    from langchain_core.messages import ToolMessage

    h = message_asset_gap
    original, messages = tool_originals(h)
    jobs = await h.jobs.list_jobs(session_id=h.session_id)
    native = [message for message in messages if isinstance(message, ToolMessage)]
    if changed == "missing":
        native[0].artifact = None
    else:
        raw = native[0].artifact[TOOL_PROJECTION_KEY]
        if changed == "conflicting_ordinal":
            raw["message"]["metadata"]["ordinal"] = native[1].artifact[TOOL_PROJECTION_KEY]["message"]["metadata"]["ordinal"]
        elif changed == "job_output":
            raw["record"]["output"] = {"changed": True}
        else:
            raw.pop("runtime_message")
            raw["schema_version"] = 1
        raw["sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "sha256"})
    with pytest.raises((FrameworkMessageStageRecoveryRequired, ValueError)):
        _tool_materials(original, h.binding, messages, jobs)


@pytest.mark.asyncio
async def test_message_original_tool_order_is_not_the_native_message_order(message_asset_gap):
    from src.runtime.framework_message_result_reader import _tool_materials
    from langchain_core.messages import ToolMessage

    h = message_asset_gap
    original, messages = tool_originals(h)
    jobs = await h.jobs.list_jobs(session_id=h.session_id)
    records = _tool_materials(original, h.binding, list(reversed(messages)), jobs)
    assert [item.record.call_id for item in records] == [item["call_id"] for item in h.oracle.state["tool_results"]]
    assert len([item for item in messages if isinstance(item, ToolMessage)]) == len(records) == 2


@pytest.mark.asyncio
async def test_only_exact_frozen_foreign_tool_history_is_not_a_current_result(message_asset_gap):
    from src.application.deep_agents.framework_transcript import serialize_messages
    from src.runtime.framework_message_result_reader import _tool_materials
    from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY
    from src.schemas.framework_approval import approval_value_hash
    from langchain_core.messages import ToolMessage

    h = message_asset_gap
    original, messages = tool_originals(h)
    foreign = deepcopy(next(item for item in messages if isinstance(item, ToolMessage)))
    foreign.id = "original-frozen-historical-tool"
    raw = foreign.artifact[TOOL_PROJECTION_KEY]
    raw["binding_fingerprint"] = "historical-binding-fingerprint"
    raw["sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "sha256"})
    frozen = original.model_copy(update={"framework_messages": serialize_messages([foreign])})
    jobs = await h.jobs.list_jobs(session_id=h.session_id)
    result = _tool_materials(frozen, h.binding, [foreign, *messages], jobs)
    assert len(result) == len(jobs) == 2
    with pytest.raises((FrameworkMessageStageRecoveryRequired, ValueError)):
        _tool_materials(original, h.binding, [foreign, *messages], jobs)

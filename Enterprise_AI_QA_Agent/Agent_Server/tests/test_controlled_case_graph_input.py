"""Frozen native input is not formal RunItem admission or a recovery permit."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict
from langchain_core.tools import tool

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.deep_agents.native_terminal import collect_native_terminal, require_native_terminal_original
from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter, ControlledCaseGraphRequest
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeBinding, RuntimeCheckpointSource
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_adapter import adapter_request
from tests.test_controlled_case_graph import config, wrapped_tools
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply
from tests.test_framework_model_policy import governed
from tests.test_runtime_binding_persistence import make_binding


def contract():
    return importlib.import_module("src.schemas.controlled_case_graph_input")


@tool
def original_tool(path: str) -> str:
    """Read only the fixed source."""
    raise AssertionError("Schema inspection executed a tool")


def frozen_input():
    binding = make_binding("session", "turn", runtime_kind="langgraph")
    request = ControlledCaseGraphRequest(session_id="session", turn_id="turn", trace_id="frozen-trace",
        model_key="primary", checkpoint_namespace=binding.checkpoint_namespace, system_prompt="original fixed context",
        framework_messages=serialize_messages([AIMessage(id="prior", content=[
            {"type": "thinking", "thinking": "original reasoning", "signature": "fixture-signature"}],
            response_metadata={"fixture": "original"}), HumanMessage(id="input", content="fixed case")]),
        business_tools=[original_tool], interrupt_on={original_tool.name: {"allowed_decisions": ["approve", "reject"]}})
    return binding, request, contract().freeze_controlled_graph_input(binding=binding, request=request)


def rehash(raw):
    raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
    return raw


def test_frozen_input_round_trips_full_binding_native_blocks_tool_schemas_and_approval_policy():
    binding, request, asset = frozen_input()
    raw = asset.model_dump(mode="json")
    assert raw["schema_version"] == 1 and raw["runtime_kind"] == "langgraph"
    assert raw["binding"] == binding.model_dump(mode="json")
    assert raw["framework_messages"] == request.framework_messages
    assert raw["tool_schemas"][0]["function"]["name"] == original_tool.name
    assert contract().parse_controlled_graph_input(raw).model_dump(mode="json") == raw
    request.framework_messages[0]["data"]["content"][0]["signature"] = "later mutation"
    request.interrupt_on[original_tool.name]["allowed_decisions"].clear()
    assert asset.model_dump(mode="json") == raw
    assert not {"owner_token", "runtime_owner", "resume", "continue_from_checkpoint", "run_item_attempt_id"}.intersection(raw)


@pytest.mark.parametrize("changed", ["system_prompt", "trace_id", "model_key", "messages", "tool_schema", "approval", "binding"])
def test_frozen_input_rejects_unsealed_changes(changed):
    _, _, asset = frozen_input()
    raw = asset.model_dump(mode="json")
    if changed in {"system_prompt", "trace_id", "model_key"}:
        raw[changed] = "not original"
    elif changed == "messages":
        raw["framework_messages"][0]["data"]["content"][0]["signature"] = "not original"
    elif changed == "tool_schema":
        raw["tool_schemas"][0]["function"]["description"] = "not original"
    elif changed == "approval":
        raw["interrupt_on"].clear()
    else:
        raw["binding"].pop("fingerprint")
        raw["binding"]["prompt_version"] = "not original"
    with pytest.raises(ValueError, match="digest"):
        contract().parse_controlled_graph_input(raw)


@pytest.mark.parametrize("changed", ["future_schema", "boolean_schema", "extra", "kind", "lossy_messages",
    "missing_ids", "duplicate_ids", "duplicate_tools", "foreign_approval", "invalid_decision", "empty_messages"])
def test_rehashed_invalid_format_is_not_a_supported_frozen_input(changed):
    _, _, asset = frozen_input()
    raw = asset.model_dump(mode="json")
    if changed == "future_schema":
        raw["schema_version"] = 2
    elif changed == "boolean_schema":
        raw["schema_version"] = True
    elif changed == "extra":
        raw["new_format"] = 1
    elif changed == "kind":
        raw["runtime_kind"] = "deepagents"
    elif changed == "lossy_messages":
        raw["framework_messages"] = [{"role": "user", "content": "lossy"}]
    elif changed == "missing_ids":
        raw["framework_messages"][0]["data"]["id"] = None
    elif changed == "duplicate_ids":
        raw["framework_messages"][1]["data"]["id"] = raw["framework_messages"][0]["data"]["id"]
    elif changed == "duplicate_tools":
        raw["tool_schemas"].append(deepcopy(raw["tool_schemas"][0]))
    elif changed == "foreign_approval":
        raw["interrupt_on"]["foreign_tool"] = True
    elif changed == "invalid_decision":
        raw["interrupt_on"][original_tool.name]["allowed_decisions"] = ["new-decision"]
    else:
        raw["framework_messages"] = []
    with pytest.raises(ValueError):
        contract().parse_controlled_graph_input(rehash(raw))


@pytest.mark.parametrize("mode", ["resume", "continue", "callable_description"])
def test_freezing_cannot_store_a_decision_recovery_permission_or_executable_configuration(mode):
    binding, request, _ = frozen_input()
    if mode == "resume":
        request = replace(request, resume={"interrupt": {"type": "approve"}})
    elif mode == "continue":
        request = replace(request, continue_from_checkpoint=True)
    else:
        request.interrupt_on[original_tool.name]["description"] = lambda *args: "not persistable"
    with pytest.raises(ValueError):
        contract().freeze_controlled_graph_input(binding=binding, request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_official_owned_graph_consumes_frozen_input_and_source_only_reader_rechecks_it(harness, transport, monkeypatch):
    reply = _tool_reply(transport)
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        async with governed(harness, _config(transport, url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            effects = []
            tools = wrapped_tools(h, effects)
            adapter, request = adapter_request(h, business_tools=tools)
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            raw = asset.model_dump(mode="json")
            request.framework_messages[0]["data"]["content"] = "mutable caller must not replace original input"
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute_frozen(raw, business_tools=tools)
                same = await adapter.execute_frozen(asset, business_tools=tools, continue_from_checkpoint=True)
            assert len(requests) == 2 and len(effects) == 1
            assert messages_to_dict(outcome.values["messages"]) == messages_to_dict(same.values["messages"])
            assert serialize_messages(outcome.values["messages"][:1]) == raw["framework_messages"]
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            before = await h.calls.snapshot(h.binding)
            assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)

            def forbidden(*args, **kwargs):
                pytest.fail("Frozen native reader attempted execution or current tool reconstruction")

            monkeypatch.setattr(adapter, "_model_resolver", forbidden)
            monkeypatch.setattr(adapter, "_model_call_policy", None)
            async with adapter.open_native_reader(raw, source=source) as (graph, reader):
                read = await collect_native_terminal(graph, reader, config(h))
                assert read == outcome.native_terminal
                checkpoints = [item async for item in reader.alist(config(h))]
                assert checkpoints and all(item.metadata[contract().CONTROLLED_GRAPH_INPUT_KEY] == asset.asset_sha256
                    for item in checkpoints)
                with pytest.raises(PermissionError):
                    await graph.ainvoke({"messages": [HumanMessage(id="forbidden", content="forbidden")]},
                        config(h), durability="sync")
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                require_native_terminal_original(cur, h.settings, source, read, raw)
            assert await h.calls.snapshot(h.binding) == before and len(requests) == 2 and len(effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["binding", "tool_schema", "missing_tool", "nested_mutation"])
async def test_frozen_execution_rejects_binding_or_tool_drift_before_resolution_budget_or_native_writes(harness, changed, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            tools = wrapped_tools(h, [])
            adapter, request = adapter_request(h, business_tools=tools)
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            if changed == "binding":
                raw = asset.model_dump(mode="json")
                raw["binding"].pop("fingerprint")
                raw["binding"]["skill_version"] = "different artifact"
                raw["binding"] = RuntimeBinding.model_validate(raw["binding"]).model_dump(mode="json")
                asset = contract().parse_controlled_graph_input(rehash(raw))
            elif changed == "tool_schema":
                tools = [tools[0].model_copy(update={"description": "different tool contract"})]
            elif changed == "missing_tool":
                tools = []
            else:
                asset.framework_messages[0]["data"]["content"] = "changed nested content"

            async def forbidden(*args):
                pytest.fail("Frozen input drift resolved a model")

            monkeypatch.setattr(adapter, "_model_resolver", forbidden)
            with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ValueError):
                await adapter.execute_frozen(asset, business_tools=tools)
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0
            async with h.provider.open(owner=h.owner.runtime_owner) as saver:
                assert await saver.aget_tuple(config(h)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["binding", "nested_mutation"])
async def test_frozen_read_checks_full_original_identity_before_provider_open(changed):
    binding, _, asset = frozen_input()
    source = RuntimeCheckpointSource(binding=binding, owner_epoch=1, task_id="task", attempt_id="attempt", worker_id="worker")
    if changed == "binding":
        source = source.model_copy(update={"binding": binding.model_copy(update={"tool_schema_version": "different"})})
    else:
        asset.framework_messages.clear()

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid frozen original opened a provider")

    adapter = ControlledCaseGraphAdapter(model_resolver=forbidden, checkpointer_factory=forbidden, model_call_policy=None)
    with pytest.raises(ValueError):
        async with adapter.open_native_reader(asset, source=source):
            pytest.fail("Invalid frozen original became readable")


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_frozen_native_approval_uses_original_policy_without_refreezing_or_consuming_input_twice(harness, decision):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            effects = []
            tools = wrapped_tools(h, effects)
            adapter, request = adapter_request(h, business_tools=tools,
                interrupt_on={"inspect_source": {"allowed_decisions": ["approve", "reject"], "description": "frozen approval"}})
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            original = asset.model_dump(mode="json")
            with framework_owner_scope(h.owner.runtime_owner):
                waiting = await adapter.execute_frozen(asset, business_tools=tools)
                pending, = waiting.native_interrupts
                assert effects == [] and len(requests) == 1 and waiting.native_terminal is None
                completed = await adapter.execute_frozen(original, business_tools=tools,
                    resume={pending.interrupt_id: {"type": decision}})
            assert completed.native_terminal is not None and not completed.native_interrupts
            assert len(requests) == 2 and len(effects) == int(decision == "approve")
            assert asset.model_dump(mode="json") == original
            assert serialize_messages(completed.values["messages"][:1]) == original["framework_messages"]
            assert (await h.calls.snapshot(h.binding)).reserved_calls == 2
            async with h.provider.open(owner=h.owner.runtime_owner) as saver:
                checkpoints = [item async for item in saver.alist(config(h))]
                assert checkpoints and all(item.metadata[contract().CONTROLLED_GRAPH_INPUT_KEY] == asset.asset_sha256
                    for item in checkpoints)


@pytest.mark.parametrize("changed", ["tool_order", "boolean_approval", "invalid_tool_name", "binding_kind"])
def test_frozen_tool_order_and_explicit_format_remain_strict(changed):
    binding, request, asset = frozen_input()
    if changed == "tool_order":
        second = original_tool.model_copy(update={"name": "second_tool"})
        request = replace(request, business_tools=[original_tool, second])
        asset = contract().freeze_controlled_graph_input(binding=binding, request=request)
        with pytest.raises(ValueError, match="schemas or order"):
            asset.request(business_tools=[second, original_tool])
        return
    raw = asset.model_dump(mode="json")
    if changed == "boolean_approval":
        raw["interrupt_on"][original_tool.name] = 1
    elif changed == "invalid_tool_name":
        raw["tool_schemas"][0]["function"]["name"] = ["not a name"]
    else:
        raw["binding"].pop("fingerprint")
        raw["binding"]["runtime_kind"] = "deepagents"
        raw["binding"] = RuntimeBinding.model_validate(raw["binding"]).model_dump(mode="json")
    with pytest.raises(ValueError):
        contract().parse_controlled_graph_input(rehash(raw))


def test_frozen_tool_iterable_is_materialized_once_without_losing_execution_tools():
    _, _, asset = frozen_input()
    rebuilt = asset.request(business_tools=iter([original_tool]))
    assert rebuilt.business_tools == [original_tool]


def test_tool_schema_comparison_preserves_json_boolean_and_number_types():
    binding, request, _ = frozen_input()
    schema = original_tool.args_schema.model_json_schema()
    schema["additionalProperties"] = False
    original = original_tool.model_copy(update={"args_schema": schema})
    asset = contract().freeze_controlled_graph_input(binding=binding, request=replace(request, business_tools=[original]))
    changed = deepcopy(schema)
    changed["additionalProperties"] = 0
    different = original.model_copy(update={"args_schema": changed})
    with pytest.raises(ValueError, match="schemas or order"):
        asset.request(business_tools=[different])


def test_parsed_json_input_does_not_retain_mutable_caller_aliases():
    _, _, asset = frozen_input()
    raw = asset.model_dump(mode="json")
    parsed = contract().parse_controlled_graph_input(raw)
    raw["framework_messages"][0]["data"]["content"][0]["signature"] = "caller changed after parsing"
    raw["tool_schemas"][0]["function"]["parameters"]["properties"]["path"]["type"] = "integer"
    assert parsed.model_dump(mode="json") == asset.model_dump(mode="json")


@pytest.mark.parametrize("gating", [False, True])
def test_frozen_boolean_approval_config_preserves_the_pinned_sdk_form(gating):
    binding, request, _ = frozen_input()
    request = replace(request, interrupt_on={original_tool.name: gating})
    asset = contract().freeze_controlled_graph_input(binding=binding, request=request)
    assert asset.model_dump(mode="json")["interrupt_on"] == {original_tool.name: gating}
    assert asset.request(business_tools=[original_tool]).interrupt_on == request.interrupt_on


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["reader", "atomic", "continue", "initial_replay"])
async def test_same_binding_does_not_authorize_a_different_input_or_initial_replay(harness, phase):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            original = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            different = contract().freeze_controlled_graph_input(binding=h.binding,
                request=replace(request, system_prompt="different but independently valid frozen input"))
            assert original.asset_sha256 != different.asset_sha256
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute_frozen(original, business_tools=[])
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            budget = await h.calls.snapshot(h.binding)
            async with h.provider.open(source=source) as saver:
                before = [item async for item in saver.alist(config(h))]
            if phase == "reader":
                with pytest.raises(ValueError, match="frozen input"):
                    async with adapter.open_native_reader(different, source=source):
                        pytest.fail("Checkpoint accepted a different independently valid frozen input")
            elif phase == "atomic":
                from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

                with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="frozen input"):
                        require_native_terminal_original(cur, h.settings, source, outcome.native_terminal, different)
            else:
                with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ValueError):
                    await adapter.execute_frozen(original if phase == "initial_replay" else different,
                        business_tools=[], continue_from_checkpoint=phase == "continue")
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == budget
            async with h.provider.open(source=source) as saver:
                assert [item async for item in saver.alist(config(h))] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["reader", "atomic", "continue", "initial"])
async def test_frozen_input_cannot_adopt_an_existing_unmarked_checkpoint(harness, phase):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute(request)
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            budget = await h.calls.snapshot(h.binding)
            async with h.provider.open(source=source) as saver:
                before = [item async for item in saver.alist(config(h))]
            if phase == "reader":
                with pytest.raises(ValueError, match="frozen input"):
                    async with adapter.open_native_reader(asset, source=source):
                        pytest.fail("A new asset adopted an unmarked checkpoint")
            elif phase == "atomic":
                from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

                with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="frozen input"):
                        require_native_terminal_original(cur, h.settings, source, outcome.native_terminal, asset)
            else:
                with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ValueError, match="frozen input"):
                    await adapter.execute_frozen(asset, business_tools=[], continue_from_checkpoint=phase == "continue")
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == budget
            async with h.provider.open(source=source) as saver:
                assert [item async for item in saver.alist(config(h))] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["resume", "continue"])
async def test_frozen_input_without_a_checkpoint_is_not_continuation_authority(harness, mode, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)

            async def forbidden(*args):
                pytest.fail("A bare asset resolved a model for checkpoint continuation")

            monkeypatch.setattr(adapter, "_model_resolver", forbidden)
            budget = await h.calls.snapshot(h.binding)
            with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ValueError, match="frozen input"):
                await adapter.execute_frozen(asset, business_tools=[], continue_from_checkpoint=mode == "continue",
                    resume={"foreign": {"type": "approve"}} if mode == "resume" else None)
            assert requests == [] and await h.calls.snapshot(h.binding) == budget
            async with h.provider.open(owner=h.owner.runtime_owner) as saver:
                assert await saver.aget_tuple(config(h)) is None


@pytest.mark.asyncio
async def test_native_reader_uses_the_input_captured_before_provider_await(harness, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute_frozen(asset, business_tools=[])
            raw = asset.model_dump(mode="json")
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            provider = adapter._checkpointer_factory
            budget = await h.calls.snapshot(h.binding)

            @asynccontextmanager
            async def mutate_at_open(**kwargs):
                raw["system_prompt"] = "caller changed while awaiting provider"
                rehash(raw)
                async with provider(**kwargs) as saver:
                    yield saver

            monkeypatch.setattr(adapter, "_checkpointer_factory", mutate_at_open)
            async with adapter.open_native_reader(raw, source=source) as (graph, reader):
                assert await collect_native_terminal(graph, reader, config(h)) == outcome.native_terminal
            assert raw["asset_sha256"] != asset.asset_sha256
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == budget


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["missing", "different"])
@pytest.mark.parametrize("phase", ["reader", "atomic", "continue"])
async def test_checkpoint_input_reference_drift_is_rejected_without_repair_or_reexecution(harness, marker, phase):
    from psycopg import sql
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), binding_overrides={"runtime_kind": "langgraph"}) as h:
            adapter, request = adapter_request(h)
            asset = contract().freeze_controlled_graph_input(binding=h.binding, request=request)
            with framework_owner_scope(h.owner.runtime_owner):
                outcome = await adapter.execute_frozen(asset, business_tools=[])
            source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                expression = (sql.SQL("metadata - %s") if marker == "missing" else
                    sql.SQL("jsonb_set(metadata, ARRAY[%s]::text[], to_jsonb(%s::text))"))
                query = (sql.SQL("UPDATE {}.checkpoints SET metadata = ").format(
                    sql.Identifier(h.settings.deep_agents.checkpoint_schema)) + expression
                    + sql.SQL(" WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s"))
                values = [contract().CONTROLLED_GRAPH_INPUT_KEY]
                if marker == "different":
                    values.append("0" * 64)
                cur.execute(query.as_string(), (*values, h.binding.thread_id, h.binding.checkpoint_namespace,
                    outcome.native_terminal.proof.checkpoint_id))
                assert cur.rowcount == 1
            budget = await h.calls.snapshot(h.binding)
            async with h.provider.open(source=source) as saver:
                before = [item async for item in saver.alist(config(h))]
            if phase == "reader":
                with pytest.raises(ValueError, match="frozen input"):
                    async with adapter.open_native_reader(asset, source=source):
                        pytest.fail("Reader ignored the persisted input reference drift")
            elif phase == "atomic":
                with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="frozen input"):
                        require_native_terminal_original(cur, h.settings, source, outcome.native_terminal, asset)
            else:
                with framework_owner_scope(h.owner.runtime_owner), pytest.raises(ValueError, match="frozen input"):
                    await adapter.execute_frozen(asset, business_tools=[], continue_from_checkpoint=True)
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == budget
            async with h.provider.open(source=source) as saver:
                assert [item async for item in saver.alist(config(h))] == before

"""Actual official structured graph output, not an injected result asset."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import asyncio
import json

import pytest
from deepagents import create_deep_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Interrupt
from pydantic import ValidationError

from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest
from src.schemas.framework_cognitive_result import FrameworkCognitiveResult
from tests.test_framework_native_terminal import Model
from src.application.deep_agents.result_projection import DeepAgentRuntimeError, project_deep_agent_result
from src.schemas.framework_cognitive_result import STRUCTURED_OUTPUT_VERSION_KEY
from tests.test_framework_message_result_reconstruction import (
    harness, checkpoint_harness, projection_harness, fresh_runtime_harness, message_asset_gap, read_original,
)
from tests.test_framework_approval_graph_input import prepared_approval_runtime, frozen_approval_result, replace_stage
from tests.test_framework_approval_result_reconstruction import approval_asset_gap


def guidance():
    return {"schema_version": 1, "scope": "cognitive_guidance_only", "summary": "Inspect the referenced boundary.",
        "evidence_refs": ["fixture:source:17"], "limitations": ["No domain Runner was executed."]}


def structured_message(**updates):
    return AIMessage(id="structured-answer", content="", tool_calls=[{
        "id": "cognitive-output", "name": "FrameworkCognitiveResult", "args": {**guidance(), **updates}, "type": "tool_call"}])


@pytest.mark.asyncio
async def test_official_structured_result_can_complete_without_assistant_text(monkeypatch):
    model = Model(responses=[structured_message()])

    async def resolve(key):
        return model

    def factory(**kwargs):
        assert isinstance(kwargs["response_format"], ToolStrategy)
        assert kwargs["response_format"].schema is FrameworkCognitiveResult
        assert kwargs["response_format"].handle_errors is False
        return create_deep_agent(**kwargs)

    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve)
    monkeypatch.setattr(adapter, "_resolve_factory", lambda: factory)
    result = await adapter.execute(DeepAgentRuntimeRequest(session_id="session", turn_id="turn", trace_id="trace",
        model_key="fixture", system_prompt="Return cognitive guidance only.", structured_output_version=1,
        messages=[{"role": "user", "content": "inspect"}]))
    assert result.output_text == ""
    assert result.metadata["structured_result"] == guidance()
    assert result.framework_messages[-2]["data"]["tool_calls"][0]["name"] == "FrameworkCognitiveResult"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"summary": " "}, {"scope": "test_passed"}])
async def test_official_invalid_structured_result_does_not_retry_the_model(monkeypatch, changed):
    from langchain.agents.structured_output import StructuredOutputValidationError

    model = Model(responses=[structured_message(**changed), structured_message()])
    generate = model._generate
    calls = []

    def observed(*args, **kwargs):
        calls.append(True)
        return generate(*args, **kwargs)

    monkeypatch.setattr(model, "_generate", observed)

    async def resolve(key):
        return model

    with pytest.raises(DeepAgentRuntimeError) as error:
        await DeepAgentRuntimeAdapter(model_resolver=resolve).execute(DeepAgentRuntimeRequest(
            session_id="session", turn_id="turn", trace_id="trace", model_key="fixture",
            system_prompt="Return cognitive guidance only.", structured_output_version=1,
            messages=[{"role": "user", "content": "inspect"}]))
    assert isinstance(error.value.__cause__, StructuredOutputValidationError)
    assert len(calls) == 1


@pytest.mark.parametrize("changed", [{"summary": ""}, {"summary": "  "}, {"schema_version": True},
    {"schema_version": 2}, {"scope": "test_passed"}, {"extra": "unsupported"}, {"limitations": "none"}])
def test_cognitive_result_rejects_invalid_or_domain_verdict_claims(changed):
    with pytest.raises(ValidationError):
        FrameworkCognitiveResult.model_validate({**guidance(), **changed})


@pytest.mark.parametrize("version", [0, 2, True, "1"])
@pytest.mark.asyncio
async def test_unsupported_output_contract_is_rejected_before_model_resolution(version):
    async def forbidden(key):
        pytest.fail("An unsupported output contract resolved a model")

    adapter = DeepAgentRuntimeAdapter(model_resolver=forbidden)
    with pytest.raises(ValueError, match="structured output version"):
        await adapter.execute(DeepAgentRuntimeRequest(session_id="session", turn_id="turn", trace_id="trace",
            model_key="fixture", system_prompt="inspect", messages=[], structured_output_version=version))


@pytest.mark.parametrize("value", [None, {}, {"summary": "untyped"}, {**guidance(), "scope": "test_passed"}])
def test_selected_contract_never_accepts_missing_or_arbitrary_structured_output(value):
    with pytest.raises(DeepAgentRuntimeError, match="valid cognitive structured result"):
        project_deep_agent_result({"messages": [AIMessage(content="old text")], "structured_response": value},
            stage="DA-E1", subagent_events=[], structured_output_version=1)


def test_structured_state_cannot_upgrade_an_unselected_legacy_contract():
    with pytest.raises(DeepAgentRuntimeError, match="unselected structured result"):
        project_deep_agent_result({"messages": [AIMessage(content="text")], "structured_response": guidance()},
            stage="DA-E1", subagent_events=[])


def test_structured_projection_retains_no_text_and_does_not_reuse_older_assistant_text():
    values = {"messages": [AIMessage(id="older", content="old answer"), structured_message(),
        ToolMessage(id="output-ack", content="SDK output acknowledgment", tool_call_id="cognitive-output")],
        "structured_response": FrameworkCognitiveResult.model_validate(guidance())}
    original = deepcopy(values)
    result = project_deep_agent_result(values, stage="DA-E1", subagent_events=[], structured_output_version=1)
    assert result.output_text == "" and result.metadata["structured_result"] == guidance()
    result.metadata["structured_result"]["evidence_refs"].append("changed")
    assert values == original


def test_structured_waiting_result_cannot_become_a_completion():
    result = project_deep_agent_result({"messages": [AIMessage(content="old")], "structured_response": guidance(),
        "__interrupt__": [Interrupt(id="pending", value={})]}, stage="DA-E4", subagent_events=[], structured_output_version=1)
    assert result.output_text == "" and result.interrupts
    assert "structured_result" not in result.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", [{"case": case, "structured": True, "output_message": structured_message()}
    for case in ("none", "tools")], indirect=True)
@pytest.mark.parametrize("terminal_events", [None, False, True])
async def test_structured_message_native_read_and_finalization_preserve_original_result(message_asset_gap, monkeypatch, terminal_events):
    from tests.test_framework_message_asset_gap_recovery import test_terminal_delivery_seals_original_and_runs_only_the_existing_finalizer
    from tests.test_framework_unsealed_result_reconstruction import remove_fixture_seal

    h = message_asset_gap
    if terminal_events is not None:
        remove_fixture_seal(h, terminal_events=terminal_events)
    first = await read_original(h)
    assert first.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert first.runtime_result.output_text == ""
    assert first.runtime_result.state["model_response_summary"]["structured_result"] == guidance()
    original = first.graph_input_asset
    assert original.feature_flags["structured_output_version"] == 1
    assert original.required_message_stage_version == h.stages.get(h.initial_task_id, h.session_id).schema_version == 24
    completed = [event for event in first.runtime_result.events if event.type == "runtime.turn_completed"]
    assert len(completed) == 1 and completed[0].payload["structured_output_version"] == 1
    await test_terminal_delivery_seals_original_and_runs_only_the_existing_finalizer(h, monkeypatch)
    session = await h.store.get_session(h.session_id)
    assert session.status.value == "completed"
    assert not any(message.role == "assistant" for message in session.messages)
    snapshot = await h.store.get_latest_snapshot(h.session_id)
    assert snapshot.graph_state["model_response_summary"]["structured_result"] == guidance()


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"structured": True, "output_message": structured_message()}], indirect=True)
async def test_structured_approval_finalizes_without_an_assistant_row_and_ignores_current_switch(frozen_approval_result, monkeypatch):
    from src.runtime.execution_context import framework_owner_scope

    h = frozen_approval_result
    asset = h.result_asset
    assert asset.runtime_result.output_text == ""
    assert asset.runtime_result.state["model_response_summary"]["structured_result"] == guidance()
    assert asset.runtime_result.state["context_bundle"][STRUCTURED_OUTPUT_VERSION_KEY] == 1
    assert all(request.structured_output_version == 1 for request in h.adapter_requests)
    h.runtime._deep_agent_structured_output_enabled = False
    with framework_owner_scope(h.result_task.runtime_owner):
        response = await h.sessions._finalize_runtime_result(session=await h.store.get_session(h.session_id),
            runtime_result=asset.runtime_result.restore(), assistant_message_id=asset.assistant_message_id,
            user_message_override=asset.user_message, turn_lease_token=h.result_task.lease_token,
            projection_owner=h.result_task.runtime_owner, projection_asset=asset)
        await h.stages.projected_approval(h.result_task.runtime_owner, response.output.id)
    assert response.output.content == "" and response.output.id == asset.runtime_result.snapshot.id
    assert not any(message.role == "assistant" for message in (await h.store.get_session(h.session_id)).messages)
    assert h.stages.get(h.initial_task_id, h.session_id).phase == "projected"


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"structured": True, "output_message": structured_message()}], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_structured_approval_unsealed_recovery_keeps_original_result(approval_asset_gap, monkeypatch, terminal_events):
    from tests.test_framework_unsealed_result_reconstruction import test_unsealed_approval_reconciliation_seals_original_and_only_finalizes

    h = approval_asset_gap
    assert h.oracle.output_text == ""
    assert h.oracle.state["model_response_summary"]["structured_result"] == guidance()
    await test_unsealed_approval_reconciliation_seals_original_and_only_finalizes(h, monkeypatch, terminal_events)
    assert (await h.store.get_latest_snapshot(h.session_id)).graph_state["model_response_summary"]["structured_result"] == guidance()


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"structured": True, "output_message": structured_message()}], indirect=True)
@pytest.mark.parametrize("changed", ["continuation", "asset"])
async def test_rehashed_result_cannot_drop_the_original_structured_contract(frozen_approval_result, changed):
    from src.runtime.framework_message_stages import validate_message_stage, FrameworkMessageStageRecoveryRequired
    from src.schemas.framework_approval import approval_value_hash

    h = frozen_approval_result
    record = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
    manifest = record["source_manifest"]
    if changed == "continuation":
        graph = manifest["approval_graph_inputs"][h.result_task.id]["graph_input_asset"]
        graph["feature_flags"].pop("structured_output_version")
        graph["runtime_preparation"]["initial_state"]["context_bundle"].pop(STRUCTURED_OUTPUT_VERSION_KEY)
        graph["asset_sha256"] = approval_value_hash({key: value for key, value in graph.items() if key != "asset_sha256"})
        manifest.pop("runtime_result_asset")
    else:
        raw = manifest["runtime_result_asset"]
        for state in (raw["runtime_result"]["state"], raw["runtime_result"]["snapshot"]["graph_state"]):
            state["context_bundle"].pop(STRUCTURED_OUTPUT_VERSION_KEY)
            state["model_response_summary"].pop("structured_result")
            state["model_response_summary"].pop("structured_output_version")
        raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    record["source_sha256"] = approval_value_hash(manifest)
    if changed == "continuation":
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="structured output contract"):
            validate_message_stage(record)
    else:
        replace_stage(h, record)
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="structured output contract"):
            await h.results.load(h.result_task.runtime_owner)


@pytest.mark.asyncio
async def test_official_structured_tool_strategy_preserves_governed_wire_and_evidence(harness, monkeypatch):
    from src.application.models.framework_model_evidence import FrameworkModelEvidence
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from tests.test_artifact_storage_service import _RustFSClient
    from tests.test_framework_model_policy import governed, execute
    from tests.test_framework_chat_models import _config, _provider, _tool_reply

    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0]["message"]["tool_calls"] = [{"id": "cognitive-output", "type": "function", "function": {
        "name": "FrameworkCognitiveResult", "arguments": json.dumps(guidance())}}]
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            objects = ArtifactStorageService(h.settings)
            client = _RustFSClient()
            monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
            h.policy._evidence = FrameworkModelEvidence(objects=objects)
            result = await execute(h, replace(h.request, structured_output_version=1))
            assert result.output_text == "" and result.metadata["structured_result"] == guidance()
            assert len(requests) == 1
            tool, = requests[0]["body"]["tools"]
            assert tool["function"]["name"] == "FrameworkCognitiveResult"
            assert tool["function"]["parameters"]["additionalProperties"] is False
            budget = await h.calls.snapshot(h.binding)
            call, = budget.calls
            assert call.phase == "completed" and budget.actual_tokens == 10
            saved = await h.policy._evidence.read_response(h.binding, call.intent, call.response_observations[0].response)
            assert saved["data"]["structured_response"] == guidance()
            assert saved["data"]["messages"][0]["data"]["tool_calls"][0]["id"] == "cognitive-output"


def test_structured_output_defaults_off():
    from src.core.config import DeepAgentsConfig

    assert DeepAgentsConfig().structured_output_enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("observe_input", [False, True])
async def test_unprepared_runtime_keeps_text_contract_when_structured_switch_is_enabled(observe_input):
    from src.application.runtime.runtime_service import RuntimeService
    from src.application.deep_agents.result_projection import DeepAgentRuntimeResult
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from tests.test_deep_agent_runtime_adapter import _ModelRuntimeStub, _runtime_session, _runtime_request

    captured = []
    requests = []

    async def capture(asset):
        captured.append(asset)

    class Adapter:
        async def execute(self, request):
            requests.append(request)
            assert request.structured_output_version is None
            return DeepAgentRuntimeResult(output_text="Original text contract")

    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=Adapter(), deep_agent_enabled=True, deep_agent_pilot_mode_keys=["code_review"],
        deep_agent_structured_output_enabled=True)
    session = _runtime_session("session-unprepared-structured")
    request = _runtime_request(session.id, "turn-unprepared-structured")
    request.context[STRUCTURED_OUTPUT_VERSION_KEY] = 1
    result = await runtime.execute_turn(session, request, on_graph_input=capture if observe_input else None)
    assert result.output_text == "Original text contract" and len(requests) == 1
    assert STRUCTURED_OUTPUT_VERSION_KEY not in result.state["context_bundle"]
    if observe_input:
        assert len(captured) == 1 and captured[0]["schema_version"] == 1
        assert "structured_output_version" not in captured[0]["feature_flags"]
    else:
        assert not captured


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", ["inspect_value", {
    "structured": True, "output_message": structured_message()}], indirect=True)
async def test_waiting_approval_keeps_original_contract_when_operator_changes_switch(prepared_approval_runtime):
    from tests.test_framework_approval_continuation import claim_decision

    h = prepared_approval_runtime
    original_version = h.stages.get(h.initial_task_id, h.session_id).source_manifest["graph_input_asset"]["feature_flags"].get("structured_output_version")
    h.runtime._deep_agent_structured_output_enabled = original_version is None
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        for key in ("B", "A"):
            task = await claim_decision(h, key=key)
            response = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
            await h.tasks.finish(task, status="completed", result_status="waiting_approval" if key == "B" else "completed")
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert len(h.adapter_requests) == 2
    assert all(request.structured_output_version == original_version for request in h.adapter_requests)
    assert response.session.status.value == "completed"
    assert response.output.content == ("" if original_version is not None else "finished")


@pytest.mark.asyncio
async def test_checkpoint_provider_preserves_typed_result_under_official_strict_msgpack(checkpoint_harness, monkeypatch):
    from langgraph.checkpoint.serde import _msgpack
    from tests.test_framework_checkpoint_fencing import start

    h = checkpoint_harness
    await start(h)
    monkeypatch.setattr(_msgpack, "STRICT_MSGPACK_ENABLED", True)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        value = FrameworkCognitiveResult.model_validate(guidance())
        assert saver.serde.loads_typed(saver.serde.dumps_typed(value)) == value
        from pydantic import BaseModel

        class UnregisteredCheckpointType(BaseModel):
            value: str

        foreign = UnregisteredCheckpointType(value="inert fixture")
        assert saver.serde.loads_typed(saver.serde.dumps_typed(foreign)) != foreign

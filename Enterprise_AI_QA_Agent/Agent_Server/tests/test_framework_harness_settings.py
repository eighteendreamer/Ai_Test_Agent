from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import fresh_runtime_harness
from tests.test_framework_approval_graph_input import prepared_approval_runtime, approval_input
from tests.test_framework_approval_continuation import claim_decision
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from tests.test_framework_approval_continuation import service
from tests.test_framework_graph_input_asset import prepared_graph_input
from tests.test_runtime_binding_persistence import make_binding
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_harness_settings import FrameworkHarnessSettings, HARNESS_SETTINGS_KEY
from src.schemas.framework_graph_input import parse_graph_input_asset


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{}, {"harness_options": {
    "read_only_filesystem_enabled": True, "cognitive_planning_enabled": True, "cognitive_subagents_enabled": True}}],
    indirect=True, ids=["original-disabled", "original-enabled"])
@pytest.mark.parametrize("changed", ["numeric", "topology", "pending"])
async def test_approval_uses_original_limits_instead_of_current_worker_settings(prepared_approval_runtime, changed):
    h = prepared_approval_runtime
    stage = h.stages.get(h.initial_task_id, h.session_id)
    asset = parse_graph_input_asset(stage.source_manifest["graph_input_asset"])
    assert stage.schema_version == asset.required_message_stage_version == 29
    settings = asset.harness_settings
    original = settings.adapter_options()
    if changed == "pending":
        def corrupt(metadata):
            for context in (metadata["pending_turn"]["context_bundle"],
                metadata["pending_turn"]["graph_state"]["context_bundle"]):
                context[HARNESS_SETTINGS_KEY]["max_subagent_calls_per_turn"] = 999
        await h.store.mutate_metadata(h.session_id, corrupt)
    else:
        for name, value in original.items():
            if (type(value) is bool) == (changed == "topology"):
                setattr(h.runtime, "_deep_agent_" + name, not value if type(value) is bool else value + 10)
    task = await claim_decision(h)
    execute = h.runtime._deep_agent_bound_runtime_adapter.execute
    observed = []

    async def inspect(request):
        actual = {name: getattr(request, name) for name in original}
        assert actual == original, "Approval changed the original Turn's cognitive limits"
        assert callable(request.on_subagent_event) == settings.cognitive_subagents_enabled
        observed.append(actual)
        return await execute(request)

    h.runtime._deep_agent_bound_runtime_adapter.execute = inspect
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        result = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert observed == [original] and result.session.status.value == "waiting_approval"
    saved = h.stages.get(h.initial_task_id, h.session_id).source_manifest["approval_graph_inputs"][task.id]
    assert parse_graph_input_asset(saved["graph_input_asset"]).harness_settings == settings


@pytest.mark.asyncio
async def test_approval_cannot_replace_consistently_rehashed_harness_settings(prepared_approval_runtime):
    h = prepared_approval_runtime
    task = await claim_decision(h)
    original = h.stages.get(h.initial_task_id, h.session_id)
    raw = approval_input(h, task)
    for context in (raw["context"], raw["runtime_preparation"]["initial_state"]["context_bundle"]):
        context[HARNESS_SETTINGS_KEY]["max_subagent_calls_per_turn"] += 1
    changed = parse_graph_input_asset(rehash(raw))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="original Harness settings"):
        await service(h).record_graph_input(task.runtime_owner, task.reference_id,
            task.input["decision_id"], changed.model_dump(mode="json"))
    assert h.stages.get(h.initial_task_id, h.session_id) == original
    assert h.adapter_requests == h.effects == []


def original_asset(*, cognitive=False):
    settings = FrameworkHarnessSettings(pilot_stage="DA-E4", read_only_filesystem_enabled=cognitive,
        read_only_max_file_size_mb=7, read_only_max_output_chars=9000, cognitive_planning_enabled=cognitive,
        cognitive_subagents_enabled=cognitive, max_subagent_calls_per_turn=2,
        subagent_model_call_limit=4, subagent_tool_call_limit=8)
    raw = prepared_graph_input(make_binding("session", "turn"), "trace").model_dump(mode="json")
    raw["context"][HARNESS_SETTINGS_KEY] = settings.model_dump()
    raw["runtime_preparation"]["initial_state"]["context_bundle"][HARNESS_SETTINGS_KEY] = settings.model_dump()
    raw["feature_flags"].update(harness_settings_version=1, read_only_filesystem=cognitive,
        cognitive_planning=cognitive, cognitive_subagents=cognitive, governed_tools=True)
    return raw


def rehash(raw):
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    return raw


@pytest.mark.parametrize("field,value", [("max_subagent_calls_per_turn", 0), ("subagent_model_call_limit", -1),
    ("subagent_tool_call_limit", True), ("read_only_max_file_size_mb", "7"), ("read_only_max_output_chars", 1.5),
    ("cognitive_planning_enabled", "true"), ("schema_version", 2), ("schema_version", True), ("unknown", 1)])
def test_harness_settings_reject_invalid_or_unknown_semantics(field, value):
    raw = original_asset()["context"][HARNESS_SETTINGS_KEY]
    raw[field] = value
    with pytest.raises(ValueError):
        FrameworkHarnessSettings.model_validate(raw)


@pytest.mark.parametrize("changed", ["context", "preparation", "topology", "version", "missing"])
def test_frozen_harness_requires_same_actual_input_preparation_and_topology(changed):
    raw = original_asset()
    if changed == "context":
        raw["context"][HARNESS_SETTINGS_KEY]["subagent_tool_call_limit"] += 1
    elif changed == "preparation":
        raw["runtime_preparation"]["initial_state"]["context_bundle"][HARNESS_SETTINGS_KEY]["subagent_model_call_limit"] += 1
    elif changed == "topology":
        raw["feature_flags"]["cognitive_subagents"] = True
    elif changed == "version":
        raw["feature_flags"]["harness_settings_version"] = True
    else:
        raw["context"].pop(HARNESS_SETTINGS_KEY)
    with pytest.raises(ValueError):
        parse_graph_input_asset(rehash(raw))


def test_old_asset_does_not_acquire_harness_semantics_from_arbitrary_context():
    raw = original_asset()
    raw["feature_flags"].pop("harness_settings_version")
    parsed = parse_graph_input_asset(rehash(raw))
    assert parsed.harness_settings is None and parsed.required_message_stage_version == 12


def test_new_format_is_rejected_by_old_worker_before_claim():
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    asset = parse_graph_input_asset(rehash(original_asset()))
    current = FrameworkWorkerCapabilities("a" * 64, True)
    current.require_message_stage(asset.required_message_stage_version)
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=28).require_message_stage(
            asset.required_message_stage_version)
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        current.require_message_stage(current.message_stage_max_version + 1)


@pytest.mark.parametrize("cognitive", [False, True])
def test_source_only_schema_uses_exact_original_harness_options(monkeypatch, cognitive):
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.schemas.runtime_binding import RuntimeCheckpointSource

    original = parse_graph_input_asset(rehash(original_asset(cognitive=cognitive)))
    source = RuntimeCheckpointSource(binding=original.runtime_preparation.identity.binding, owner_epoch=1,
        task_id="task", attempt_id="attempt", worker_id="worker")
    adapter = DeepAgentRuntimeAdapter(model_resolver=None)
    configure = adapter._configure_harness
    observed = []

    def capture(model, **kwargs):
        observed.append(deepcopy(kwargs))
        return configure(model, **kwargs)

    monkeypatch.setattr(adapter, "_configure_harness", capture)
    adapter.native_schema(original, source=source, saver=None)
    assert {name: observed[0][name] for name in original.harness_settings.adapter_options()} == original.harness_settings.adapter_options()
    assert observed[0]["_schema_only"] is True

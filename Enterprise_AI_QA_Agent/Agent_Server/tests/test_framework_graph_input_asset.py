"""Contract tests for the immutable Deep Agents graph input asset."""
from __future__ import annotations

import pytest

from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import selected
from tests.test_agent_task_dispatch import harness


def test_graph_input_asset_round_trips_and_binds_its_digest():
    from src.schemas.framework_graph_input import FrameworkGraphInputAsset, graph_input_asset

    asset = graph_input_asset(
        session_id="session-1",
        turn_id="turn-1",
        trace_id="trace-1",
        model_key="model-1",
        checkpoint_namespace="qa:v2",
        system_prompt="system",
        messages=[{"role": "user", "content": "inspect"}],
        framework_messages=[],
        context={"mode_key": "code_review"},
        business_tool_keys=["read-only"],
        interrupt_tool_keys=[],
        feature_flags={"cognitive_planning": False},
    )
    restored = FrameworkGraphInputAsset.model_validate_json(asset.model_dump_json())
    assert restored == asset
    assert len(asset.asset_sha256) == 64


@pytest.mark.parametrize("field", ["system_prompt", "messages", "context", "asset_sha256"])
def test_graph_input_asset_rejects_post_freeze_mutation(field):
    from src.schemas.framework_graph_input import graph_input_asset

    asset = graph_input_asset(
        session_id="session-1", turn_id="turn-1", trace_id="trace-1", model_key="model-1",
        checkpoint_namespace=None, system_prompt="system", messages=[], framework_messages=[],
        context={}, business_tool_keys=[], interrupt_tool_keys=[], feature_flags={},
    )
    record = asset.model_dump(mode="json")
    if field == "system_prompt":
        record[field] += " changed"
    elif field == "messages":
        record[field] = [{"role": "user", "content": "changed"}]
    elif field == "context":
        record[field] = {"changed": True}
    else:
        record[field] = "0" * 64
    with pytest.raises(ValueError):
        type(asset).model_validate(record)


@pytest.mark.asyncio
async def test_runtime_submitted_stage_records_graph_asset_once(admission):
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
    from src.schemas.framework_graph_input import graph_input_asset
    from src.schemas.session import ExecutionRequest

    h = admission
    task = await selected(h)
    stages = FrameworkMessageStages(h.tasks)
    session = await h.store.get_session(h.session_id)
    await stages.begin(task.runtime_owner, message_source_manifest(
        session, h.binding, h.payload, modes=h.sessions._mode_registry.list(),
    ))
    request = ExecutionRequest(
        turn_id=h.binding.turn_id, trace_id=task.trace_id, session_id=h.session_id,
        user_message=h.payload.content, normalized_input=h.payload.content,
        mode_key="default", model_key="primary", runtime_binding=h.binding,
    )
    await stages.freeze_execution(task.runtime_owner, request)
    await stages.submit_runtime(task.runtime_owner)
    asset = graph_input_asset(
        session_id=h.session_id, turn_id=h.binding.turn_id, trace_id=task.trace_id,
        model_key="primary", checkpoint_namespace=h.binding.checkpoint_namespace,
        system_prompt="system", messages=[{"role": "user", "content": "inspect"}],
        framework_messages=[], context={"mode_key": "default"}, business_tool_keys=[],
        interrupt_tool_keys=[], feature_flags={},
    )
    saved = await stages.record_graph_input(task.runtime_owner, asset)
    assert saved.phase == "runtime_submitted"
    assert saved.source_manifest["graph_input_asset"] == asset.model_dump(mode="json")
    assert await stages.record_graph_input(task.runtime_owner, asset) == saved
    changed = graph_input_asset(
        session_id=h.session_id, turn_id=h.binding.turn_id, trace_id=task.trace_id,
        model_key="primary", checkpoint_namespace=h.binding.checkpoint_namespace,
        system_prompt="changed", messages=[{"role": "user", "content": "inspect"}],
        framework_messages=[], context={"mode_key": "default"}, business_tool_keys=[],
        interrupt_tool_keys=[], feature_flags={},
    )
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        await stages.record_graph_input(task.runtime_owner, changed)

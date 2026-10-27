"""Contract tests for the immutable Deep Agents graph input asset."""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import selected
from tests.test_agent_task_dispatch import harness


def prepared_graph_input(binding, trace_id, *, identity=None, initial_state=None):
    from src.schemas.framework_graph_input import (
        FrameworkProjectionIdentity, FrameworkRuntimePreparation, graph_input_asset,
    )

    identity = identity or FrameworkProjectionIdentity(binding=binding, assistant_message_id="prepared-assistant",
        snapshot_id="prepared-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
    state = initial_state if initial_state is not None else {
        "session_id": binding.session_id, "turn_id": binding.turn_id, "trace_id": trace_id,
        "selected_model_key": "primary", "selected_model_name": "Original display name",
        "context_bundle": {"runtime_binding": binding.model_dump(mode="json")},
        "event_log": [{"type": "runtime.turn_started", "payload": {"message": "original"}}],
    }
    return graph_input_asset(session_id=binding.session_id, turn_id=binding.turn_id, trace_id=trace_id,
        model_key="primary", checkpoint_namespace=binding.checkpoint_namespace, system_prompt="system",
        messages=[], framework_messages=[], context={"skill_keys": ["original-skill"]}, business_tool_keys=[],
        interrupt_tool_keys=[], feature_flags={},
        runtime_preparation=FrameworkRuntimePreparation(identity=identity, initial_state=state))


def test_prepared_graph_input_round_trip_and_nested_copy():
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from tests.test_runtime_binding_persistence import make_binding

    asset = prepared_graph_input(make_binding("session-1", "turn-1"), "trace-1")
    state = asset.runtime_preparation.initial_state
    copied = prepared_graph_input(asset.runtime_preparation.identity.binding, "trace-1", initial_state=state)
    state["event_log"][0]["payload"]["message"] = "mutated after freeze"
    assert copied.runtime_preparation.initial_state["event_log"][0]["payload"]["message"] == "original"
    assert parse_graph_input_asset(copied.model_dump(mode="json")) == copied
    assert copied.schema_version == 2


@pytest.mark.parametrize("changed", [None, "approval", "decision", "mode", "epoch", "event", "bad_log", "bad_event", "bad_payload", "unknown", "digest"])
def test_approval_graph_preparation_has_an_exact_invocation_boundary(changed):
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_graph_input import FrameworkApprovalGraphInput
    from tests.test_runtime_binding_persistence import make_binding

    raw = prepared_graph_input(make_binding("session-1", "turn-1"), "trace-1").model_dump(mode="json")
    raw["runtime_preparation"]["initial_state"]["event_log"].append({"type": "turn.resumed", "payload": {
        "approval_id": "approval-1", "decision_id": "decision-1", "continue_from_checkpoint": False}})
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    value = dict(task_id="approval-task", attempt_id="attempt-1", owner_epoch=1,
        approval_id="approval-1", decision_id="decision-1", continue_from_checkpoint=False, graph_input_asset=raw)
    if changed in {"approval", "decision"}:
        value[changed + "_id"] = "foreign"
    elif changed == "mode":
        value["continue_from_checkpoint"] = 0
    elif changed == "epoch":
        value["owner_epoch"] = True
    elif changed == "unknown":
        value["future"] = True
    elif changed == "event":
        raw["runtime_preparation"]["initial_state"]["event_log"][-1]["type"] = "turn.completed"
        raw["asset_sha256"] = approval_value_hash({key: item for key, item in raw.items() if key != "asset_sha256"})
    elif changed in {"bad_log", "bad_event", "bad_payload"}:
        events = raw["runtime_preparation"]["initial_state"]["event_log"]
        if changed == "bad_log":
            raw["runtime_preparation"]["initial_state"]["event_log"] = "invalid"
        elif changed == "bad_event":
            events[-1] = "invalid"
        else:
            events[-1]["payload"] = "invalid"
        raw["asset_sha256"] = approval_value_hash({key: item for key, item in raw.items() if key != "asset_sha256"})
    elif changed == "digest":
        raw["system_prompt"] = "mutated"
    if changed is None:
        parsed = FrameworkApprovalGraphInput.model_validate(value)
        assert parsed.model_dump(mode="json") == value
    else:
        with pytest.raises(ValueError):
            FrameworkApprovalGraphInput.model_validate(value)


@pytest.mark.parametrize("flag,marker", [(True, 1), (4, 4), ("1", 1), (None, 1), (1, None), (1, True), (2, 1), (1, 2), (3, 2)])
def test_graph_tool_material_format_cannot_drift_from_prepared_runtime(flag, marker):
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from tests.test_runtime_binding_persistence import make_binding

    raw = prepared_graph_input(make_binding("session-1", "turn-1"), "trace-1").model_dump(mode="json")
    raw["feature_flags"]["tool_projection_version"] = flag
    if marker is not None:
        raw["runtime_preparation"]["initial_state"]["context_bundle"]["framework_tool_projection_version"] = marker
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    with pytest.raises(ValueError, match="projection material"):
        parse_graph_input_asset(raw)


@pytest.mark.parametrize("changed", ["binding", "trace", "model", "state", "digest", "future"])
def test_prepared_graph_input_rejects_drift_even_with_rehashed_envelope(changed):
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from src.schemas.framework_approval import approval_value_hash
    from tests.test_runtime_binding_persistence import make_binding

    asset = prepared_graph_input(make_binding("session-1", "turn-1"), "trace-1")
    raw = asset.model_dump(mode="json")
    if changed == "binding":
        raw["runtime_preparation"]["identity"]["binding"]["turn_id"] = "other-turn"
    elif changed in {"trace", "model"}:
        raw["trace_id" if changed == "trace" else "model_key"] = "other"
    elif changed == "state":
        raw["runtime_preparation"]["initial_state"]["context_bundle"] = {}
    elif changed == "future":
        raw["schema_version"] = 3
    if changed == "digest":
        raw["runtime_preparation"]["initial_state"]["selected_model_name"] = "changed"
    else:
        raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
    with pytest.raises(ValueError):
        parse_graph_input_asset(raw)


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
@pytest.mark.parametrize("tool_projection_version", [None, 1, 2, 3])
async def test_runtime_submitted_stage_records_graph_asset_once(admission, tool_projection_version):
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
    if tool_projection_version is not None:
        from src.schemas.framework_graph_input import FrameworkProjectionIdentity, FrameworkRuntimePreparation

        identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="prepared-assistant",
            snapshot_id="prepared-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
        preparation = FrameworkRuntimePreparation(identity=identity, initial_state={"session_id": h.session_id,
            "turn_id": h.binding.turn_id, "trace_id": task.trace_id, "selected_model_key": "primary",
            "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json"),
                "framework_tool_projection_version": tool_projection_version}})
    asset = graph_input_asset(
        session_id=h.session_id, turn_id=h.binding.turn_id, trace_id=task.trace_id,
        model_key="primary", checkpoint_namespace=h.binding.checkpoint_namespace,
        system_prompt="system", messages=[{"role": "user", "content": "inspect"}],
        framework_messages=[], context={"mode_key": "default"}, business_tool_keys=[],
        interrupt_tool_keys=[], feature_flags={"tool_projection_version": tool_projection_version} if tool_projection_version else {},
        runtime_preparation=preparation if tool_projection_version else None,
    )
    saved = await stages.record_graph_input(task.runtime_owner, asset)
    assert saved.phase == "runtime_submitted"
    assert saved.source_manifest["graph_input_asset"] == asset.model_dump(mode="json")
    if tool_projection_version is not None:
        from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
        from src.runtime.task_deferred import TaskDeferred

        expected_version = 12 + tool_projection_version
        assert saved.schema_version == asset.required_message_stage_version == expected_version
        FrameworkWorkerCapabilities("a" * 64, True).require_message_stage(saved.schema_version)
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=16).require_message_stage(17)
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=17).require_message_stage(18)
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=18).require_message_stage(19)
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            FrameworkWorkerCapabilities("a" * 64, True).require_message_stage(20)
        with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
            FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=expected_version - 1).require_message_stage(saved.schema_version)
        from src.runtime.framework_message_stages import validate_message_stage, FrameworkMessageStageRecoveryRequired
        downgraded = saved.model_dump(mode="json")
        downgraded["schema_version"] = expected_version - 1
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="compatible message stage"):
            validate_message_stage(downgraded)
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


@pytest.mark.parametrize("changed", [None, "missing_flag", "unknown_flag", "bool_flag", "missing_material", "foreign_trace"])
def test_graph_verification_preparation_requires_stage19_and_exact_original_format(changed):
    from src.application.testing.verification_service import VerificationService
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from src.schemas.framework_verification_projection import VERIFICATION_PREPARATION_KEY
    from tests.test_runtime_binding_persistence import make_binding

    legacy = prepared_graph_input(make_binding("session-1", "turn-1"), "trace-1")
    raw = legacy.model_dump(mode="json")
    assert parse_graph_input_asset(raw).model_dump(mode="json") == raw
    assert legacy.required_message_stage_version == 12
    context = raw["runtime_preparation"]["initial_state"]["context_bundle"]
    context[VERIFICATION_PREPARATION_KEY] = VerificationService.prepare_projection(
        legacy.runtime_preparation.identity, trace_id="foreign" if changed == "foreign_trace" else "trace-1")
    raw["feature_flags"]["verification_projection_version"] = 1
    if changed == "missing_flag":
        raw["feature_flags"].pop("verification_projection_version")
    elif changed in {"unknown_flag", "bool_flag"}:
        raw["feature_flags"]["verification_projection_version"] = 2 if changed == "unknown_flag" else True
    elif changed == "missing_material":
        context.pop(VERIFICATION_PREPARATION_KEY)
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    if changed is None:
        assert parse_graph_input_asset(raw).required_message_stage_version == 19
    else:
        with pytest.raises(ValueError):
            parse_graph_input_asset(raw)

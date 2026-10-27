"""Original root budget facts are material, never permission to send or settle."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_model_call import (
    FrameworkModelBudget, FrameworkModelBudgetSnapshot, FrameworkModelCallIntent,
    FrameworkModelCallRecord, FrameworkModelUsage,
)
from tests.test_runtime_binding_persistence import make_binding


def original_budget():
    binding = make_binding("session", "turn", model_config_fingerprint="fingerprint")
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    calls = []
    for index, phase in enumerate(("ready", "invoking", "unknown", "completed", "completed")):
        calls.append(FrameworkModelCallRecord(binding_id=binding.binding_id,
            intent=FrameworkModelCallIntent(attempt_id=f"attempt-{index}", logical_call_id=f"position-{index}",
                branch_id="root", purpose="agent", model_key="fixture", model_config_fingerprint="fingerprint",
                transport="openai_chat_completions", request_sha256="a" * 64,
                reserved_input_tokens=12, reserved_output_tokens=8),
            owner_epoch=1, owner_task_id="task", owner_attempt_id="task-attempt", phase=phase,
            created_at=now, usage=FrameworkModelUsage(input_tokens=2, output_tokens=1, total_tokens=3)
                if index == 4 else None))
    return binding, FrameworkModelBudgetSnapshot(policy=FrameworkModelBudget(max_calls=8, max_tokens=1000,
        deadline_at=now + timedelta(minutes=5), model_config_fingerprints={"fixture": "fingerprint"}), calls=calls)


def test_original_root_budget_roundtrip_keeps_reservations_unknown_usage_and_legacy_wire():
    from src.application.models.framework_model_budget_projection import capture_model_budget_material, read_model_budget_material

    binding, snapshot = original_budget()
    original = snapshot.model_dump(mode="json")
    material = capture_model_budget_material(binding, snapshot)
    assert set(material["snapshot"]) == {"policy", "calls"}
    assert "settled_response_id" not in material["snapshot"]["calls"][0]
    restored = read_model_budget_material(binding, material)
    assert restored == snapshot
    assert restored.reserved_calls == 5 and restored.charged_tokens == 83 and restored.actual_tokens == 3
    assert restored.unknown_calls == restored.unmeasured_calls == 1
    restored.calls.clear()
    assert len(material["snapshot"]["calls"]) == 5 and snapshot.model_dump(mode="json") == original
    material["snapshot"]["calls"].clear()
    assert snapshot.model_dump(mode="json") == original


@pytest.mark.parametrize("changed", ["missing", "version", "bool_version", "scope", "extra", "binding_id",
    "binding_fingerprint", "digest", "tokens", "computed", "future_snapshot", "foreign_call", "duplicate_call",
    "coercion", "missing_usage", "foreign_policy"])
def test_root_budget_material_rejects_missing_drift_unknown_formats_and_cross_binding_calls(changed):
    from src.application.models.framework_model_budget_projection import capture_model_budget_material, read_model_budget_material

    binding, snapshot = original_budget()
    value = capture_model_budget_material(binding, snapshot)
    if changed == "missing":
        value = None
    elif changed in {"version", "bool_version"}:
        value["schema_version"] = True if changed == "bool_version" else 2
    elif changed in {"scope", "binding_id", "binding_fingerprint"}:
        value[changed] = "changed"
    elif changed == "extra":
        value["future"] = "unsupported"
    elif changed == "digest":
        value["sha256"] = "b" * 64
    elif changed == "tokens":
        value["snapshot"]["calls"][0]["intent"]["reserved_input_tokens"] += 1
    else:
        if changed in {"computed", "future_snapshot"}:
            value["snapshot"]["reserved_calls" if changed == "computed" else "future"] = 5
        elif changed == "foreign_call":
            value["snapshot"]["calls"][0]["binding_id"] = "other"
        elif changed == "duplicate_call":
            value["snapshot"]["calls"].append(deepcopy(value["snapshot"]["calls"][0]))
        elif changed == "coercion":
            value["snapshot"]["calls"][0]["owner_epoch"] = "1"
        elif changed == "missing_usage":
            value["snapshot"]["calls"][0].pop("usage")
        elif changed == "foreign_policy":
            value["snapshot"]["policy"]["model_config_fingerprints"] = {"foreign": "different"}
        value["sha256"] = approval_value_hash({key: item for key, item in value.items() if key != "sha256"})
    with pytest.raises(ValueError):
        read_model_budget_material(binding, value)


def test_result_projection_carries_original_budget_without_changing_legacy_counters():
    from langchain_core.messages import AIMessage
    from src.application.deep_agents.result_projection import project_deep_agent_result
    from src.application.models.framework_model_budget_projection import capture_model_budget_material

    binding, snapshot = original_budget()
    material = capture_model_budget_material(binding, snapshot)
    legacy = project_deep_agent_result({"messages": [AIMessage(content="answer")]}, stage="DA-E1", subagent_events=[])
    assert "root_model_budget" not in legacy.metadata
    result = project_deep_agent_result({"messages": [AIMessage(content="answer")]}, stage="DA-E1", subagent_events=[],
        root_model_budget=material)
    assert result.metadata["root_model_budget"] == material
    result.metadata["root_model_budget"]["snapshot"]["calls"].clear()
    assert len(material["snapshot"]["calls"]) == 5


def test_empty_original_ledger_is_valid_material_not_an_invented_model_call():
    from src.application.models.framework_model_budget_projection import capture_model_budget_material, read_model_budget_material

    binding, snapshot = original_budget()
    snapshot.calls.clear()
    restored = read_model_budget_material(binding, capture_model_budget_material(binding, snapshot))
    assert restored == snapshot
    assert restored.reserved_calls == restored.charged_tokens == restored.actual_tokens == 0

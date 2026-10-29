"""Original root budget facts are material, never permission to send or settle."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_model_call import (
    FrameworkModelBudget, FrameworkModelBudgetSnapshot, FrameworkModelCallIntent,
    FrameworkModelCallRecord, FrameworkModelUsage, union_activity_intervals,
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


def test_root_activity_time_is_the_union_of_persisted_call_intervals():
    """Overlapping root/subagent calls must not double-count wall-clock time."""
    _binding, snapshot = original_budget()
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)
    snapshot.calls[0] = snapshot.calls[0].model_copy(update={
        "phase": "completed", "started_at": base,
        "settled_at": base + timedelta(seconds=10),
    })
    snapshot.calls[1] = snapshot.calls[1].model_copy(update={
        "phase": "completed", "started_at": base + timedelta(seconds=3),
        "settled_at": base + timedelta(seconds=8),
    })
    snapshot.calls[2] = snapshot.calls[2].model_copy(update={
        "phase": "unknown", "started_at": base + timedelta(seconds=20),
        "settled_at": base + timedelta(seconds=25),
    })
    snapshot.calls[3] = snapshot.calls[3].model_copy(update={
        "phase": "ready", "started_at": None, "settled_at": None,
    })

    assert snapshot.root_activity_intervals() == [
        (base, base + timedelta(seconds=10)),
        (base + timedelta(seconds=20), base + timedelta(seconds=25)),
    ]
    assert snapshot.root_activity_seconds() == 15
    from src.application.models.framework_model_budget_projection import (
        capture_model_budget_material,
        read_model_budget_material,
    )

    restored = read_model_budget_material(
        _binding,
        capture_model_budget_material(_binding, snapshot),
    )
    assert restored.root_activity_seconds() == 15


def test_root_activity_time_uses_explicit_observation_for_an_unclosed_call():
    _binding, snapshot = original_budget()
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)
    snapshot.calls[1] = snapshot.calls[1].model_copy(update={
        "phase": "invoking", "started_at": base + timedelta(seconds=4),
        "settled_at": None,
    })

    with pytest.raises(ValueError, match="as_of"):
        snapshot.root_activity_intervals()
    assert snapshot.root_activity_seconds(
        as_of=base + timedelta(seconds=9)
    ) == 5


def test_activity_union_keeps_approval_waiting_gap_out_of_root_time():
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)

    intervals = union_activity_intervals([
        (base, base + timedelta(seconds=10)),
        # The approval is waiting between these two execution attempts.
        (base + timedelta(seconds=60), base + timedelta(seconds=70)),
        # A child branch overlaps the resumed attempt.
        (base + timedelta(seconds=65), base + timedelta(seconds=75)),
    ])

    assert intervals == [
        (base, base + timedelta(seconds=10)),
        (base + timedelta(seconds=60), base + timedelta(seconds=75)),
    ]
    assert sum((end - start).total_seconds() for start, end in intervals) == 25


@pytest.mark.asyncio
async def test_postgres_activity_reader_joins_existing_attempt_timeline(monkeypatch):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    binding, snapshot = original_budget()
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)
    snapshot.calls[0] = snapshot.calls[0].model_copy(update={
        "phase": "completed", "started_at": base + timedelta(seconds=2),
        "settled_at": base + timedelta(seconds=4),
    })

    class Cursor:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=()):
            if "SELECT runtime_binding FROM" in statement:
                self.rows = [{"runtime_binding": binding.model_dump(mode="json")}]
            elif "SELECT policy FROM" in statement:
                self.rows = [{"policy": snapshot.policy.model_dump(mode="json")}]
            elif "SELECT attempt_id,record FROM" in statement:
                self.rows = [{"attempt_id": call.intent.attempt_id, "record": call.model_dump(mode="json")}
                    for call in snapshot.calls]
            elif "SELECT attempt.started_at" in statement:
                self.rows = [
                    {"started_at": base, "finished_at": base + timedelta(seconds=10)},
                    {"started_at": base + timedelta(seconds=60), "finished_at": base + timedelta(seconds=70)},
                ]
            else:
                raise AssertionError(f"unexpected SQL: {statement}")

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return list(self.rows)

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self.cursor_instance

    connection = Connection()
    monkeypatch.setattr(
        "src.runtime.postgres_framework_model_calls.postgres_connect",
        lambda settings: connection,
    )
    settings = SimpleNamespace(database=SimpleNamespace(
        postgres_agent_task_table="tasks",
        postgres_agent_attempt_table="attempts",
    ))
    store = PostgresFrameworkModelCalls(settings)

    intervals = await store.root_activity_intervals(binding)

    assert intervals == [
        (base, base + timedelta(seconds=10)),
        (base + timedelta(seconds=60), base + timedelta(seconds=70)),
    ]

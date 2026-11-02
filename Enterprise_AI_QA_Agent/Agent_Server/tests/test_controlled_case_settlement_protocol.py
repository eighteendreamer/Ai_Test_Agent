"""Strict, additive settlement formats do not enable graph execution."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.controlled_case_settlement_recovery import ControlledCaseSettlementAdmission, settlement_recovery_task_id
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_v2_worker_contract import binding


def proof():
    values = dict(schema_version=1, scope="controlled_case_settlement_admission_only",
        task_id=settlement_recovery_task_id("origin-task", "origin-attempt"),
        origin_task_id="origin-task", origin_attempt_id="origin-attempt", origin_owner_epoch=1,
        binding_fingerprint="a"*64, run_item_execution_attempt_id="original-run-attempt",
        input_sha256="b"*64, application_commit_sha256="c"*64, assessment_sha256="d"*64,
        origin_task_sha256="e"*64, origin_attempt_sha256="f"*64, origin_owner_sha256="1"*64,
        run_item_sha256="2"*64, run_item_attempt_sha256="3"*64,
        admitted_at=datetime(2026, 11, 2, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"))
    return {**values, "asset_sha256": approval_value_hash(values)}


def command():
    raw = proof()
    turn = str(uuid5(NAMESPACE_URL, "case-run:run:item:original-run-attempt"))
    return AgentTaskCommandV2(operation="case_settle", task_id=raw["task_id"], parent_task_id=raw["origin_task_id"],
        session_id="session-1", turn_id=turn, project_id="project", request_id="request", trace_id="trace",
        run_id="run", run_item_id="item", reference_id="fixed-version",
        runtime_binding=binding(turn_id=turn, runtime_kind="langgraph"),
        case_settlement_reference={"origin_task_id": raw["origin_task_id"], "origin_attempt_id": raw["origin_attempt_id"],
            "run_item_execution_attempt_id": raw["run_item_execution_attempt_id"], "admission_sha256": raw["asset_sha256"]})


def test_settlement_admission_round_trip_is_not_execution_or_a_public_task_input():
    caps = FrameworkWorkerCapabilities("a"*64, False)
    value = caps.require_case_settlement_format(proof())
    assert value == ControlledCaseSettlementAdmission.model_validate(value.model_dump(mode="json"))
    original = command()
    wire = original.model_dump(mode="json")
    assert caps.require_command_format(wire) == original
    assert "case_execution_input" not in wire and "case_execution_reference" not in wire
    assert wire["case_settlement_reference"]["admission_sha256"] == value.asset_sha256
    with pytest.raises(TaskDeferred):
        caps.require(command().runtime_binding, operation="case_settle")


@pytest.mark.parametrize("changed", ["future_field", "future_version", "future_scope", "bool_version", "old_reader"])
def test_unsupported_settlement_format_defers_without_downgrade(changed):
    raw, caps = proof(), FrameworkWorkerCapabilities("a"*64, False)
    if changed == "future_field":
        raw["future"] = True
    elif changed == "future_version":
        raw["schema_version"] = 99
    elif changed == "future_scope":
        raw["scope"] = "future_settlement_scope"
    elif changed == "bool_version":
        raw["schema_version"] = True
    else:
        caps = replace(caps, controlled_case_settlement_max_version=0)
    raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
    with pytest.raises(TaskDeferred, match="framework_case_settlement_format_unavailable"):
        caps.require_case_settlement_format(raw)


@pytest.mark.parametrize("changed", ["task_id", "digest", "time"])
def test_supported_but_invalid_lineage_is_not_a_compatibility_deferral(changed):
    raw = proof()
    if changed == "task_id":
        raw["task_id"] = "foreign-task"
    elif changed == "time":
        raw["admitted_at"] = "2026-11-02T00:00:00"
    else:
        raw["assessment_sha256"] = "f"*64
    if changed != "digest":
        raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
    with pytest.raises(ValidationError):
        FrameworkWorkerCapabilities("a"*64, False).require_case_settlement_format(raw)


@pytest.mark.parametrize("changed", ["parent", "task", "turn", "payload", "worker", "attempt", "resource", "type", "runtime", "missing_reference", "other_operation"])
def test_settlement_wire_accepts_only_its_scoped_identity(changed):
    raw = command().model_dump(mode="json")
    changes = {"parent": {"parent_task_id": "foreign"}, "task": {"task_id": "foreign"},
        "turn": {"turn_id": "foreign"}, "payload": {"payload": {"execute": True}},
        "worker": {"worker_id": "foreign"}, "attempt": {"attempt_id": "foreign"},
        "resource": {"resource_id": "foreign"}, "type": {"task_type": "coordinator_child"},
        "missing_reference": {"case_settlement_reference": None}, "other_operation": {"operation": "message"}}
    if changed == "runtime":
        raw["runtime_binding"] = binding(turn_id=raw["turn_id"]).model_dump(mode="json")
    else:
        raw.update(changes[changed])
    with pytest.raises(ValidationError):
        AgentTaskCommandV2.model_validate(raw)


@pytest.mark.parametrize("changed", ["field", "version", "operation"])
def test_unknown_settlement_command_format_is_deferred_before_claim(changed):
    raw = deepcopy(command().model_dump(mode="json"))
    if changed == "field":
        raw["case_settlement_reference"]["future_field"] = True
    elif changed == "version":
        raw["case_settlement_reference"]["schema_version"] = 99
    else:
        raw["operation"] = "case_settle_future"
    with pytest.raises(TaskDeferred, match="framework_command_format_unavailable"):
        FrameworkWorkerCapabilities.require_command_format(raw)


def recovery_receipt():
    values = dict(schema_version=1, scope="controlled_case_recovery_settlement_only",
        task_id=proof()["task_id"], attempt_id="settlement-attempt", origin_task_id="origin-task",
        origin_attempt_id="origin-attempt", origin_owner_epoch=1, binding_fingerprint="a"*64,
        admission_sha256="b"*64, assessment_sha256="c"*64, result_id="original-result",
        settled_at="2026-11-02T00:00:00Z")
    values.update({name: "d"*64 for name in ("result_sha256", "item_sha256", "attempt_sha256",
        "origin_task_sha256", "origin_attempt_sha256", "origin_owner_sha256", "settlement_task_sha256",
        "settlement_attempt_sha256", "task_event_sha256")})
    return {**values, "receipt_sha256": approval_value_hash(values)}


@pytest.mark.parametrize("change", ["future_field", "future_scope", "future_version", "bool_version", "old_reader"])
def test_recovery_receipt_reader_is_strict_and_not_an_execution_grant(change):
    raw, caps = recovery_receipt(), FrameworkWorkerCapabilities("a"*64, False)
    assert caps.require_case_recovery_receipt_format(raw).model_dump(mode="json") == raw
    if change == "future_field":
        raw["future"] = True
    elif change == "future_scope":
        raw["scope"] = "future_scope"
    elif change == "future_version":
        raw["schema_version"] = 99
    elif change == "bool_version":
        raw["schema_version"] = True
    else:
        caps = replace(caps, controlled_case_recovery_receipt_max_version=0)
    raw["receipt_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "receipt_sha256"})
    with pytest.raises(TaskDeferred, match="framework_case_recovery_receipt_format_unavailable"):
        caps.require_case_recovery_receipt_format(raw)


@pytest.mark.parametrize("change", ["task", "time", "digest", "owner_epoch"])
def test_invalid_recovery_receipt_is_not_repaired_or_downgraded(change):
    raw = recovery_receipt()
    if change == "task":
        raw["task_id"] = "foreign"
    elif change == "time":
        raw["settled_at"] = "2026-11-02T00:00:00"
    elif change == "owner_epoch":
        raw["origin_owner_epoch"] = True
    else:
        raw["result_sha256"] = "e"*64
    if change != "digest":
        raw["receipt_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "receipt_sha256"})
    with pytest.raises(ValidationError):
        FrameworkWorkerCapabilities("a"*64, False).require_case_recovery_receipt_format(raw)


def test_local_scoped_lease_never_serializes_secret_or_becomes_graph_execution_owner():
    from src.schemas.controlled_case_settlement_recovery import ControlledCaseSettlementLease
    from src.schemas.runtime_binding import RuntimeExecutionOwner

    wire = command()
    lease = ControlledCaseSettlementLease(binding=wire.runtime_binding, task_id=wire.task_id,
        attempt_id="settlement-attempt", attempt_no=1, worker_id="settlement-worker",
        lease_token="synthetic-local-lease", reference=wire.case_settlement_reference)
    assert "synthetic-local-lease" not in repr(lease) + lease.model_dump_json()
    assert not hasattr(lease, "owner_epoch") and not hasattr(lease, "controlled_case_lease")
    with pytest.raises(ValidationError):
        RuntimeExecutionOwner.model_validate(lease.model_dump())
    with pytest.raises(TaskDeferred):
        replace(FrameworkWorkerCapabilities("a"*64, False), controlled_case_settlement_available=True).require(
            wire.runtime_binding, operation="case_settle")

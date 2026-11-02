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

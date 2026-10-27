"""Lossless root ledger material; provenance and execution rights are external.

No upstream counterpart (checked: FrameworkModelBudgetSnapshot, LangGraph
1.2.12 UntrackedValue, v4 §6.3/§9.2): graph invocation counters do not store
the application's root reservations or original settlement facts. This codec
does not reserve, settle, reconcile, invoke or authorize recovery. Callers must
verify the original result/event source before consuming its material.
"""
from __future__ import annotations

from typing import Any

from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_model_call import FrameworkModelBudgetSnapshot
from src.schemas.runtime_binding import RuntimeBinding


def _snapshot_wire(snapshot: FrameworkModelBudgetSnapshot) -> dict[str, Any]:
    # Computed totals are projections, not input fields (extra='forbid'). Keep
    # the original call serializer, including its v1/v2 settlement omission.
    return snapshot.model_dump(mode="json", include=set(FrameworkModelBudgetSnapshot.model_fields))


def capture_model_budget_material(binding: RuntimeBinding, snapshot: FrameworkModelBudgetSnapshot) -> dict[str, Any]:
    payload = {"schema_version": 1, "scope": "root_model_budget_material_only",
        "binding_id": binding.binding_id, "binding_fingerprint": binding.fingerprint,
        "snapshot": _snapshot_wire(snapshot)}
    material = {**payload, "sha256": approval_value_hash(payload)}
    read_model_budget_material(binding, material)
    return material


def read_model_budget_material(binding: RuntimeBinding, value: Any) -> FrameworkModelBudgetSnapshot:
    """Decode already sourced original facts; a digest is not their authority.

    Missing historical material stays missing. Unknown calls retain their
    reservation and missing usage stays None, never a made-up zero. Returning
    a validated copy cannot mutate the original event or live ledger.
    """
    if (not isinstance(value, dict) or set(value) != {"schema_version", "scope", "binding_id",
            "binding_fingerprint", "snapshot", "sha256"}
        or type(value["schema_version"]) is not int or value["schema_version"] != 1
        or value["scope"] != "root_model_budget_material_only"
        or value["binding_id"] != binding.binding_id or value["binding_fingerprint"] != binding.fingerprint):
        raise ValueError("Original root model budget material is missing, unsupported or belongs to another binding")
    payload = {key: item for key, item in value.items() if key != "sha256"}
    if value["sha256"] != approval_value_hash(payload):
        raise ValueError("Original root model budget material digest changed")
    snapshot = FrameworkModelBudgetSnapshot.model_validate(value["snapshot"])
    if (approval_value_hash(_snapshot_wire(snapshot)) != approval_value_hash(value["snapshot"])
        or binding.model_config_fingerprint not in snapshot.policy.model_config_fingerprints.values()
        or any(call.binding_id != binding.binding_id for call in snapshot.calls)
        or len({call.intent.attempt_id for call in snapshot.calls}) != len(snapshot.calls)):
        raise ValueError("Original root model budget material differs from its exact ledger fields or binding")
    return snapshot.model_copy(deep=True)

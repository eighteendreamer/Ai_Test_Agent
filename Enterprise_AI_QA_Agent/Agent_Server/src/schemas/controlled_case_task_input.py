"""Reader-first formal case input, not a RunItem lease or execution permit.

No upstream counterpart (checked: AgentCaseExecutionContext, RunPlanSnapshotIdentity,
ControlledCaseGraphInputAsset, v4 §9/§12): framework checkpoints do not own formal
case versions or the RunItem's original execution attempt. Keep that identity
separate from the AgentTask attempt created by its own claim.
"""
from __future__ import annotations

import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.controlled_case_graph_input import ControlledCaseGraphInputAsset, parse_controlled_graph_input
from src.schemas.framework_approval import approval_value_hash
from src.schemas.run_management import RunPlanSnapshotIdentity


class ControlledCaseTaskInputReference(BaseModel):
    """Delivery identity only; the full original remains in PostgreSQL."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_item_execution_attempt_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlledCaseTaskInputAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    context: AgentCaseExecutionContext
    run_item_execution_attempt_id: str = Field(min_length=1)
    plan_snapshot_identity: RunPlanSnapshotIdentity
    graph_input: ControlledCaseGraphInputAsset
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled case input requires its explicit integer version")
        return value

    @field_validator("graph_input", mode="before")
    @classmethod
    def read_graph_input(cls, value):
        return parse_controlled_graph_input(value)

    @model_validator(mode="after")
    def verify_original(self):
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Controlled case input digest differs from its frozen contents")
        context = self.context
        if (not all((context.project_id, context.run_id, context.run_item_id, context.plan_id, context.target_key))
            or context.plan_id != self.plan_snapshot_identity.plan_id):
            raise ValueError("Controlled case input differs from its claimed plan identity")
        expected_turn = str(uuid5(NAMESPACE_URL,
            f"case-run:{context.run_id}:{context.run_item_id}:{self.run_item_execution_attempt_id}"))
        if self.graph_input.binding.turn_id != expected_turn:
            raise ValueError("Controlled case input differs from its original RunItem execution turn")
        targets = [step for step in context.steps if step.item.key == context.target_key]
        if (len(targets) != 1 or len({step.item.key for step in context.steps}) != len(context.steps)
            or targets[0].phase != "case" or targets[0].item.kind == "prepare"
            or not targets[0].item.case_id or not targets[0].item.case_version_id or targets[0].content_json is None):
            raise ValueError("Controlled case input requires its unique complete formal target version")
        return self

    @property
    def case_version_id(self) -> str:
        return next(step.item.case_version_id for step in self.context.steps if step.item.key == self.context.target_key)


def parse_controlled_case_task_input(value) -> ControlledCaseTaskInputAsset:
    # Frozen models retain mutable nested context. Never trust a prebuilt
    # object or retain caller JSON aliases across a later awaited boundary.
    if isinstance(value, ControlledCaseTaskInputAsset):
        value = value.model_dump(mode="json")
    try:
        value = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Controlled case input contains non-persistable execution data") from exc
    return ControlledCaseTaskInputAsset.model_validate(value)

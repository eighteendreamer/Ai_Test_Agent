from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from src.schemas.case_planning import (
    CasePlanItem, PlanningEvidence, PlanningGap, PlanningModel, PlanInputBinding,
)


class BoundCaseInput(PlanningModel):
    binding: PlanInputBinding
    resolved: bool
    value: Any = None
    run_item_id: str | None = None
    result_id: str | None = None
    actual_path: list[str] | None = None


class CaseExecutionStep(PlanningModel):
    item: CasePlanItem
    phase: Literal["before", "case", "after"]
    inputs: dict[str, BoundCaseInput] = Field(default_factory=dict)
    content_json: dict[str, Any] | None = None
    execution_notes: str = ""
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class AgentCaseExecutionContext(PlanningModel):
    schema_version: Literal[1] = 1
    project_id: str
    run_id: str
    run_item_id: str
    plan_id: str
    objective: str
    target_key: str
    steps: list[CaseExecutionStep] = Field(min_length=1)
    runtime_inputs: dict[str, Any] = Field(default_factory=dict)
    evidence: list[PlanningEvidence] = Field(default_factory=list)
    unresolved: list[PlanningGap] = Field(default_factory=list)

    def runtime_context(self) -> dict[str, Any]:
        """Use the existing graph/TurnState/snapshot context, without copies per layer."""
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "run_item_id": self.run_item_id,
            "execution_basis": "cases",
            "case_execution": self.model_dump(mode="json"),
        }

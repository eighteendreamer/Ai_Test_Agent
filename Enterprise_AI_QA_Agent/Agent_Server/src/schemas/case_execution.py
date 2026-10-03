from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

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


class AgentCaseExecutionCheckpoint(PlanningModel):
    schema_version: Literal[1] = 1
    project_id: str
    run_id: str
    run_item_id: str
    plan_id: str
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_attempt_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    phase: Literal["admitted", "running", "snapshot"]
    snapshot_id: str | None = None
    snapshot_version: int | None = Field(default=None, ge=1)


class CaseToolEvidenceReference(PlanningModel):
    call_id: str = Field(min_length=1, max_length=256)
    output_path_tokens: list[str] = Field(default_factory=list, max_length=32)


class CaseExecutionCheck(PlanningModel):
    name: str = Field(min_length=1, max_length=240)
    expected_path_tokens: list[str] = Field(max_length=32)
    observations: list[CaseToolEvidenceReference] = Field(min_length=1, max_length=20)
    operator: Literal["equals", "not_equals", "contains", "not_contains", "lt", "lte", "gt", "gte", "semantic"]
    verdict: Literal["passed", "failed", "not_run"] | None = None
    reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_method(self):
        if self.operator == "semantic" and (self.verdict is None or not self.reason.strip()):
            raise ValueError("语义检查必须声明判定和依据说明")
        if self.operator != "semantic" and len(self.observations) != 1:
            raise ValueError("确定性比较需要且只能引用一个实际值")
        return self


class CaseStepReceipt(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    status: Literal["passed", "failed", "error", "blocked", "skipped"]
    summary: str = Field(min_length=1, max_length=4000)
    checks: list[CaseExecutionCheck] = Field(default_factory=list, max_length=200)
    outputs: dict[str, CaseToolEvidenceReference] = Field(default_factory=dict)
    output_source: CaseToolEvidenceReference | None = None

    @model_validator(mode="after")
    def one_output_shape(self):
        if self.output_source is not None and self.outputs:
            raise ValueError("输出不能同时声明完整对象来源和字段映射")
        return self


class AgentCaseExecutionReceipt(PlanningModel):
    schema_version: Literal[1] = 1
    steps: list[CaseStepReceipt] = Field(min_length=1, max_length=200)

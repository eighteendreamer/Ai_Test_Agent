"""Versioned intermediate decisions; only the merged plan is executable."""
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from src.schemas.case_planning import (
    FormalCasePlanItem, PlanningDecision, PlanningGap, PlanningModel,
    PlanningQuery, PlanInputBinding, PreparationPlanItem,
)
from src.schemas.case_effects import EffectEvidenceDeclaration

STAGE_PROTOCOL = "case-planning-staged-evidence-v1"
STAGED_PROMPT_VERSION = "case-planning-staged-evidence-v4"


class BusinessPlanningQuery(PlanningQuery):
    kind: Literal["cases", "documents", "document", "recordings", "recording"]
    operation: None = None


class LogicalFormalCaseItem(FormalCasePlanItem):
    effect_evidence: None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=0)


class LogicalPreparationItem(PreparationPlanItem):
    effect_evidence: None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=0)


class LogicalPlanningDecision(PlanningDecision):
    queries: list[BusinessPlanningQuery] = Field(default_factory=list, max_length=8)
    items: list[Annotated[LogicalFormalCaseItem | LogicalPreparationItem, Field(discriminator="kind")]] = Field(default_factory=list, max_length=200)


class EvidenceItemBinding(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    evidence_ids: list[str] = Field(min_length=1, max_length=50)
    input_bindings: dict[str, PlanInputBinding] = Field(default_factory=dict)
    effect_evidence: EffectEvidenceDeclaration | None = None


class EvidencePlanningDecision(PlanningModel):
    action: Literal["query", "finish"]
    summary: str = Field(min_length=1, max_length=4000)
    queries: list[PlanningQuery] = Field(default_factory=list, max_length=8)
    bindings: list[EvidenceItemBinding] = Field(default_factory=list, max_length=200)
    unresolved: list[PlanningGap] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_action(self):
        if self.action == "query" and not self.queries:
            raise ValueError("证据查询需要指定来源")
        if self.action == "finish" and self.queries:
            raise ValueError("最终证据声明不能携带未完成查询")
        return self


def logical_decision_hash(decision):
    raw = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class PlanningStagePrompts(PlanningModel):
    protocol: Literal["case-planning-staged-evidence-v1"] = STAGE_PROTOCOL
    logic: str
    effects: str


class PlanningStageCheckpoint(PlanningModel):
    stage: Literal["logic", "effects"] = "logic"
    logical_decision: PlanningDecision | None = None
    logical_hash: str | None = None

    @model_validator(mode="after")
    def fixed_logic(self):
        if self.stage == "logic":
            if self.logical_decision is not None or self.logical_hash is not None:
                raise ValueError("逻辑阶段尚不能保存已冻结决定")
        elif self.logical_decision is None or self.logical_hash != logical_decision_hash(self.logical_decision):
            raise ValueError("证据阶段需要一致的固定逻辑决定及哈希")
        return self

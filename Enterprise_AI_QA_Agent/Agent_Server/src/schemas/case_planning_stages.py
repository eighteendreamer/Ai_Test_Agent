"""Versioned intermediate decisions; only the merged plan is executable."""
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import Field, model_serializer, model_validator

from src.schemas.case_planning import (
    FormalCasePlanItem, PlanningDecision, PlanningGap, PlanningModel,
    PlanningQuery, PlanInputBinding, PreparationPlanItem, FormalCasePlanItemV2,
    PreparationPlanItemV2, PlanningDecisionV2,
    FormalCasePlanItemV3, PreparationPlanItemV3, PlanningDecisionV3,
)
from src.schemas.case_effects import EffectEvidenceDeclaration, EffectRelationDeclaration
from src.schemas.case_effect_sources import SourceEffectRelationDeclaration

STAGE_PROTOCOL = "case-planning-staged-evidence-v1"
STAGED_PROMPT_VERSION = "case-planning-staged-evidence-v4"
RELATION_STAGE_PROTOCOL = "case-planning-staged-evidence-v2"
RELATION_PROMPT_VERSION = "case-planning-staged-evidence-v5"


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


class LogicalFormalCaseItemV2(FormalCasePlanItemV2):
    effect_declarations: None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=0)


class LogicalPreparationItemV2(PreparationPlanItemV2):
    effect_declarations: None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=0)


class LogicalPlanningDecisionV2(PlanningDecisionV2):
    queries: list[BusinessPlanningQuery] = Field(default_factory=list, max_length=8)
    items: list[Annotated[LogicalFormalCaseItemV2 | LogicalPreparationItemV2, Field(discriminator="kind")]] = Field(default_factory=list, max_length=200)


class LogicalFormalCaseItemV3(FormalCasePlanItemV3):
    effect_declarations: None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=0)


class LogicalPreparationItemV3(PreparationPlanItemV3):
    effect_declarations: None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=0)


class LogicalPlanningDecisionV3(PlanningDecisionV3):
    queries: list[BusinessPlanningQuery] = Field(default_factory=list, max_length=8)
    items: list[Annotated[LogicalFormalCaseItemV3 | LogicalPreparationItemV3, Field(discriminator="kind")]] = Field(default_factory=list, max_length=200)


class PlanningOperationSelection(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    tool_key: str = Field(min_length=1, max_length=160)
    operation: str | None = Field(default=None, min_length=1, max_length=160)
    evidence_ids: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, max_length=50)


class LogicalPlanningDecisionV4(LogicalPlanningDecisionV3):
    operation_selections: list[PlanningOperationSelection] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def selections_after_logic(self):
        if self.action != "finish" and self.operation_selections:
            raise ValueError("操作选择只能随已完成的逻辑计划保存")
        return self


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


class EvidenceItemBindingV2(EvidenceItemBinding):
    effect_evidence: None = None
    effect_declarations: list[EffectRelationDeclaration] | None = Field(default=None, min_length=1, max_length=200)


class EvidencePlanningDecisionV2(EvidencePlanningDecision):
    bindings: list[EvidenceItemBindingV2] = Field(default_factory=list, max_length=200)


class EvidenceItemBindingV3(EvidenceItemBindingV2):
    effect_declarations: list[SourceEffectRelationDeclaration] | None = Field(default=None, min_length=1, max_length=200)


class EvidencePlanningDecisionV3(EvidencePlanningDecisionV2):
    bindings: list[EvidenceItemBindingV3] = Field(default_factory=list, max_length=200)


def logical_decision_hash(decision, operation_selections=None):
    value = decision.model_dump(mode="json")
    if operation_selections is not None:
        value = {"decision": value, "operation_selections": [entry.model_dump(mode="json") for entry in operation_selections]}
    raw = json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class PlanningStagePrompts(PlanningModel):
    protocol: Literal["case-planning-staged-evidence-v1", "case-planning-staged-evidence-v2"] = STAGE_PROTOCOL
    logic: str
    effects: str


class PlanningStageCheckpoint(PlanningModel):
    stage: Literal["logic", "effects"] = "logic"
    logical_decision: PlanningDecision | PlanningDecisionV2 | PlanningDecisionV3 | None = None
    logical_hash: str | None = None
    operation_selections: list[PlanningOperationSelection] | None = Field(default=None, max_length=200)

    @model_serializer(mode="wrap")
    def preserve_original_stage(self, handler):
        data = handler(self)
        if self.operation_selections is None:
            data.pop("operation_selections", None)
        return data

    @model_validator(mode="after")
    def fixed_logic(self):
        if self.stage == "logic":
            if self.logical_decision is not None or self.logical_hash is not None or self.operation_selections is not None:
                raise ValueError("逻辑阶段尚不能保存已冻结决定")
        elif self.logical_decision is None or self.logical_hash != logical_decision_hash(self.logical_decision, self.operation_selections):
            raise ValueError("证据阶段需要一致的固定逻辑决定及哈希")
        return self

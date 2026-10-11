"""Frozen evidence-task receipts; canonical execution contracts stay unchanged."""
from typing import Literal

from pydantic import Field, model_validator
from src.schemas.case_planning import PlanningModel, PlanningGap, PlanInputBinding
from src.schemas.case_effect_sources import SourceEffectRelationDeclaration
from src.schemas.case_planning_references import ReferencedEffectDeclaration
from src.schemas.case_planning_scenes import ScenePlanningQuery

EFFECT_TASK_PROTOCOL = "case-planning-staged-evidence-v3"
TASKS = ("bindings", "produced", "cleanup")


class TaskBinding(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    evidence_ids: list[str] = Field(min_length=1, max_length=50)
    input_bindings: dict[str, PlanInputBinding] = Field(default_factory=dict)
    declaration_roles: list[Literal["produced", "cleanup"]] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def distinct_roles(self):
        if len(set(self.declaration_roles)) != len(self.declaration_roles):
            raise ValueError("声明任务角色不能重复")
        return self


class TaskDeclarations(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    evidence_ids: list[str] = Field(min_length=1, max_length=50)
    effect_declarations: list[ReferencedEffectDeclaration] = Field(min_length=1, max_length=200)


class EffectTaskDecision(PlanningModel):
    action: Literal["query", "finish"]
    summary: str = Field(min_length=1, max_length=4000)
    queries: list[ScenePlanningQuery] = Field(default_factory=list, max_length=8)
    unresolved: list[PlanningGap] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def query_or_receipt(self):
        if (self.action == "query") != bool(self.queries):
            raise ValueError("声明任务查询和完成状态必须对应")
        if self.action == "query" and self.bindings:
            raise ValueError("查询响应尚不能提交声明任务回执")
        return self


class BindingTaskDecision(EffectTaskDecision):
    task: Literal["bindings"]
    bindings: list[TaskBinding] = Field(default_factory=list, max_length=200)


class ProducedTaskDecision(EffectTaskDecision):
    task: Literal["produced"]
    bindings: list[TaskDeclarations] = Field(default_factory=list, max_length=200)


class CleanupTaskDecision(EffectTaskDecision):
    task: Literal["cleanup"]
    bindings: list[TaskDeclarations] = Field(default_factory=list, max_length=200)


class CanonicalTaskBinding(PlanningModel):
    item_key: str
    evidence_ids: list[str]
    input_bindings: dict[str, PlanInputBinding] = Field(default_factory=dict)
    effect_declarations: list[SourceEffectRelationDeclaration] | None = None


class EffectTaskReceipt(PlanningModel):
    task: Literal["bindings", "produced", "cleanup"]
    summary: str
    bindings: list[CanonicalTaskBinding]
    declaration_roles: dict[str, list[Literal["produced", "cleanup"]]] = Field(default_factory=dict)
    unresolved: list[PlanningGap] = Field(default_factory=list)


class EffectTaskCheckpoint(PlanningModel):
    completed: list[EffectTaskReceipt] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def receipt_order(self):
        names = [r.task for r in self.completed]
        if names != list(TASKS[:len(names)]):
            raise ValueError("声明任务回执不能重复或越过原顺序")
        return self

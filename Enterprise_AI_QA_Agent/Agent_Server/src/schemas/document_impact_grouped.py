"""Versioned group requests/results preserve evidence separately from inference."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.schemas.document_impact_anchored_responses import DocumentImpactAnchoredDecision
from src.schemas.document_impact_batches import ImpactBatchModel
from src.schemas.document_impact_reasoning import DocumentImpactDiscoveryTask, DocumentImpactReasoningCheckpoint
from src.schemas.document_impact_responses import DocumentImpactEvidenceOutcome
from src.schemas.document_impact_workflow import (
    DocumentImpactCoordinationContext, DocumentImpactUnitResult,
    DocumentImpactWorkflowProtocol, DocumentImpactWorkflowResult,
)


class DocumentImpactGroupedProtocol(DocumentImpactWorkflowProtocol):
    version: Literal["impact-coordinator-v2"] = "impact-coordinator-v2"


class DocumentImpactDiscoveryResult(DocumentImpactUnitResult):
    task_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_outcomes: list[DocumentImpactEvidenceOutcome] = Field(default_factory=list)


class DocumentImpactGroupedContext(DocumentImpactCoordinationContext):
    protocol: DocumentImpactGroupedProtocol
    reasoning_checkpoint: DocumentImpactReasoningCheckpoint
    discovery_task: DocumentImpactDiscoveryTask | None = None
    discovery_results: list[DocumentImpactDiscoveryResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage(self):
        if (self.stage == "discover_rules") != (self.discovery_task is not None):
            raise ValueError("分组发现必须绑定固定任务，用例阶段不能更换为发现组")
        if self.discovery_units or self.stage == "discover_rules" and (self.rules or self.discovery_results):
            raise ValueError("分组阶段不能复制旧单元答案或提前注入后续判断")
        return self


def impact_coordination_context(value):
    model = DocumentImpactGroupedContext if value.get("protocol", {}).get("version") == "impact-coordinator-v2" else (
        DocumentImpactCoordinationContext)
    return model.model_validate(value)


class GroupedEvidenceOutcome(ImpactBatchModel):
    member_ref: Annotated[str, Field(pattern=r"^[PD][1-9][0-9]*$")]
    outcome: Literal["analyzed", "evidence_insufficient", "unprocessed"]
    reason: str = Field(min_length=1)


class DocumentImpactGroupedWireDecision(DocumentImpactAnchoredDecision):
    protocol_version: Literal["impact-response-v4"]
    unit_outcome: Literal["analyzed", "evidence_insufficient"] | None = Field(default=None,
        description="Required non-null for finish: analyzed or evidence_insufficient. Null for read only.")
    evidence_outcomes: list[GroupedEvidenceOutcome] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_action_contract(self):
        has_reads = bool(self.reads.segment_refs or self.reads.case_refs or
            self.reads.difference_refs or self.reads.ranges)
        if self.action == "read" and (not has_reads or self.unit_outcome is not None or
            self.candidates or self.evidence_outcomes):
            raise ValueError("补读需要范围，不能声明结束、候选或证据处理结果")
        if self.action == "finish" and (has_reads or self.unit_outcome is None):
            raise ValueError("结束需要明确unit_outcome，不能携带未完成补读")
        return self


class DocumentImpactEvidenceResult(DocumentImpactUnitResult):
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def no_rule_copies(self):
        if self.unit.kind == "case" or self.candidates:
            raise ValueError("证据覆盖不能作为用例或复制组级候选")
        return self


class DocumentImpactGroupedResult(DocumentImpactWorkflowResult):
    implementation_version: Literal["impact-coordinator-v2"] = "impact-coordinator-v2"
    protocol: DocumentImpactGroupedProtocol
    reasoning_checkpoint: DocumentImpactReasoningCheckpoint
    discovery_tasks: list[DocumentImpactDiscoveryResult]
    units: list[DocumentImpactEvidenceResult | DocumentImpactUnitResult]


ImpactWorkflowProtocol = Annotated[
    DocumentImpactWorkflowProtocol | DocumentImpactGroupedProtocol, Field(discriminator="version")]
ImpactWorkflowResult = Annotated[
    DocumentImpactWorkflowResult | DocumentImpactGroupedResult, Field(discriminator="implementation_version")]

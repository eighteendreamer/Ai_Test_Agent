from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.document_impact_batches import ImpactBatchModel
from src.schemas.document_impact_calls import DocumentImpactUnit
from src.schemas.document_impact_responses import DocumentImpactResponseCheckpoint, DocumentImpactRuleCandidate


WORKFLOW_VERSION = "impact-coordinator-v1"


class DocumentImpactWorkflowProtocol(ImpactBatchModel):
    version: Literal["impact-coordinator-v1"] = WORKFLOW_VERSION
    discovery_instruction: str = Field(min_length=1)
    case_instruction: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self):
        raw = json.dumps(self.model_dump(mode="json", exclude={"sha256"}), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("影响工作流协议与固定哈希不同")
        return self


class DocumentImpactRuleOrigin(ImpactBatchModel):
    checkpoint: DocumentImpactResponseCheckpoint
    local_key: str


class DocumentImpactDiscoveredRule(ImpactBatchModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate: DocumentImpactRuleCandidate
    origins: list[DocumentImpactRuleOrigin] = Field(min_length=1)


class DocumentImpactUnitResult(ImpactBatchModel):
    unit: DocumentImpactUnit
    status: Literal["analyzed", "evidence_insufficient", "rejected", "ineligible", "unknown", "unprocessed", "incomplete"]
    calls: list[str] = Field(default_factory=list)
    final_check: DocumentImpactResponseCheckpoint | None = None
    reason_codes: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    candidates: list[DocumentImpactRuleCandidate] = Field(default_factory=list)


class DocumentImpactCoordinationContext(ImpactBatchModel):
    protocol: DocumentImpactWorkflowProtocol
    stage: Literal["discover_rules", "case_impacts"]
    rules: list[DocumentImpactDiscoveredRule] = Field(default_factory=list)
    discovery_units: list[DocumentImpactUnitResult] = Field(default_factory=list)


class DocumentImpactOverlap(ImpactBatchModel):
    rule_ids: list[str] = Field(min_length=2)
    status: Literal["possible_overlap_or_conflict"] = "possible_overlap_or_conflict"


class DocumentImpactWorkflowResult(ImpactBatchModel):
    implementation_version: Literal["impact-coordinator-v1"] = WORKFLOW_VERSION
    task_id: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol: DocumentImpactWorkflowProtocol
    units: list[DocumentImpactUnitResult]
    rules: list[DocumentImpactDiscoveredRule]
    overlaps: list[DocumentImpactOverlap]
    basis_limitations: list[str]
    status: Literal["complete", "partial"]
    semantic_status: Literal["pending_review"] = "pending_review"
    deployment_status: Literal["unknown"] = "unknown"

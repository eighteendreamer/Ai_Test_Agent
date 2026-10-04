from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from src.schemas.document_impact_batches import (
    ImpactBatchModel, DocumentImpactCaseCitation, DocumentImpactTextCitation,
)
from src.schemas.document_impact_calls import DocumentImpactCallObject, DocumentImpactRangeRead, DocumentImpactUnit


VALIDATOR_VERSION = "impact-response-validator-v1"


class DocumentImpactReadRequest(ImpactBatchModel):
    segment_ids: list[str] = Field(default_factory=list, max_length=100)
    case_version_ids: list[str] = Field(default_factory=list, max_length=100)
    difference_ids: list[str] = Field(default_factory=list, max_length=100)
    ranges: list[DocumentImpactRangeRead] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_identity(self):
        for items in (self.segment_ids, self.case_version_ids, self.difference_ids):
            if len(items) != len(set(items)):
                raise ValueError("影响补读身份不能重复")
        keys = [(item.source_id, item.start_char, item.end_char) for item in self.ranges]
        if len(keys) != len(set(keys)):
            raise ValueError("影响补读区间不能重复")
        return self

    @property
    def has_reads(self):
        return bool(self.segment_ids or self.case_version_ids or self.difference_ids or self.ranges)


class DocumentImpactCaseCandidate(ImpactBatchModel):
    version_id: str = Field(min_length=1)
    level: Literal["none", "minor", "major", "uncertain"]
    reason: str = Field(min_length=1)
    verification_points: list[str] = Field(default_factory=list)
    citations: list[DocumentImpactCaseCitation] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_versions(self):
        if any(citation.version_id != self.version_id for citation in self.citations):
            raise ValueError("用例影响只能引用该固定版本")
        return self


class DocumentImpactRuleCandidate(ImpactBatchModel):
    # A local proposal key is not a stable requirement identity or an approved mapping.
    key: str = Field(min_length=1)
    kind: Literal["added", "modified", "removed", "uncertain"]
    summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    document_citations: list[DocumentImpactTextCitation] = Field(min_length=1)
    case_impacts: list[DocumentImpactCaseCandidate] = Field(default_factory=list)
    applicability_suggestion: Literal["unknown", "current", "future"] = "unknown"
    uncertainties: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cases(self):
        if len({item.version_id for item in self.case_impacts}) != len(self.case_impacts):
            raise ValueError("同一规则候选的用例影响版本不能重复")
        return self


class DocumentImpactDecision(ImpactBatchModel):
    protocol_version: Literal["impact-response-v1"]
    unit: DocumentImpactUnit
    action: Literal["read", "finish"]
    summary: str = Field(min_length=1)
    reads: DocumentImpactReadRequest = Field(default_factory=DocumentImpactReadRequest)
    unit_outcome: Literal["analyzed", "evidence_insufficient"] | None = None
    candidates: list[DocumentImpactRuleCandidate] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "read" and (not self.reads.has_reads or self.unit_outcome is not None or self.candidates):
            raise ValueError("补读决定需要范围，不能声明结束或提交最终候选")
        if self.action == "finish" and (self.reads.has_reads or self.unit_outcome is None):
            raise ValueError("结束决定需要单元结果，不能携带尚未完成的补读")
        if len({candidate.key for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("影响规则候选key不能重复")
        return self


class DocumentImpactResponseProtocolSnapshot(ImpactBatchModel):
    version: Literal["impact-call-v2", "impact-call-v3"] = "impact-call-v2"
    scope: Literal["one declared unit only"] = "one declared unit only"
    response_schema: dict[str, Any]
    instruction: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self):
        raw = json.dumps(self.model_dump(mode="json", exclude={"sha256"}), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("影响响应协议快照与哈希不同")
        return self


class DocumentImpactResponseIssue(ImpactBatchModel):
    code: str
    message: str


class DocumentImpactResponseAssessment(ImpactBatchModel):
    validator_version: Literal["impact-response-validator-v1"] = VALIDATOR_VERSION
    runtime_versions: dict[str, str]
    task_id: str
    call_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["read_requested", "unit_finished", "rejected", "ineligible"]
    decision: DocumentImpactDecision | None
    issues: list[DocumentImpactResponseIssue]
    basis_limitations: list[str]
    # Position/structure validation never establishes meaning, deployment or test success.
    semantic_status: Literal["pending_review"] = "pending_review"
    deployment_status: Literal["unknown"] = "unknown"

    @model_validator(mode="after")
    def validate_status(self):
        if self.status in {"read_requested", "unit_finished"}:
            expected = "read" if self.status == "read_requested" else "finish"
            if self.decision is None or self.decision.action != expected or self.issues:
                raise ValueError("影响响应核验状态与结构决定不一致")
        elif not self.issues:
            raise ValueError("影响响应拒绝需要明确问题")
        return self


class DocumentImpactResponseCheckpoint(ImpactBatchModel):
    task_id: str
    call_id: str
    validator_version: Literal["impact-response-validator-v1"] = VALIDATOR_VERSION
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: DocumentImpactCallObject
    created_at: datetime

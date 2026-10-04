"""Model-facing v4 decisions; authoritative identities belong to the saved call."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt, StrictStr

from src.schemas.document_impact_batches import ImpactBatchModel

SourceRef = Annotated[str, Field(pattern=r"^S[1-9][0-9]*$")]
SegmentRef = Annotated[str, Field(pattern=r"^P[1-9][0-9]*$")]
CaseRef = Annotated[str, Field(pattern=r"^C[1-9][0-9]*$")]
DifferenceRef = Annotated[str, Field(pattern=r"^D[1-9][0-9]*$")]
RuleRef = Annotated[str, Field(pattern=r"^R[1-9][0-9]*$")]


class BoundRangeRead(ImpactBatchModel):
    source_ref: SourceRef
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(ge=0)


class BoundReadRequest(ImpactBatchModel):
    segment_refs: list[SegmentRef] = Field(default_factory=list, max_length=100)
    case_refs: list[CaseRef] = Field(default_factory=list, max_length=100)
    difference_refs: list[DifferenceRef] = Field(default_factory=list, max_length=100)
    ranges: list[BoundRangeRead] = Field(default_factory=list, max_length=100)


class BoundTextCitation(ImpactBatchModel):
    source_ref: SourceRef
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(ge=0)
    quote: str = Field(min_length=1)


class BoundCaseCitation(ImpactBatchModel):
    root: Literal["content_json", "execution_notes"] = "content_json"
    path: list[StrictStr | StrictInt] = Field(default_factory=list)
    value: Any = Field(...)


class BoundCaseCandidate(ImpactBatchModel):
    case_ref: CaseRef
    level: Literal["none", "minor", "major", "uncertain"]
    reason: str = Field(min_length=1)
    verification_points: list[str] = Field(default_factory=list)
    citations: list[BoundCaseCitation] = Field(min_length=1)


class BoundRuleCandidate(ImpactBatchModel):
    # Discovery proposes local keys, not authoritative requirement identities.
    key: str = Field(min_length=1)
    kind: Literal["added", "modified", "removed", "uncertain"]
    summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    document_citations: list[BoundTextCitation] = Field(min_length=1)
    case_impacts: list[BoundCaseCandidate] = Field(default_factory=list)
    applicability_suggestion: Literal["unknown", "current", "future"] = "unknown"
    uncertainties: list[str] = Field(default_factory=list)


class BoundRuleCaseAssessment(ImpactBatchModel):
    rule_ref: RuleRef
    case_impacts: list[BoundCaseCandidate] = Field(min_length=1)


class DocumentImpactBoundDecision(ImpactBatchModel):
    protocol_version: Literal["impact-response-v2"]
    action: Literal["read", "finish"]
    summary: str = Field(min_length=1)
    reads: BoundReadRequest = Field(default_factory=BoundReadRequest)
    unit_outcome: Literal["analyzed", "evidence_insufficient"] | None = None
    candidates: list[BoundRuleCandidate | BoundRuleCaseAssessment] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

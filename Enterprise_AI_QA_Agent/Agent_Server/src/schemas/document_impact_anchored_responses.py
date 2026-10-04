"""v5 model decisions select supplied text instead of calculating citations."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from src.schemas.document_impact_bound_responses import (
    BoundRuleCandidate, BoundRuleCaseAssessment, DocumentImpactBoundDecision,
)

TextEvidenceRef = Annotated[str, Field(pattern=r"^T[1-9][0-9]*$")]


class AnchoredRuleCandidate(BoundRuleCandidate):
    document_citations: list[TextEvidenceRef] = Field(min_length=1)


class DocumentImpactAnchoredDecision(DocumentImpactBoundDecision):
    protocol_version: Literal["impact-response-v3"]
    candidates: list[AnchoredRuleCandidate | BoundRuleCaseAssessment] = Field(default_factory=list)

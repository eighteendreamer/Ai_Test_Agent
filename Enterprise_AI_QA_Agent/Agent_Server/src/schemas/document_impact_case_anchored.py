"""Model decisions select delivered case fields; system owns literal citations."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from src.schemas.document_impact_anchored_responses import AnchoredRuleCandidate
from src.schemas.document_impact_bound_responses import BoundCaseCandidate, BoundRuleCaseAssessment
from src.schemas.document_impact_grouped import DocumentImpactGroupedWireDecision

CaseFieldRef = Annotated[str, Field(pattern=r"^F[1-9][0-9]*$")]


class CaseAnchoredCandidate(BoundCaseCandidate):
    citations: list[CaseFieldRef] = Field(min_length=1)


class CaseAnchoredRuleCandidate(AnchoredRuleCandidate):
    case_impacts: list[CaseAnchoredCandidate] = Field(default_factory=list)


class CaseAnchoredRuleAssessment(BoundRuleCaseAssessment):
    case_impacts: list[CaseAnchoredCandidate] = Field(min_length=1)


class DocumentImpactCaseAnchoredDecision(DocumentImpactGroupedWireDecision):
    protocol_version: Literal["impact-response-v5"]
    candidates: list[CaseAnchoredRuleCandidate | CaseAnchoredRuleAssessment] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list,
        description="Only specific missing evidence that prevents completing the CURRENT stage, naming affected "
            "rules or evidence members. Empty when current analysis is complete. Deployment unknown, pending "
            "review, intentional negative conditions, unchanged rules and separately assigned later tasks "
            "are not by themselves unresolved analysis gaps; retain those statements in summary/reasons/uncertainties.")

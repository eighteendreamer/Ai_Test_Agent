"""Versioned model wire; execution retains the original canonical declarations."""
from typing import Annotated, Literal

from pydantic import Field, model_validator
from src.schemas.case_planning import PlanningModel
from src.schemas.case_planning_stages import EvidenceItemBindingV3, EvidencePlanningDecisionV3
from src.schemas.case_effect_sources import SourceEffectCheck, SourceOperationSelector, SourceEffectRelationDeclaration

REFERENCE_PROTOCOL = "case-planning-evidence-references-v1"
REFERENCE_PROMPT_VERSION = "case-planning-evidence-references-v11"


class ReferencedDocumentExpected(PlanningModel):
    evidence_ref: str | None = Field(default=None, pattern=r"^E[1-9][0-9]*$")
    text_ref: str | None = Field(default=None, pattern=r"^T[1-9][0-9]*$")
    payload_path_tokens: list[Annotated[str, Field(max_length=256)]] | None = Field(default=None, max_length=32)
    excerpt: str | None = Field(default=None, min_length=1, max_length=8000)
    literal: str | None = Field(default=None, min_length=1, max_length=8000)
    encoding: Literal["text", "json"] | None = None

    @model_validator(mode="after")
    def one_reference(self):
        if bool(self.evidence_ref) == bool(self.text_ref):
            raise ValueError("文档预期需要且只能选择一个E或T引用")
        if self.text_ref:
            if self.literal is None or self.encoding is None or self.payload_path_tokens is not None or self.excerpt is not None:
                raise ValueError("T引用需要literal/encoding，原文及路径由系统提供")
        elif self.payload_path_tokens is None:
            raise ValueError("E文档引用需要明确payload_path_tokens")
        return self


class ReferencedEffectCheck(SourceEffectCheck):
    expected_source: ReferencedDocumentExpected | None = None


class ReferencedOperationSelector(SourceOperationSelector):
    expected_source: ReferencedDocumentExpected | None = None


class ReferencedEffectDeclaration(SourceEffectRelationDeclaration):
    checks: list[ReferencedEffectCheck] = Field(min_length=1, max_length=200)
    select_when: list[ReferencedOperationSelector] = Field(min_length=1, max_length=20)


class ReferencedItemBinding(EvidenceItemBindingV3):
    effect_declarations: list[ReferencedEffectDeclaration] | None = Field(default=None, min_length=1, max_length=200)


class ReferencedEvidenceDecision(EvidencePlanningDecisionV3):
    bindings: list[ReferencedItemBinding] = Field(default_factory=list, max_length=200)

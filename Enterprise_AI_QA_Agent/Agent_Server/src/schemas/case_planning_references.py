"""Versioned model wire; execution retains the original canonical declarations."""
from typing import Annotated, Literal

from pydantic import Field, model_validator
from src.schemas.case_planning import PlanInputBinding, PlanningModel
from src.schemas.case_planning_stages import EvidenceItemBindingV3, EvidencePlanningDecisionV3
from src.schemas.case_effect_sources import SourceEffectCheck, SourceOperationSelector, SourceEffectRelationDeclaration

REFERENCE_PROTOCOL = "case-planning-evidence-references-v1"
REFERENCE_PROMPT_VERSION = "case-planning-evidence-references-v11"
REFERENCE_PROTOCOL_V2 = "case-planning-evidence-references-v2"
REFERENCE_PROMPT_VERSION_V2 = "case-planning-evidence-references-v13"
REFERENCE_PROTOCOL_V3 = "case-planning-evidence-references-v3"
REFERENCE_PROMPT_VERSION_V3 = "case-planning-evidence-references-v14"


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


class ReferencedDocumentValueV2(PlanningModel):
    reference: str = Field(pattern=r"^E[1-9][0-9]*$")
    payload_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    excerpt: None = None
    literal: None = None
    encoding: None = None


class ReferencedDocumentTextV2(PlanningModel):
    reference: str = Field(pattern=r"^E[1-9][0-9]*$")
    payload_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    excerpt: str = Field(min_length=1, max_length=8000)
    literal: str = Field(min_length=1, max_length=8000)
    encoding: Literal["text", "json"]


class ReferencedTextExpectedV2(PlanningModel):
    reference: str = Field(pattern=r"^T[1-9][0-9]*$")
    payload_path_tokens: None = None
    excerpt: str | None = Field(default=None, min_length=1, max_length=8000)
    literal: str = Field(min_length=1, max_length=8000)
    encoding: Literal["text", "json"]


# Match the existing canonical document/text forms in the delivered JSON Schema,
# rather than imposing cross-field restrictions only after the model responds.
ReferencedDocumentExpectedV2 = ReferencedDocumentValueV2 | ReferencedDocumentTextV2 | ReferencedTextExpectedV2


class ReferencedEffectCheckV2(SourceEffectCheck):
    expected_source: ReferencedDocumentExpectedV2 | None = None


class ReferencedOperationSelectorV2(SourceOperationSelector):
    expected_source: ReferencedDocumentExpectedV2 | None = None


class ReferencedEffectDeclarationV2(SourceEffectRelationDeclaration):
    checks: list[ReferencedEffectCheckV2] = Field(min_length=1, max_length=200)
    select_when: list[ReferencedOperationSelectorV2] = Field(min_length=1, max_length=20)


class ReferencedItemBindingV2(EvidenceItemBindingV3):
    effect_declarations: list[ReferencedEffectDeclarationV2] | None = Field(default=None, min_length=1, max_length=200)


class ReferencedEvidenceDecisionV2(EvidencePlanningDecisionV3):
    bindings: list[ReferencedItemBindingV2] = Field(default_factory=list, max_length=200)


class ReferencedDocumentValueV3(PlanningModel):
    document_ref: str = Field(pattern=r"^D[1-9][0-9]*$")
    payload_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    excerpt: None = None
    literal: None = None
    encoding: None = None


class ReferencedDocumentTextV3(PlanningModel):
    document_ref: str = Field(pattern=r"^D[1-9][0-9]*$")
    payload_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    excerpt: str = Field(min_length=1, max_length=8000)
    literal: str = Field(min_length=1, max_length=8000)
    encoding: Literal["text", "json"]


class ReferencedTextExpectedV3(PlanningModel):
    text_ref: str = Field(pattern=r"^T[1-9][0-9]*$")
    payload_path_tokens: None = None
    excerpt: str | None = Field(default=None, min_length=1, max_length=8000)
    literal: str = Field(min_length=1, max_length=8000)
    encoding: Literal["text", "json"]


ReferencedDocumentExpectedV3 = ReferencedDocumentValueV3 | ReferencedDocumentTextV3 | ReferencedTextExpectedV3


class ReferencedEffectCheckV3(SourceEffectCheck):
    expected_source: ReferencedDocumentExpectedV3 | None = None


class ReferencedOperationSelectorV3(SourceOperationSelector):
    expected_source: ReferencedDocumentExpectedV3 | None = None


class ReferencedEffectDeclarationV3(SourceEffectRelationDeclaration):
    checks: list[ReferencedEffectCheckV3] = Field(min_length=1, max_length=200)
    select_when: list[ReferencedOperationSelectorV3] = Field(min_length=1, max_length=20)


class ReferencedItemBindingV3(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    evidence_refs: list[Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]] = Field(min_length=1, max_length=50)
    input_bindings: dict[str, PlanInputBinding] = Field(default_factory=dict)
    effect_evidence: None = None
    effect_declarations: list[ReferencedEffectDeclarationV3] | None = Field(default=None, min_length=1, max_length=200)


class ReferencedEvidenceDecisionV3(EvidencePlanningDecisionV3):
    bindings: list[ReferencedItemBindingV3] = Field(default_factory=list, max_length=200)

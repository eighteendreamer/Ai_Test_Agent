"""Review-only source statement mapping for document-based case generation."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.schemas.document_parsing import DocumentSourceLocation


_EXPECTED_FIELD_DESCRIPTION = (
    "Literal JSON property key in content_json.steps[...], not the non-empty string value stored under that key. "
    "Example: for {\"expected\":\"...\"}, return \"expected\"."
)


class GenerationCoverageStepSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(min_length=1, max_length=120)
    step_order: StrictInt = Field(ge=1)
    expected_field: str = Field(min_length=1, max_length=120, description=_EXPECTED_FIELD_DESCRIPTION)


class GenerationCoverageStepReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(min_length=1, max_length=120)
    step_order: StrictInt = Field(ge=1)
    expected_field: str = Field(min_length=1, max_length=120)
    expected_path: list[str | StrictInt] | None = Field(default=None, min_length=1, max_length=20)


class GenerationCoverageAssessment(BaseModel):
    """Untrusted model suggestion for one fixed source candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: Literal["in_scope", "out_of_scope", "uncertain"]
    rationale: str = Field(min_length=1, max_length=4000)
    step_refs: list[GenerationCoverageStepSelection] = Field(default_factory=list, max_length=200)


class DocumentGenerationCoverageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    statement: str = Field(min_length=1, max_length=5_000_000)
    source_block_id: str = Field(min_length=1, max_length=160)
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(ge=1)
    source: DocumentSourceLocation
    scope: Literal["local", "inherited", "unscoped"]
    section_id: str | None = None

    @model_validator(mode="after")
    def validate_span(self):
        if self.end_char - self.start_char != len(self.statement):
            raise ValueError("文档候选语句与来源范围长度不一致")
        return self


class GenerationCoverageReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_block_id: str = Field(min_length=1, max_length=160)
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(ge=1)
    source: DocumentSourceLocation
    scope: Literal["local", "inherited", "unscoped"]
    model_disposition: Literal["in_scope", "out_of_scope", "uncertain", "not_reported"]
    rationale: str | None = None
    step_refs: list[GenerationCoverageStepReference] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)


class GenerationCoverageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["document-generation-coverage-v2"] = "document-generation-coverage-v2"
    review_state: Literal["required"] = "required"
    mapping_state: Literal["complete", "incomplete", "empty_inventory"]
    parse_snapshot_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    candidate_inventory_complete: bool
    items: list[GenerationCoverageReviewItem]
    unknown_candidate_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self):
        body = self.model_dump(mode="json", exclude={"sha256"})
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("生成覆盖回执与固定哈希不同")
        return self


def fixed_generation_coverage_review(**values) -> GenerationCoverageReview:
    candidate = GenerationCoverageReview.model_construct(**values)
    body = candidate.model_dump(mode="json", exclude={"sha256"})
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return GenerationCoverageReview.model_validate({**body, "sha256": hashlib.sha256(raw).hexdigest()})

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.document_parsing import (
    DocumentParseOptions, DocumentParserCapability, DocumentSection,
    DocumentSourceLocation, DocumentTable,
)


class DocumentDifferencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_input_bytes: int = Field(default=100_000_000, ge=1)
    max_text_chars: int = Field(default=5_000_000, ge=1)
    max_lines: int = Field(default=100_000, ge=1)
    max_structure_items: int = Field(default=50_000, ge=1)
    max_changes: int = Field(default=10_000, ge=1)
    max_character_pair_product: int = Field(default=1_000_000, ge=1)
    max_character_detail_chars: int = Field(default=10_000, ge=1)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_cpu_seconds: float = Field(default=30, gt=0, le=300)
    max_memory_bytes: int = Field(default=536_870_912, gt=0)
    max_output_bytes: int = Field(default=40_000_000, gt=0)


class DocumentDifferenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    document_id: str
    version_id: str
    snapshot_id: str
    result_sha256: str
    original_sha256: str
    parser: DocumentParserCapability
    options: DocumentParseOptions
    dependency_versions: dict[str, str]


class DocumentDifferenceSource(BaseModel):
    block_id: str
    source: DocumentSourceLocation


class DocumentDifferenceRange(BaseModel):
    # Half-open Python character offsets in unmodified extracted text.
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    text: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    sources: list[DocumentDifferenceSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_char - self.start_char != len(self.text):
            raise ValueError("差异字符区间与原文长度不一致")
        if not self.text:
            if self.sources or self.start_line is not None or self.end_line is not None:
                raise ValueError("差异空侧不能伪造变化来源")
        elif self.start_line is None or self.end_line is None or self.start_line > self.end_line:
            raise ValueError("差异行区间无效")
        return self


DifferenceOperation = Literal["insert", "delete", "replace"]


class DocumentCharacterDifference(BaseModel):
    operation: DifferenceOperation
    old: DocumentDifferenceRange
    new: DocumentDifferenceRange


class DocumentTextDifference(BaseModel):
    operation: DifferenceOperation
    old: DocumentDifferenceRange
    new: DocumentDifferenceRange
    character_status: Literal["complete", "omitted"] = "complete"
    characters: list[DocumentCharacterDifference] = Field(default_factory=list)


class DocumentDifferenceView(BaseModel):
    kind: Literal["raw", "normalized"]
    normalization: Literal["none", "line-endings-v1"]
    changes: list[DocumentTextDifference] = Field(default_factory=list)


class DocumentSectionDifference(BaseModel):
    operation: DifferenceOperation
    old: list[DocumentSection]
    new: list[DocumentSection]
    old_ranges: list[DocumentDifferenceRange]
    new_ranges: list[DocumentDifferenceRange]


class DocumentTableDifference(BaseModel):
    operation: DifferenceOperation
    old: list[DocumentTable]
    new: list[DocumentTable]
    old_ranges: list[DocumentDifferenceRange]
    new_ranges: list[DocumentDifferenceRange]


class DocumentDifferenceWarning(BaseModel):
    code: str
    message: str
    side: Literal["old", "new", "both"] = "both"
    source: DocumentSourceLocation | None = None


class DocumentDifferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["document-difference-v1"] = "document-difference-v1"
    implementation_version: Literal["deterministic-v1"] = "deterministic-v1"
    old: DocumentDifferenceReference
    new: DocumentDifferenceReference
    policy: DocumentDifferencePolicy
    originals_equal: bool
    parsing_basis_changed: bool
    raw: DocumentDifferenceView
    normalized: DocumentDifferenceView
    sections: list[DocumentSectionDifference] = Field(default_factory=list)
    tables: list[DocumentTableDifference] = Field(default_factory=list)
    warnings: list[DocumentDifferenceWarning] = Field(default_factory=list)

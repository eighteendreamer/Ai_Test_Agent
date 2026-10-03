from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.case_management import TestCaseDraftCreateRequest


ImportLayout = Literal["rows", "split", "json"]
ImportColumn = Literal[
    "case_key", "title", "case_type", "priority", "preconditions", "steps",
    "expected_results", "test_data", "cleanup", "execution_notes", "content_json",
    "order", "action", "expected",
]


class CaseImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: ImportLayout = "rows"
    mode_key: str = Field(min_length=1, max_length=80)
    case_sheet: str | None = Field(default=None, max_length=160)
    step_sheet: str | None = Field(default=None, max_length=160)
    header_row: int = Field(default=1, ge=1, le=50)
    columns: dict[ImportColumn, str] = Field(default_factory=dict)
    step_columns: dict[ImportColumn, str] = Field(default_factory=dict)


class CaseImportLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(default=5_000_000, ge=1)
    max_expanded_bytes: int = Field(default=50_000_000, ge=1)
    max_zip_entries: int = Field(default=2000, ge=1)
    max_sheets: int = Field(default=50, ge=1)
    max_rows: int = Field(default=5000, ge=1)
    max_columns: int = Field(default=100, ge=1)
    max_cell_chars: int = Field(default=20000, ge=1)
    max_cases: int = Field(default=1000, ge=1)


class CaseImportSheet(BaseModel):
    name: str
    headers: list[str]
    suggested_columns: dict[str, str] = Field(default_factory=dict)


class CaseImportIssue(BaseModel):
    severity: Literal["error", "warning"] = "error"
    code: str
    message: str
    sheet: str | None = None
    row: int | None = None
    field: str | None = None


class CaseImportCandidate(BaseModel):
    row: int
    sheet: str | None = None
    step_rows: list[int] = Field(default_factory=list)
    draft: TestCaseDraftCreateRequest


class CaseImportPreview(BaseModel):
    parser_version: Literal["case-import-v1"] = "case-import-v1"
    filename: str
    file_hash: str
    options: CaseImportOptions
    sheets: list[CaseImportSheet] = Field(default_factory=list)
    candidates: list[CaseImportCandidate] = Field(default_factory=list)
    issues: list[CaseImportIssue] = Field(default_factory=list)
    # Mapping is resolved once and can later be frozen with the import batch.
    resolved_columns: dict[str, str] = Field(default_factory=dict)
    resolved_step_columns: dict[str, str] = Field(default_factory=dict)

    @property
    def valid_count(self) -> int:
        return len(self.candidates)

from __future__ import annotations

from datetime import datetime
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
    # Missing key: infer from headers. Empty value: explicitly leave unmapped.
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
    diagnostics: dict = Field(default_factory=dict)

    @property
    def valid_count(self) -> int:
        return len(self.candidates)


class CaseImportPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: CaseImportLimits = Field(default_factory=CaseImportLimits)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_cpu_seconds: float = Field(default=20, gt=0, le=300)
    max_memory_bytes: int = Field(default=268_435_456, gt=0)
    max_output_bytes: int = Field(default=20_000_000, gt=0)


class CaseImportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: CaseImportPolicy = Field(default_factory=CaseImportPolicy)
    options: CaseImportOptions | None = None


class CaseImportSettingsRecord(BaseModel):
    scope_key: str
    revision: int
    payload: CaseImportSettings


class CaseImportUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=256)
    content_base64: str = Field(min_length=1)


class CaseImportSourceRecord(BaseModel):
    id: str
    project_id: str
    filename: str
    file_hash: str
    size_bytes: int
    storage_uri: str
    security_report: dict = Field(default_factory=dict)
    sheets: list[CaseImportSheet] = Field(default_factory=list)
    created_at: datetime


class CaseImportBatchRecord(BaseModel):
    id: str
    project_id: str
    source_id: str
    preview_hash: str
    config_snapshot: CaseImportSettingsRecord
    preview: CaseImportPreview
    status: Literal["previewed", "committed"] = "previewed"
    case_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    committed_at: datetime | None = None


class CaseImportBatchView(BaseModel):
    batch: CaseImportBatchRecord
    # Live preflight against the project catalog; not part of frozen JSON.
    conflicting_case_keys: list[str] = Field(default_factory=list)

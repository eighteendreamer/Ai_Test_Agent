from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.document_parsing import (
    DocumentParseOptions, DocumentParseSnapshot, DocumentParserCapability, DocumentSection,
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
    implementation_version: str = Field(default="deterministic-v1", min_length=1, max_length=120)
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


class DocumentDifferenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: DocumentDifferencePolicy = Field(default_factory=DocumentDifferencePolicy)


class DocumentDifferenceSettingsRecord(BaseModel):
    scope_key: str
    revision: int
    payload: DocumentDifferenceSettings


UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class DocumentDifferenceTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creation_key: str = Field(min_length=1, max_length=160)
    old_snapshot_id: str = Field(pattern=UUID_PATTERN)
    new_snapshot_id: str = Field(pattern=UUID_PATTERN)


class DocumentDifferenceSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    document_id: str
    # Filtering target only: both exact sides below remain mandatory.
    version_id: str
    old_snapshot: DocumentParseSnapshot
    new_snapshot: DocumentParseSnapshot
    config: DocumentDifferenceSettingsRecord
    implementation_version: str = Field(default="deterministic-v1", min_length=1, max_length=120)
    python_version: str

    @model_validator(mode="after")
    def validate_identity(self):
        for snapshot in (self.old_snapshot, self.new_snapshot):
            if snapshot.seed.project_id != self.project_id or snapshot.seed.document_id != self.document_id:
                raise ValueError("比较输入必须属于当前项目和文档")
        if self.version_id != self.new_snapshot.seed.version_id:
            raise ValueError("比较任务筛选版本必须是新侧原件版本")
        if "unsupported" in {self.old_snapshot.status, self.new_snapshot.status}:
            raise ValueError("不支持解析的快照不能提交比较任务")
        return self


class DocumentDifferenceSummary(BaseModel):
    raw_changes: int = Field(ge=0)
    normalized_changes: int = Field(ge=0)
    section_changes: int = Field(ge=0)
    table_changes: int = Field(ge=0)
    originals_equal: bool
    parsing_basis_changed: bool


class DocumentDifferenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    task_id: str
    seed: DocumentDifferenceSeed
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    status: Literal["complete", "partial"]
    effective_policy: DocumentDifferencePolicy
    summary: DocumentDifferenceSummary
    warnings: list[DocumentDifferenceWarning]
    created_at: datetime


class DocumentDifferenceFailureArtifact(BaseModel):
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    exit_code: int | None = None


class DocumentDifferenceFailureView(BaseModel):
    task_id: str
    artifact: DocumentDifferenceFailureArtifact
    exit_code: int | None = None
    error: str
    stdout: str
    stderr: str


class DocumentDifferenceTaskRecord(BaseModel):
    id: str
    project_id: str
    document_id: str
    version_id: str
    old_snapshot_id: str
    new_snapshot_id: str
    creation_key: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["queued", "comparing", "result_saved", "finished"]
    attempt_no: int
    snapshot_id: str | None = None
    error_code: str | None = None
    failure_artifact: DocumentDifferenceFailureArtifact | None = None
    message: str
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None = None
    completed_at: datetime | None = None


class DocumentDifferenceTaskPage(BaseModel):
    items: list[DocumentDifferenceTaskRecord]
    has_more: bool
    limit: int
    offset: int


class DocumentDifferenceSnapshotPage(BaseModel):
    items: list[DocumentDifferenceSnapshot]
    has_more: bool
    limit: int
    offset: int


class DocumentDifferenceSnapshotView(BaseModel):
    snapshot: DocumentDifferenceSnapshot
    result: DocumentDifferenceResult


class DocumentDifferenceClaim(BaseModel):
    task: DocumentDifferenceTaskRecord
    seed: DocumentDifferenceSeed
    snapshot: DocumentDifferenceSnapshot | None = None
    attempt_id: str
    worker_id: str
    lease_token: str
    remaining_seconds: float


class DocumentDifferenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["document_difference"] = "document_difference"
    project_id: str
    task_id: str

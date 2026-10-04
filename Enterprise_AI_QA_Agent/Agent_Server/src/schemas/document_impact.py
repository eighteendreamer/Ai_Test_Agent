from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.api_docs import ApiDocumentAsset
from src.schemas.case_management import TestCaseBundle, TestCaseRecord, TestCaseSourceRef
from src.schemas.document_differences import DocumentDifferenceResult, DocumentDifferenceSnapshot, UUID_PATTERN
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.document_impact_analysis import DocumentImpactAnalysisContext
from src.schemas.document_impact_batches import DocumentImpactBatchSettings
from src.schemas.document_impact_calls import DocumentImpactInvocationSnapshot
from src.schemas.document_impact_responses import DocumentImpactResponseProtocolSnapshot
from src.schemas.document_impact_workflow import DocumentImpactWorkflowProtocol
from src.schemas.document_impact_reasoning import DocumentImpactReasoningSettings


class DocumentImpactPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_case_versions: int = Field(default=500, ge=1, le=10000)
    max_context_chars: int = Field(default=2_000_000, ge=1, le=50_000_000)
    timeout_seconds: float = Field(default=120, gt=0, le=600)


class DocumentImpactSettingsRecord(BaseModel):
    scope_key: str
    revision: int
    payload: DocumentImpactPolicy


class DocumentImpactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_version_ids: list[Annotated[str, Field(pattern=UUID_PATTERN)]] = Field(default_factory=list, max_length=10000)


class DocumentImpactOriginalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    version_id: str
    asset: ApiDocumentAsset
    text: str
    parse: DocumentParseSnapshotView | None = None


class DocumentImpactSourceLink(BaseModel):
    case_version_id: str
    source_index: int = Field(ge=0)
    source_ref: TestCaseSourceRef
    evidence_id: str | None = None
    status: Literal["available", "partial", "missing"]
    codes: list[str] = Field(default_factory=list)


class DocumentImpactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["document-impact-input-v1"] = "document-impact-input-v1"
    project_id: str
    document_id: str
    selection: Literal["project_current", "explicit"]
    config: DocumentImpactSettingsRecord
    difference_snapshot: DocumentDifferenceSnapshot
    difference: DocumentDifferenceResult
    old: DocumentParseSnapshotView
    new: DocumentParseSnapshotView
    cases: list[TestCaseBundle]
    originals: list[DocumentImpactOriginalEvidence]
    source_links: list[DocumentImpactSourceLink]


class DocumentImpactInputEnvelope(BaseModel):
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input: DocumentImpactInput


class DocumentImpactCaseReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: TestCaseRecord
    version_id: str = Field(pattern=UUID_PATTERN)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentImpactSourceSeed(BaseModel):
    """Submission metadata only; document and case bodies are Worker inputs."""
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    version_id: str = Field(pattern=UUID_PATTERN)
    selection: Literal["project_current", "explicit"]
    config: DocumentImpactSettingsRecord
    difference_snapshot: DocumentDifferenceSnapshot
    cases: list[DocumentImpactCaseReference]
    analysis_context: DocumentImpactAnalysisContext | None = None
    batch_settings: DocumentImpactBatchSettings | None = None
    invocation_snapshot: DocumentImpactInvocationSnapshot | None = None
    response_protocol: DocumentImpactResponseProtocolSnapshot | None = None
    workflow_protocol: DocumentImpactWorkflowProtocol | None = None
    reasoning_settings: DocumentImpactReasoningSettings | None = None
    implementation_version: Literal["impact-sources-v2"] = "impact-sources-v2"

    @model_validator(mode="after")
    def validate_fixed_scope(self):
        fixed = self.difference_snapshot.seed
        if (fixed.project_id != self.project_id or fixed.document_id != self.document_id or
            fixed.version_id != self.version_id):
            raise ValueError("影响输入必须绑定当前项目文档的固定比较")
        if any(item.case.project_id != self.project_id for item in self.cases):
            raise ValueError("影响输入用例必须属于当前项目")
        if len({item.version_id for item in self.cases}) != len(self.cases):
            raise ValueError("影响输入用例版本不能重复")
        if len(self.cases) > self.config.payload.max_case_versions:
            raise ValueError("影响输入用例版本数超过固定策略")
        if self.analysis_context and self.analysis_context.project.id != self.project_id:
            raise ValueError("影响模型依据必须属于固定项目")
        return self


class DocumentImpactTaskRequest(DocumentImpactSelection):
    creation_key: str = Field(min_length=1, max_length=160)
    difference_snapshot_id: str = Field(pattern=UUID_PATTERN)
    model_key: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^\S(?:.*\S)?$")
    environment_key: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")
    target_release: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")


class DocumentImpactInputCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=UUID_PATTERN)
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class DocumentImpactTaskRecord(BaseModel):
    id: str
    project_id: str
    document_id: str
    version_id: str
    difference_snapshot_id: str
    creation_key: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["queued", "preparing", "input_saved", "analyzing", "result_saved", "finished"]
    message: str
    attempt_no: int
    input_checkpoint: DocumentImpactInputCheckpoint | None = None
    snapshot_id: str | None = None
    error_code: str | None = None
    failure_artifact: dict | None = None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None = None
    completed_at: datetime | None = None


class DocumentImpactTaskPage(BaseModel):
    items: list[DocumentImpactTaskRecord]
    has_more: bool
    limit: int
    offset: int


class DocumentImpactCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["document_impact"] = "document_impact"
    project_id: str
    task_id: str


class DocumentImpactSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=UUID_PATTERN)
    task_id: str = Field(pattern=UUID_PATTERN)
    seed: DocumentImpactSourceSeed
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["complete", "partial"]
    unit_count: int = Field(ge=0)
    analyzed_count: int = Field(ge=0)
    rule_count: int = Field(ge=0)
    created_at: datetime


class DocumentImpactSnapshotPage(BaseModel):
    items: list[DocumentImpactSnapshot]
    has_more: bool
    limit: int
    offset: int


class DocumentImpactClaim(BaseModel):
    task: DocumentImpactTaskRecord
    seed: DocumentImpactSourceSeed
    attempt_id: str
    worker_id: str
    lease_token: str
    remaining_seconds: float
    snapshot: DocumentImpactSnapshot | None = None

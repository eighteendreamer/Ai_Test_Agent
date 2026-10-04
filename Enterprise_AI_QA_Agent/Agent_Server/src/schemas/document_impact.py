from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.api_docs import ApiDocumentAsset
from src.schemas.case_management import TestCaseBundle, TestCaseSourceRef
from src.schemas.document_differences import DocumentDifferenceResult, DocumentDifferenceSnapshot, UUID_PATTERN
from src.schemas.document_parsing import DocumentParseSnapshotView


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

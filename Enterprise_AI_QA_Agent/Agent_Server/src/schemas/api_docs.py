from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GitDocumentOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["git"] = "git"
    repository_id: str
    repository_name: str
    repository_root: str
    config_revision: int = Field(ge=1)
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    blob: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    path: str
    mode: Literal["100644", "100755"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    git_version: str
    reader_policy: dict[str, int | float]


class ApiDocumentAsset(BaseModel):
    id: str
    version_id: str | None = None
    kind: Literal["original", "derived"]
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_uri: str
    bucket: str | None = None
    object_name: str | None = None
    created_at: datetime
    reason: str
    security_report: dict[str, object] = Field(default_factory=dict)


class ApiDocRecord(BaseModel):
    id: str
    catalog_revision: int = Field(default=0, ge=0)
    current_version_id: str | None = None
    version_number: int = Field(default=0, ge=0)
    title: str
    filename: str
    project_id: str | None = None
    legacy_project_name: str | None = None
    project_name: str | None = None
    project_url: str | None = None
    source: str = "manual_upload"
    source_origin: GitDocumentOrigin | None = None
    format_label: str = "其他文档"
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    storage_uri: str
    original_asset: ApiDocumentAsset | None = None
    derived_assets: list[ApiDocumentAsset] = Field(default_factory=list)
    bucket: str | None = None
    object_name: str | None = None
    endpoint_count: int | None = None
    preview_available: bool = False
    preview_truncated: bool = False
    preview_text: str | None = None
    preview_error: str | None = None
    uploaded_at: datetime
    updated_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)


class ApiDocUploadRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=120)
    filename: str
    content_base64: str
    source: str = "manual_upload"
    title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_url: str | None = None


class ApiDocVersionUploadRequest(ApiDocUploadRequest):
    project_id: str
    request_id: str = Field(min_length=1, max_length=120)
    expected_parent_version_id: str


class ApiDocHistoryInitRequest(BaseModel):
    project_id: str
    request_id: str = Field(min_length=1, max_length=120)
    expected_catalog_revision: int = Field(ge=0)


class ApiDocVersionRecord(BaseModel):
    id: str
    document_id: str
    version: int = Field(ge=1)
    parent_version_id: str | None = None
    created_at: datetime
    snapshot: ApiDocRecord


class ApiDocVersionResult(BaseModel):
    document: ApiDocRecord
    version: ApiDocVersionRecord
    created_new: bool
    source_origin: GitDocumentOrigin | None = None


class ApiDocSubmission(BaseModel):
    document_id: str
    request_id: str
    request_hash: str
    source_request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result: ApiDocVersionResult


class ApiDocHistory(BaseModel):
    versions: dict[str, ApiDocVersionRecord] = Field(default_factory=dict)
    submissions: dict[str, ApiDocSubmission] = Field(default_factory=dict)
    assets: dict[str, ApiDocumentAsset] = Field(default_factory=dict)


class ApiDocUpdateRequest(BaseModel):
    title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_url: str | None = None


class ApiDocImportUrlRequest(BaseModel):
    url: str
    title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_url: str | None = None
    source: str = "tools_api_docs_url"


class ApiDocImportIntegrationRequest(BaseModel):
    integration_id: str
    title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_url: str | None = None
    document_url: str | None = None
    workspace_id: str | None = None
    import_source_id: str | None = None
    source: str = "tools_api_docs_integration"


class AttachmentUploadRequest(BaseModel):
    filename: str
    content_base64: str
    source: str = "chat_attachment"


class UploadedAttachmentRecord(BaseModel):
    id: str
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    storage_uri: str
    preview_text: str | None = None
    preview_truncated: bool = False
    preview_error: str | None = None
    uploaded_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)

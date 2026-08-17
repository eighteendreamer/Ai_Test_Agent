from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ApiDocRecord(BaseModel):
    id: str
    title: str
    filename: str
    project_id: str | None = None
    legacy_project_name: str | None = None
    project_name: str | None = None
    project_url: str | None = None
    source: str = "manual_upload"
    format_label: str = "其他文档"
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    storage_uri: str
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
    filename: str
    content_base64: str
    source: str = "manual_upload"
    title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_url: str | None = None


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
